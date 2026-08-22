# Loop Engineering 第2段階(Mutation 自動化) 設計書

作成日: 2026-08-22
前提: 第1段階(決定論的ゲート)導入済み — `scripts/verify.py` + `.loop-hooks.json` + loop-hooks プラグイン
(`2026-08-22-loop-engineering-phase1-design.md`)。スパイク結果: `2026-08-22-mutation-spike-results.md`
参照元: `~/news-collector/docs/superpowers/specs/2026-08-20-verification-roadmap-design.md` §2(同じ Python/uv 環境での前例)

---

## 1. 何を作るか、なぜ作るか

第1段階は「テストが通るまでターンを終われない」を強制した。だが**テストそのものの品質は自己申告**のままである。
「テストを書きました」が何も証明していない可能性(assert が弱い・境界を見ていない)を機械検出するのが本段階の目的。

このリポジトリは deny/ask ガードのプラグインであり、**ガード本体の検出漏れは製品の安全性そのもの**に直結する。
スパイクでは `bash_guard.py` の生き残り変異に本物の穴(大文字に続く `$VAR` 境界が未テスト等)が含まれていた。

条件は良い: lib 4 本 + bash_guard で 803 変異・約 11 秒。

## 2. 全体構成

| 構成要素 | 置き場所 | 責務 |
|---|---|---|
| mutmut 3.7 | dev 依存(`pyproject.toml` `[tool.mutmut]`) | 変異の生成・実行。`mutants/` を作業ディレクトリに使う(gitignore) |
| `verify.py mutation` ステージ | `scripts/verify.py` | mutmut を実行し、**ファイル別 score** を計算、baseline とラチェット比較、evidence に記録 |
| baseline | `.loop/mutation-baseline.json`(**Git 追跡**) | ファイル別の到達 score。下回ったら fail。書込保護はユーザー手動 |
| import 再構成 | `hooks/*.py`・`tests/*.py`・`tests/helpers.py`・`tests/conftest.py` | mutmut の要件(ルート起点の完全修飾 import)を満たす前提作業 |

**Stop ゲートには入れない**(11 秒は毎ターンには重い・対象拡大で伸びる)。運用は「テストを書いた/変えたタスクの完了条件」
として `CONTRIBUTING.md` に明記する。`verify all` = `quick` + `mutation`(コミット前・タスク完了時)。

## 3. Task 0: import のルート起点化(前提)

mutmut は変異キーを**ファイルパス由来**(`hooks.lib.patterns`)で期待する。現状は conftest の `sys.path` 注入により
実行時名が `lib.patterns` になり不一致で早期停止する(スパイク §経過 1)。

| 対象 | 変更 |
|---|---|
| `hooks/__init__.py` | 空ファイルを追加(名前空間パッケージの合流を防ぎ、`hooks` を通常パッケージにする) |
| hook スクリプト 9 本(`pre_tool_use/bash_guard.py`・`secrets_guard.py`・`exfil_guard.py`、`post_tool_use/secrets_scan.py`・`quality_gate.py`・`exfil_output_scan.py`、`notification/notify.py`、`config_change/config_guard.py`、`audit/audit_log.py`) | `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` → `.parent.parent.parent`(プラグインルート)、`from lib import …` → `from hooks.lib import …`(`# noqa: E402` は維持) |
| `hooks/lib/scanners.py` | `from . import patterns` はそのまま(パッケージ内相対 import) |
| `tests/*.py` 10 本 | `from lib import …` → `from hooks.lib import …` |
| `tests/conftest.py` | `sys.path.insert(0, str(REPO_ROOT / "hooks"))` → `sys.path.insert(0, str(REPO_ROOT))` |
| `tests/helpers.py` `load_hook(relpath)` | `importlib.import_module("hooks." + relpath[:-3].replace("/", "."))`。呼び出しごとに fresh なモジュールを返す従来の契約を保つため、import 前に `sys.modules` から同名を除去する |

