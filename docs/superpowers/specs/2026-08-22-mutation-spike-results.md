# Mutation スパイク結果(Loop Engineering 第2段階の入口)

実施日: 2026-08-22
設計書: `2026-08-22-loop-engineering-phase2-mutation-design.md`
実施場所: リポジトリの使い捨てコピー(scratch)。本体は未変更

---

## 結論: mutmut 3.7.0 を採用。import のルート起点化(`from hooks.lib import …`)が前提

news-collector のスパイク(`~/news-collector/docs/superpowers/specs/2026-08-20-mutation-spike-results.md`)と
同じ結論・同じ前提。cosmic-ray・自作ミューテータの検証は不要。

## 経過

1. **現レイアウトのまま**(`source_paths=["hooks"]`, `only_mutate=["hooks/lib/patterns.py"]`)→ 早期停止:

   > Recorded keys (e.g.): `['lib.patterns.x_luhn_ok', …]`
   > Expected keys (e.g.): `['hooks.lib.patterns.x_luhn_ok', …]`

   原因: mutmut は変異キーを**ファイルパス由来**(`hooks.lib.patterns`)で期待するが、このリポジトリは
   conftest が `hooks/` を `sys.path` に注入し、実行時モジュール名が `lib.patterns` になる。
   mutmut にプレフィックス変換の設定は無い(`src/`・`source/` の剥離のみ特別扱い)。
2. **`paths_to_mutate` に単一ファイルを指定**すると `mutants/hooks/lib/` に他の lib が
   コピーされず import で落ちる → `source_paths` はディレクトリ(`hooks`)、絞り込みは `only_mutate` で行う。
3. **`also_copy` が要る**: テストが読むリポジトリファイル(`rules/`・`examples/`・`.github/`・`docs/`・
   README・CONTRIBUTING・`.claude-plugin/`・`scripts/`)を `mutants/` に複製しないと
   `test_packaging` / `test_verify` が落ちる。
4. **import をルート起点に揃えたコピーで完走**:
   - hook スクリプト 9 本: `sys.path.insert(0, <plugin root>)` + `from hooks.lib import …`
   - テスト 9 本: `from hooks.lib import …`、conftest は `REPO_ROOT` を挿入
   - `tests/helpers.load_hook`: `importlib.import_module("hooks.<dir>.<name>")` 方式
     (`spec_from_file_location` のままだと `bash_guard.x_…` で記録され、ガード本体が対象にできない)
   - pytest 239 件 green、hook の直接実行(`uv run hooks/pre_tool_use/bash_guard.py` に stdin)exit 0

## 実測(lib 4 本 + bash_guard、803 変異、**約 11 秒**、122 mutations/sec)

| ファイル | 変異 | killed | survived | no tests | score |
|---|---|---|---|---|---|
| `hooks/lib/config.py` | 161 | 124 | 37 | 0 | 77.0 |
| `hooks/lib/hook_io.py` | 79 | 64 | 15 | 0 | 81.0 |
| `hooks/lib/patterns.py` | 112 | 93 | 19 | 0 | 83.0 |
| `hooks/lib/scanners.py` | 148 | 129 | 19 | 0 | 87.2 |
| `hooks/pre_tool_use/bash_guard.py` | 303 | 201 | 40 | 62 | 66.3 |

score = killed / 全変異(「no tests」= その関数を呼ぶテストが無い、は未検出扱い)。
mutmut の終了コード→状態: `1/3/-24`=killed、`0`=survived、`5/33`=no tests、`24/152/255`=timeout、
`34`=skipped、`35`=suspicious、`37`=caught by type check(`mutmut/__main__.py` の `status_by_exit_code`)。

生き残りのサンプル(bash_guard):

- 変数境界の regex `(?![A-Za-z0-9_])` → `(?![a-za-z0-9_])` が生き残る = **大文字に続く `$VAR` 境界がテストされていない**(本物の穴)
- `text.replace("'", "")` → `replace("XX'XX", "")` が生き残る = クォート除去の効果を直接見るテストが無い
- `rstrip("/")` → `rstrip(None)` が生き残る = 末尾スラッシュ付きパスの basename 判定が未テスト

**ガード本体の生き残りは deny 層の検出漏れに直結しうる**ため、lib と同等以上に価値がある。

## 第2段階本体に持ち越すもの

- Task 0 = import 再構成(上記 4.)。`hooks/` は稼働中のガードなので、1 ファイルずつ原子的に書き換え、直後に pytest と全 hook の直接実行スモークで確認する
- 生き残り計 130 + no tests 62 のトリアージ(ファイル別タスク)。正体の記録はこの文書に追記する
