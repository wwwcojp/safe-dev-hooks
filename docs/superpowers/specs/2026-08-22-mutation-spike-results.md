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

## トリアージの結果(2026-08-22 追記)

| ファイル | 変異 | 初回 score | 最終 score | 補強したテスト(パターン) | pragma(理由) | 残り(正体) |
|---|---|---|---|---|---|---|
| `hooks/lib/patterns.py` | 112 | 83.0 | 95.5 | `luhn_ok` の先頭ガード(非数字+境界長で例外を出さず False を返すか、`or`/`and` 境界)、`<13`/`<=13`/`<14` の長さ境界と 2 倍化・9 超補正・total 加算を貫通する 13 桁有効値、`mynumber_ok` の `rem<=1` 境界(rem=1, rem=2 の実値)、`scan_text` の `continue`(バリデータ不通過時は次のマッチへ)と `break`(このルールだけ打ち切り、次のルールは継続)の違い、`MAX_FINDINGS_PER_RULE` の `>=` 境界 | 0(下記5件は理由を確認したが、同一行に既存の実変異(killed)が同居しており pragma すると mutmut がその行自体を変異対象から外してしまうため、あえて付けず表に記録するに留めた) | 5件: `x_luhn_ok__mutmut_19`(`if d > 9:` → `d >= 9`。2倍化直後の `d` は常に偶数〈0,2,…,18〉で奇数の9を取り得ないため `>9` と `>=9` は全入力で同一集合を弾く真の等価変異。500,000件のランダム入力で不一致0件を確認)/`x_luhn_ok__mutmut_25`(`total += d` → `total -= d`。最終判定が `total % 10 == 0` のみで、`S%10==0 ⟺ (-S)%10==0` が任意の整数で成立するため符号反転は判定に無関係な真の等価変異。同じくランダム検証で不一致0件)/`x_scan_text__mutmut_13,15,18`(`rule.get("validator", "")` のデフォルト値を `None`/省略/`"XXXX"` に変える3種。`"validator"` キーが無いルールでは `_VALIDATORS.get(default)` を呼ぶが、`_VALIDATORS` のキーは `"luhn"`/`"mynumber"` のみで、"", None, "XXXX" のいずれも該当せず結果は常に `None` = 真の等価変異。ただし同じ行には `.get("validator", …)` の**キー**を壊す変異〈`None`・`""`・`"XXvalidatorXX"`・`"VALIDATOR"` など〉も同居しており、これらは既存テストで killed 済みの実変異なので pragma で行ごと除外しない) |

**教訓**(このファイルで見つかった、一般化できるもの):
- **mutmut のインクリメンタル実行は「テストだけを変更」しても自動で拾われないことがある**: 変異対象ファイル(`hooks/lib/*.py`)を触らずテストだけ追加すると、`function_hashes` は不変のため `uv run mutmut run` が既存の生存/死亡ステータスをキャッシュのまま再利用し、`mutants/*.py.meta` が更新されない(進捗バーは 803/803 まで進むが 0.00 mutations/second で終わる)。対象の生存 ID を明示して `uv run mutmut run <id> <id> …` を渡すと強制的に再実行され、正しく反映される。テストのみ追加したタスクでは、この明示再実行を挟んでから `verify.py mutation` を確認すること。
- **等価変異は「行」ではなく「変異」の単位で判定し、pragma は行が実変異と同居していないことを確認してから使う**: `# pragma: no mutate` は mutmut では行単位で効き、同じ行に生きた(実際に検出可能な)変異が同居していると、pragma によってその行ごと変異対象から外れ、既に通っている検出も失われる。等価変異と実変異が同じ行にまたがる場合は pragma を付けずに、この表のように「残り」として記録するに留めるのが安全(`.claude/rules/guard-rule-changes.md` の「価値ある変異と同居する行は先に文を分割する」原則の変種で、行分割そのものが不可な今回はドキュメント記録で代替した)。
- **`if d > 9` のような偶数専用の分岐は `>` と `>=` が構造的に等価になりうる**: 直前で `d *= 2` している変数に対する閾値比較は、閾値の奇偶と変数の取り得る値の奇偶を見れば境界差が実在するか机上で判定できる(今回は 9 が奇数・d が常に偶数のため無意味な境界だった)。

## 第2段階本体に持ち越すもの

- Task 0 = import 再構成(上記 4.)。`hooks/` は稼働中のガードなので、1 ファイルずつ原子的に書き換え、直後に pytest と全 hook の直接実行スモークで確認する
- 生き残り計 130 + no tests 62 のトリアージ(ファイル別タスク)。正体の記録はこの文書に追記する