**実行時の安全性**: Claude Code は `uv run "${CLAUDE_PLUGIN_ROOT}/hooks/<dir>/<name>.py"` でスクリプトを直接実行する。
`sys.path[0]` にプラグインルートを挿入するので `hooks` パッケージは常に自インストールのものが解決される
(`uv run --script` は依存ゼロの隔離環境で、site-packages に `hooks` という名前のパッケージは無い)。

**ドッグフーディング上の危険(最重要)**: `hooks/` は**このセッションで稼働中のガードそのもの**(plugin source = このディレクトリ)。
フックがクラッシュすると exit 1 = 非ブロックで**ガードが黙って外れる**側に倒れる。よって:

- 書込保護のため Edit/Write は使えない。Bash 経由の python スクリプト書込(`.claude/rules/dogfooding.md` 項目 1)で
  **1 ファイルずつ原子的に**(読み→置換→書き戻し)行う
- 各ファイル直後に `uv run pytest -q`、全件終了後に**全 hook スクリプトの直接実行スモーク**
  (各スクリプトに無害な JSON を stdin で渡し exit 0・traceback 無しを確認)
- Bash 書込は loop-hooks のゲート外なので、最後に `uv run python scripts/verify.py quick` を明示実行する
- ruff の isort が `from hooks.lib import` を first-party として並べ替えを要求する場合は手で揃える(`ruff --fix` は保護対象に書けない)

## 4. mutmut 設定

`pyproject.toml`:

```toml
[dependency-groups]
dev = ["pytest>=8", "ruff>=0.8", "mutmut>=3.7"]

[tool.mutmut]
source_paths = ["hooks"]
only_mutate = [
  "hooks/lib/patterns.py", "hooks/lib/hook_io.py", "hooks/lib/scanners.py", "hooks/lib/config.py",
  "hooks/pre_tool_use/bash_guard.py",
]
also_copy = [
  "rules", "examples", ".claude-plugin", "scripts", ".github", "docs",
  "README.md", "README.ja.md", "CONTRIBUTING.md", ".claude-hooks.json", ".loop-hooks.json", ".gitignore",
]
```

- `source_paths` はディレクトリ(`hooks`)。単一ファイル指定だと他の lib がコピーされず import で落ちる(スパイク §経過 2)
- `also_copy` = テストが `REPO_ROOT` 基準で読むファイル(スパイクで確定)。テストが新たにリポジトリファイルを読むようになったら足す
- `mutants/` を `.gitignore` に追加。`uv run ruff check hooks tests scripts`・pytest `testpaths=["tests"]` はいずれも `mutants/` を見ない

## 5. `verify.py mutation` ステージ

### 5.1 フロー

1. `uv run mutmut run` を実行(`capture_output`)。非ゼロ終了 → evidence に `{"name":"mutmut","ok":false}` で `pass:false`、出力末尾を stderr に出して終了
2. `mutants/**/*.py.meta` の `exit_code_by_key` から**ファイル別**に集計:
   - killed = 終了コード `1`・`3`・`-24`(mutmut の `status_by_exit_code` で "killed")
   - 全変異 = そのファイルの全キー(survived `0`・no tests `5/33`・timeout・suspicious 等はすべて未検出扱い)
   - score = `round(killed / total * 100, 1)`。ファイルパスは `mutants/` からの相対(`hooks/lib/patterns.py`)
3. baseline と比較(5.2)
4. evidence に `stage:"mutation"`、`checks:[{"name":"mutmut","ok":true,"ms":…},{"name":"baseline","ok":…,"scores":{file: {"score","killed","total"}}}]` を追記

### 5.2 baseline `.loop/mutation-baseline.json`

```json
{"files": {"hooks/lib/patterns.py": 98.2, "hooks/lib/hook_io.py": 100.0}, "updated": "2026-08-22T12:34:56Z"}
```

ファイル別ラチェット:

| 状況 | 判定 |
|---|---|
| 結果 score < baseline score のファイルがある | **fail**。下回った全ファイルを `file: score < baseline` の形で列挙し、`uv run mutmut results` / `uv run mutmut show <id>` で生き残りを確認しテストを補強する旨を stderr に出す |
| baseline にあるが結果に無いファイル(`only_mutate` から外された) | **fail**。「対象の縮小は baseline から手で外す(書込保護・ユーザー作業)」と出す。**対象を黙って狭めてゲートを通す経路を塞ぐ** |
| 結果にあるが baseline に無いファイル(新規対象) | 登録して pass |
| 全ファイルが同点以上 | pass。上回ったファイルの値を更新し `updated` を書き換える |
| baseline ファイルが無い(初回) | 全ファイルを登録して pass |

baseline の書込は verify.py 自身が行う(インタプリタ経由なので `write_protected` の予防層に掛からない。
エージェントが Edit/Bash で直接触る経路だけを塞ぐ — news-collector と同じ構図)。

### 5.3 ステージ体系

| ステージ | 中身 | 想定実行者 |
|---|---|---|
| `quick` | leak → ruff → pytest(第1段階のまま) | Stop フック・コミット前 |
| `mutation` | 5.1 | **テストを書いた/変えたタスクの完了条件**、`all` の一部 |
| `all` | `quick` が通ったら `mutation` | コミット前・タスク完了時 |

`main()` は `quick`/`mutation`/`all` と未知ステージ(非ゼロ終了)を扱う。`STAGES` 辞書(決定論的チェック列)と
`mutation`(専用関数)は別扱い — `test_quick_stage_mirrors_ci` の双方向一致は `quick` のみが対象。

### 5.4 ハーネス自身のテスト(`tests/test_verify.py` に追加)

mutmut は実行しない。`run_mutation(repo_root, runner=...)` のように mutmut 呼び出しを注入可能にし、
`tmp_path` に偽の `mutants/<path>.py.meta` を置いて検証する:

| 観点 | 中身 |
|---|---|
| score 集計 | 終了コード `{1,3,-24}` を killed、`0/33/36` を未検出として数える。ファイル別に分かれる |
| 初回 | baseline 無し → 全ファイル登録・True・evidence `pass:true` |
| 低下 | 1 ファイルだけ下回る → False、stderr に `file: score < baseline`、baseline は**更新されない** |
| 向上 | 上回ったファイルだけ更新、他は据え置き |
| 欠落 | baseline にあるファイルが結果に無い → False、stderr にその旨 |
| mutmut 失敗 | runner が非ゼロ → False、evidence に `mutmut ok:false`、baseline 不変 |
| `all` | `run_stage("quick")` が False なら mutation を呼ばない(monkeypatch で確認) |

mutation の受け入れ条件(判定を壊してテストが落ちることを確認)はここにも適用する。

## 6. Git・保護

- `.gitignore`: `.loop/` → `.loop/*` + `!.loop/mutation-baseline.json`(**baseline は Git 追跡**: ラチェットが clone を跨いで効き、
  score の変化が PR の diff でレビューできる。第1段階 §4.1 の予告どおり)。`mutants/` を追加。evidence・state は ignore のまま
- **ユーザー手動(書込保護)**: `.claude-hooks.json` の `secrets_guard.write_protected_paths` に `"*.loop/mutation-baseline.json"` を追加。
  現在の配列 `[".loop-hooks.json", "*.loop/state.json"]` に足す(第1段階の手動作業がまだなら、まとめて 3 要素にする)
- 予防層であり絶対ではない点は第1段階 §6 のとおり。最終判定者は CI と人間のブランチレビュー(baseline の diff も含む)

## 7. トリアージ(ファイルごとに 1 タスク)

順序: `patterns` → `hook_io` → `scanners` → `config` → `bash_guard`(小さく純粋なものから。最後に最大・最重要)。
各タスクの進め方(news-collector の教訓を踏襲):

1. `uv run python scripts/verify.py mutation` で現状 score を baseline に登録(初回)
2. `uv run mutmut results` で生き残りを列挙し、`uv run mutmut show <id>` を**全件**読んで変更行を並べる。正体は数パターンに収まる
3. パターンごとに fixture・**厳密な期待値**のテストを追加する(「等価比較」「`in` の部分一致」「SystemExit が出ることだけ」は穴になりやすい)
4. 真の等価変異(エンコーディング名の大小・`rstrip(None)` が同値な入力しか来ない等)のみ `# pragma: no mutate` を**行単位**で付け、**理由コメント必須**。
   価値ある変異と同居する行は文を分割してプラグマの範囲を最小にする
5. `bash_guard` の「no tests」62 件は、その関数を呼ぶテストが無い箇所。`main()`・I/O ラッパーは `load_hook` + stdin 注入で覆う
6. 到達点は各ファイル **100 に近づける**。届かない残りは正体(等価変異か、テスト困難か)を `2026-08-22-mutation-spike-results.md` に追記し、その値で baseline を確定

**トリアージで見つかったガードの本物の穴はテスト追加で塞ぐ(ルール変更ではない)**。ガードの挙動変更が必要と判明した場合は
`.claude/rules/guard-rule-changes.md` に従い別途扱う(deny を弱める変更はユーザー確認必須)。

## 8. CI・ドキュメント

- CI は変更しない(mutation はタスク完了ゲート。`quick` と CI の一致原則もそのまま)
- `CONTRIBUTING.md`: 検証ゲート節に「テストを書いた/変えたタスクの完了条件: `uv run python scripts/verify.py mutation`(baseline を下回れない)」
  「コミット前: `verify.py all`」「等価変異は `# pragma: no mutate` + 理由」を追記。PR 前チェックリストにも 1 行
- `.claude/rules/dogfooding.md` 項目 3: baseline も書込保護対象であること、対象の縮小は baseline を手で外す必要があることを追記
- 第1段階 spec §11 の「第2段階」は本 spec を指すよう 1 行更新(矛盾する記述を残さない)

## 9. 作らないもの

- CI からの mutation 実行、Stop ゲートへの組込み
- secrets_guard / exfil_guard / post_tool_use 等への対象拡大(同じ手順の後続タスク。`only_mutate` に足してトリアージ)
- 第3段階(PBT)・第4段階(アーキテクチャテスト)
- mutmut の設定のカスタム(`max_stack_depth`・`pytest_add_cli_args` 等)。必要になってから

## 10. リスクと未確定事項

| 項目 | 内容 | 扱い |
|---|---|---|
| Task 0 で稼働中ガードを壊す | hook がクラッシュすると非ブロック=ガード素通り | §3 の原子的書換+全 hook 直接実行スモーク。作業は 1 セッション内で完結させ、中断しない |
| 「no tests」の扱い | 未検出扱いにすると bash_guard の初期 score が 66 と低い | 正直な値として受け入れ、トリアージで上げる。baseline は低い値から始めても**下げない**ことが契約 |
| 所要時間の増加 | 対象拡大で 11 秒 → 数十秒 | mutmut は関数ハッシュで増分実行する。Stop ゲートには入れない設計なので許容 |
| `also_copy` の漏れ | テストが新たにリポジトリファイルを読むと mutants 内で落ちる | mutmut の失敗出力がそのまま出る。`also_copy` に足す |
| baseline の Git 追跡と書込保護の両立 | verify.py の自動更新で diff が出る | 意図した挙動(PR でレビュー)。エージェントが直接編集する経路だけ塞ぐ |
| 等価変異の判定ミス | 本物の穴を pragma で隠す | pragma には理由コメント必須。レビューで pragma 行を重点確認 |
| verify.py 自身の改変 | mutation は CI で回らないため、`MUTATION_KILLED_CODES` やラチェット判定を書き換えれば全ファイル 100 にできる | 予防層なし。ブランチレビュー(人間)が唯一の検出点。`scripts/verify.py`・`tests/test_verify.py` の diff を PR で重点確認する |
