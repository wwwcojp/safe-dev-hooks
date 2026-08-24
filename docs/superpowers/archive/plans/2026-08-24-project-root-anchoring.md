# 相対パスの基準をプロジェクトルートにする(0.7.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** プロジェクト設定 `.claude-hooks.json` の探索先、監査ログの出力先、および同種の「プロジェクト直下を探す」処理すべての基準を、`event["cwd"]`(Claude が `cd` した一時的な作業ディレクトリ)ではなくプロジェクトルートにする。現状は作業ディレクトリ次第でプロジェクト層が適用されず、`secrets_guard.write_protected_paths` などの保護が**通知すら出さずに**外れる。

**Architecture:** 基準解決を `hooks/lib/config.py` の 1 関数 `project_root(cwd)` に閉じ込め、**基準を必要とする全箇所がそれを使う**。呼び出し側 9 箇所の `config.load_config(event.get("cwd"))` は変更しない — call site に判断を置くと 1 箇所の直し漏れが無言のセキュリティ低下になるため、基準の決定はライブラリ側に一本化する。あわせて「見つけたのに読まなかったプロジェクト設定」を通知する(0.7.0 の「採用しなかったら必ず通知する」原則の穴を塞ぐ)。

**Tech Stack:** Python 3.10+ stdlib(os / json / pathlib)、uv、pytest、ruff、mutmut(`scripts/verify.py mutation`)

**Spec:** なし(バグ修正。根拠は下記「調査の根拠」)

---

## 調査の根拠

指示を鵜呑みにせず、着手前に再現を自分の目で確認すること。**以下はすべて 2026-08-24 に実測で再現済み。**

### 症状 1: プロジェクト設定が無言で外れる(重大)

```python
import sys; sys.path.insert(0, "<repo>")
from hooks.lib import config
a = config.load_config("<project-root>")
b = config.load_config("<project-root>/scripts")   # サブディレクトリ
a["secrets_guard"] == b["secrets_guard"]           # → False
```

実測(`.claude-hooks.json` を持つプロジェクトで確認済み):

```
ルート: write_protected_paths=[3件]  custom_patterns=1  _notices=[]
サブ  : write_protected_paths=[]     custom_patterns=0  _notices=[]   ← 通知も出ない
```

原因は `hooks/lib/config.py` の `_load_config`:

```python
project_path = Path(cwd or ".") / PROJECT_CONFIG_NAME   # ← cwd 基準。上位探索なし
```

`cwd` は Bash ツールの `cd` に追従する。Claude がサブディレクトリへ移動した状態で発火したフックは、プロジェクト設定を見つけられずグローバル層+既定へ縮退する。**しかも `_notices` が空** — 0.7.0 で「採用しなかった設定は必ず通知する」を原則にしたのに、この経路だけ抜けている。

### 症状 2: 監査ログが作業ディレクトリに散らばる

`hooks/audit/audit_log.py:26`:

```python
log_dir = Path(event.get("cwd") or ".") / log_dir   # ← 同じ原因
```

証拠(ログ自身が原因を記録していた): `<repo>/rules/.claude/logs/audit-20260718.jsonl` の中身に、それを作った当の
コマンド `cd <repo>/rules && ...` が写っている。他に `mutants/rules/`、別リポジトリの `spike/out/`・
`docs/superpowers/specs/` にも散在。**確認した 4 箇所はすべて gitignore 済みでコミット漏れのリスクは無かった**が、
`.claude/logs` を無視していないリポジトリでは混入し得る。

### 症状 3(プラン初版のスコープ漏れ): 同じ原因のバグがあと 3 箇所

いずれも実測で確認済み。ルート基準なら成立し、サブディレクトリ基準だと**無言で**失敗する。

| 箇所 | 症状 | 影響 |
|---|---|---|
| `hooks/lib/scanners.py:22-30` `_resolve_config_path` | `.gitleaks.toml` が見つからない | 検出バックエンドの設定が無言で外れる |
| `hooks/config_change/config_guard.py:37-39` `_disable_all_hooks_active` | `.claude/settings.json` を見つけられない | **`disableAllHooks` 有効の警告が出ない**(全 Hooks 無効化の唯一の可視化) |
| `hooks/post_tool_use/quality_gate.py:42` `resolve_commands` | マーカーファイル(`pyproject.toml` 等)未検出 | 品質検査が無言でスキップ |

```
scanners     : ルート → 設定を発見 / サブ → 発見できず
quality_gate : ルート → ['ruff check a.py'] / サブ → []
config_guard : ルート → True(警告あり) / サブ → False(警告なし)
```

### 触ってはいけない箇所(意図的に cwd 基準のまま)

`hooks/pre_tool_use/secrets_guard.py:128` `_normalize_target` は **cwd 基準のままにする**。これは「利用者が
書こうとしている場所」を利用者の指定どおりに解釈する処理であり、「同じプロジェクトか」を問う基準解決とは
別問題。`docs/security-model.md` 項目 11 に明記済みの区別を崩さないこと。

### `CLAUDE_PROJECT_DIR` の可用性

- Bash ツールの環境には**来ない**(実測: `env | grep CLAUDE` に無い)。
- フックの環境には**来る**(プラン初版の作者が `audit_log.py` へ一時計測を入れて観測。計測後 `git checkout` で復元済み)。
- 傍証: `.claude/settings.json` のフック定義で `${CLAUDE_PLUGIN_ROOT}` が実際に機能している(loop-hooks の Stop ゲートが動作中)。フック環境に追加の環境変数が注入されるのは確実。
- 公式ドキュメント(Reference scripts by path)も「セッションが開始したプロジェクトルート」と定義。

**ただし**: この可用性はプラン外の前提であり、**テストでは検証できない**(テストは自分で環境変数を立てるため)。
Task 1 の最後に実地確認を必ず行うこと(下記)。

### 既存の承認は壊れない(但し書きあり)

`trust.project_key(cwd)` は `os.path.realpath(cwd or ".")`。従来キーが `trusted_projects` に記録されるのは
「設定が見つかった = ルートにいた」場合なので、修正後の基準と一致する。**通常は再承認不要。**
但し書き: モノレポでサブディレクトリの設定を承認していた場合、そのエントリは使われなくなる(ルートの承認が要る)。
Task 1 でキー不変をテストとして固定すること。

### 意図的な挙動変更(1 件)

モノレポでサブパッケージが独自の `.claude-hooks.json` を持つ場合、従来は「そこに `cd` していれば」読まれたが、
今後はプロジェクトルートの設定だけが読まれる。セッション単位で内容ハッシュを承認する trust モデルとしては
こちらが正しい。**ただし無言で落とさず通知する**(Task 2)。CHANGELOG に明記すること。

---

## 設計判断(実装者はここを再検討しないこと。異論があればユーザーに上げる)

### D1: 基準の決定順序 — 環境変数 → git ルート → cwd

```
CLAUDE_PROJECT_DIR(あれば無条件に採用)
  → cwd の最近傍の祖先で `.git` が存在するディレクトリ
    → cwd(従来どおり)
```

初版は環境変数のみのフォールバック(`os.environ.get(...) or cwd`)だったが、それでは
**ユーザーがサブディレクトリで `claude` を起動した場合**(この場合アンカー自体がサブディレクトリになる)と、
Claude Code 以外のハーネスから叩かれた場合にバグが残る。git ルートを二次フォールバックに置くとこの穴が塞がる。

- **なぜ「上方への `.claude-hooks.json` 探索」ではなく git ルートか**: 上方探索は cwd の最近傍の設定を拾うため、
  モノレポで悪意あるサブパッケージの設定に `cd` した場合にそれを拾ってしまい、本修正の目的と逆行する。
  git ルートなら中間のサブパッケージ設定を拾わず、決定的。
- **安全性**: フォールバックで解決したルートも `trust.gate` の承認対象(キーはその realpath)。承認なしに
  採用されることはない。むしろ cwd より既存の承認エントリ(= リポジトリルート)と一致しやすい。
- **worktree**: `.git` は**ファイル**になる(実測確認済み)。`is_dir()` でなく `exists()` で判定すること。
- **cwd が None のときは git 探索を行わない**(`project_root(None)` は `None` を返す)。既存の
  `Path(cwd or ".")` / `project_key(cwd or ".")` の意味を変えないため。mutmut は `mutants/` を cwd にして
  テストを走らせるので、ここを "." で探索し始めるとリポジトリルートを掴んで挙動が変わる。

### D2: 読まなかったプロジェクト設定は通知する

`cwd` 側に `.claude-hooks.json` が存在するのに、基準が別ディレクトリだったために読まなかった場合、
通知を出す。0.7.0 の「無視したら必ず通知する」原則をこの経路にも適用する。無通知だと、D3 の攻撃も
モノレポの挙動変更も利用者から見えない。

- 流量: 同じパスについてはクールダウン(`notice_cooldown_sec`、既定 3600 秒)。`trust` の状態ファイルの
  仕組みを再利用し、新しいセクション `skipped_last` を使う。状態ファイルが使えない場合は毎回通知
  (既存の `_untrusted` と同じ「可視性優先」)。
- 文面と状態管理は `hooks/lib/trust.py` に置く(利用者向け通知文と状態ファイルの扱いを 1 モジュールに集約する
  既存の設計を維持)。

### D3: 環境変数をアンカーにすることのセキュリティ影響

アンカーがハーネス供給の環境変数になるため、リポジトリ同梱の `.claude/settings.json` の `env` で
影響され得る。`trust.gate` は解決後パスをキーに承認を要求するので**自己承認はできない**が、
**プロジェクト層を黙って落とすことはできる**。D2 の通知でこれが可視化される。
`docs/security-model.md` に 1 項目として記述すること。

### D4: `quality_gate` の実行ディレクトリも揃える

`run_checks(commands, cwd=cwd)` の実行ディレクトリもプロジェクトルートにする。ツールの設定
(`pyproject.toml`・`ruff.toml` 等)はルートにあるため、マーカー検出だけルート基準にして実行を
サブディレクトリのままにすると不整合になる。挙動変更なので CHANGELOG に記載。

### D5: バージョンは 0.7.1

意図的な挙動変更(モノレポのサブパッケージ設定)を含むが、**従来の挙動は「作業ディレクトリ次第で変わる」
= 安定した契約が存在しなかった**ため、破壊的変更ではなく不整合の修正として扱う。厳密な semver シグナルを
優先するなら 0.8.0 だが、本プランは 0.7.1 を採る。

---

## Global Constraints

- 実行時依存ゼロ(stdlib のみ)。`requires-python = ">=3.10"`
- **`hooks/` は自インストールの `write_protected_paths` により Edit/Write 不可** → Bash 経由の python 書込(読み → 置換 → 書き戻し)。稼働中のガードを書き換えるので、書換直後に `uv run pytest` と対象フックの直接実行スモークを行う
- **前提の変化(2026-08-24 に判明)**: loop-hooks プラグインが更新され、(a) 検証ゲートは `PostToolUse(Edit|Write)` の dirty 記録ではなく **`watch` に一致する未コミット変更の内容ハッシュ**で発火するようになった(= **Bash 経由の書込もゲート対象**。`.claude/rules/dogfooding.md:13` の「Bash 書込はゲート外」という記述は古い)、(b) 状態がリポジトリ外(`$CLAUDE_PLUGIN_DATA/state/` または `~/.cache/loop-hooks/state/`)へ移り `.loop/state.json` は使われなくなった、(c) フック定義のパスが変わった。**セッションを再起動するまで古い登録が残りゲートが機能しない**ので、実装中は各タスクで `uv run python scripts/verify.py quick` を明示実行して自衛すること。`.loop/evidence.jsonl` と `.loop/mutation-baseline.json` は `scripts/verify.py` 側の資産で影響を受けない
- **`hooks/lib/config.py` の mutation baseline は 100.0、`trust.py` は 97.3**(`.loop/mutation-baseline.json`)。新規分岐は**すべて変異テストで殺せる**形にする。等価変異を pragma で消さず、到達不能な既定値つき `.get` を書かないこと。各タスク完了時に `uv run python scripts/verify.py mutation` が exit 0
- 「呼び出し側から観測できない」を等価変異の理由にしないこと(0.7.0 のブランチレビューの教訓。`docs/superpowers/specs/2026-08-22-mutation-spike-results.md` 参照)。防御的な契約は内部関数を直接呼ぶ白箱テストで固定する
- `hooks/audit/audit_log.py`・`hooks/config_change/config_guard.py`・`hooks/post_tool_use/quality_gate.py` は `only_mutate` 対象外(通常のテストのみ)
- 実ホームパスをリポジトリに書かない(テストは `tmp_path` / `/home/alice`)
- テストは実 `$HOME` に書かない。**`CLAUDE_PROJECT_DIR` は conftest の autouse fixture で既定クリア**する — さもないと、loop-hooks の Stop ゲート経由で pytest が走ったとき(= フック環境)に環境変数が既存 449 テストへ漏れる
- `load_config` は例外を送出しない契約を維持する。`project_root` も例外を投げない(祖先ディレクトリの `exists()` が `OSError` を投げ得ることに注意)
- 作業ブランチ: `fix/project-root-anchoring`(main から作成)。**このプランを最初のコミットとしてブランチに載せる**(現在 main 上で untracked)

## File Structure

| ファイル | 責務 |
|---|---|
| Modify `hooks/lib/config.py` | `import os`、`project_root(cwd)` を追加。`_load_config` のプロジェクト層探索と `trust.gate` のキーをその戻り値基準に。読まなかった設定の通知を `_notices` へ |
| Modify `hooks/lib/trust.py` | 「読まなかったプロジェクト設定」の通知文と、状態ファイルのクールダウン(`skipped_last`) |
| Modify `hooks/lib/scanners.py` | `.gitleaks.toml` 探索の基準を `config.project_root` に |
| Modify `hooks/config_change/config_guard.py` | プロジェクト `settings.json` 探索の基準を `config.project_root` に |
| Modify `hooks/post_tool_use/quality_gate.py` | マーカー検出と実行ディレクトリの基準を `config.project_root` に |
| Modify `hooks/audit/audit_log.py` | 相対 `log_dir` の基準を `config.project_root` に |
| Modify `tests/conftest.py` | autouse: `CLAUDE_PROJECT_DIR` を削除 |
| Modify `tests/test_config.py`・`tests/test_trust.py`・`tests/test_scanners.py`・`tests/test_config_guard.py`・`tests/test_quality_gate.py`・`tests/test_audit_and_notify.py` | 各基準の分岐、サブディレクトリ起点の回帰、trust キー不変 |
| Modify `docs/hooks/audit_log.md`・`docs/configuration.md`・`docs/security-model.md` | 基準がプロジェクトルートであること、D3 の影響 |
| Modify `CHANGELOG.md`・`pyproject.toml`・`.claude-plugin/plugin.json` | 0.7.1 と挙動変更 |

---

### Task 1: `config.project_root` と設定探索の基準差し替え

**Files:**
- Modify: `hooks/lib/config.py`(Bash 経由の python 書込 — write_protected)
- Modify: `tests/conftest.py`
- Modify: `tests/test_config.py`

**先にテストを書き、失敗を目視してから実装すること(superpowers:test-driven-development)。**

**Interfaces(以降のタスクが使う):**

```python
PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"


def project_root(cwd: str | None = None) -> str | None:
    """相対パスとプロジェクト設定の基準を返す。

    event["cwd"] は Bash の cd に追従する一時的な作業ディレクトリなので基準にできない。
    Claude Code がフックに渡すセッション開始時のプロジェクトルート CLAUDE_PROJECT_DIR を
    優先し、無ければ cwd の最近傍の git ルート、それも無ければ cwd に戻す(D1)。
    例外は投げない。cwd が None のときは git 探索を行わず None を返す。
    """
```

- `or` で数珠つなぎにせず `if` で分岐すること(空文字を未設定として扱う分岐を変異テストで殺せる形にする)
- git ルート探索は `Path(cwd)` とその `parents` を順に見て `(d / ".git").exists()` の最初の一致を返す。
  **`is_dir()` ではなく `exists()`**(worktree では `.git` がファイル)。`OSError` は握りつぶして探索を打ち切る

**Steps:**
- [ ] `tests/conftest.py` に autouse fixture を追加し、`monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)` を行う
- [ ] `tests/test_config.py` に失敗するテストを追加する:
  - [ ] `CLAUDE_PROJECT_DIR` が設定されていれば、`cwd` が別のディレクトリでもそこの `.claude-hooks.json` が読まれる(承認済みの状態で)
  - [ ] `CLAUDE_PROJECT_DIR` が空文字なら未設定と同じ扱い
  - [ ] `CLAUDE_PROJECT_DIR` 未設定 + cwd がサブディレクトリ + 祖先に `.git` → **git ルート**の設定が読まれる
  - [ ] `.git` が**ファイル**(worktree)でも git ルートとして認識される
  - [ ] `CLAUDE_PROJECT_DIR` 未設定 + `.git` も無い → 従来どおり cwd 基準
  - [ ] `project_root(None)` は `None`(git 探索をしない)
  - [ ] 祖先の `exists()` が `OSError` を投げても例外を外に出さない
  - [ ] **回帰**: プロジェクトルートに承認済みの設定があるとき、`cwd` をサブディレクトリにしても `secrets_guard.write_protected_paths` がルートと一致する
  - [ ] **trust キー不変**: 基準がどう解決されても `trusted_projects` のキーは解決後ルートの realpath になり、既存の承認エントリで採用されること
- [ ] テストが期待どおり失敗することを確認する
- [ ] `hooks/lib/config.py` に `import os`・`PROJECT_DIR_ENV`・`project_root` を追加し、`_load_config` で `root = project_root(cwd)` を求めて `project_path` と `trust.gate` の第 2 引数に使う
- [ ] `uv run pytest -q` が全件通ることを確認(既存 449 件が壊れていないこと)
- [ ] `uv run python scripts/verify.py quick` が exit 0
- [ ] `uv run python scripts/verify.py mutation` が exit 0(`hooks/lib/config.py` が 100.0 を下回らない)
- [ ] **実地確認(必須・ここで行う)**: テストは自分で環境変数を立てるため、実際のフック環境に
      `CLAUDE_PROJECT_DIR` が来ているかを検証できない。稼働中のフックを使って次を確認する:
  - [ ] サブディレクトリを `cwd` にした合成イベントを `hooks/pre_tool_use/secrets_guard.py` に流し、
        **プロジェクト設定由来の** `write_protected_paths`(例: `*/.loop/state.json`)で `deny` になること
  - [ ] 同じイベントで `CLAUDE_PROJECT_DIR` を環境から外すと、git ルート経由で同じ結果になること
  - [ ] 結果を報告に含める。ここで期待どおりにならなければ **D1 の前提が崩れている** — 実装を続けず報告すること

### Task 2: 読まなかったプロジェクト設定の通知(D2)

**Files:**
- Modify: `hooks/lib/trust.py`(Bash 経由の python 書込)
- Modify: `hooks/lib/config.py`(Bash 経由の python 書込)
- Modify: `tests/test_trust.py`・`tests/test_config.py`

**Steps:**
- [ ] 失敗するテストを先に書く:
  - [ ] `cwd` に `.claude-hooks.json` があり、基準が別ディレクトリのとき `_notices` に通知が 1 件出る
  - [ ] 同じパスについて 2 回目はクールダウンで抑止される。`notice_cooldown_sec: 0` なら毎回出る
  - [ ] 状態ファイルが使えない(ディレクトリ・書込不可)ときは毎回通知する
  - [ ] `cwd` と基準が同じなら通知は出ない。`cwd` 側にファイルが無ければ通知は出ない
  - [ ] 通知文面の全文一致(`hooks/lib/trust.py` の他の通知と同じ厳密さで)
  - [ ] `audit_log` はこの通知も出さない(既存の `quiet_notices=True` で自動的にそうなることの確認)
- [ ] `hooks/lib/trust.py` に通知文と `skipped_last` セクションを扱う関数を追加する
- [ ] `hooks/lib/config.py` の `_load_config` から呼び、`_notices` に載せる
- [ ] `uv run pytest -q`・`uv run python scripts/verify.py quick` が exit 0
- [ ] `uv run python scripts/verify.py mutation` が exit 0(`config.py` 100.0、`trust.py` 97.3 を下回らない)

### Task 3: 残り 3 箇所の基準差し替え(症状 3)

**Files:**
- Modify: `hooks/lib/scanners.py`・`hooks/config_change/config_guard.py`・`hooks/post_tool_use/quality_gate.py`(いずれも Bash 経由の python 書込)
- Modify: `tests/test_scanners.py`・`tests/test_config_guard.py`・`tests/test_quality_gate.py`

`scanners.py` が `config` を import しても循環しない(`config → trust` のみ、`config → scanners` は無い)。
確認してから進めること。

**Steps:**
- [ ] 失敗するテストを先に書く(各箇所について「ルート基準なら成立/サブディレクトリ起点でも成立」の 2 方向):
  - [ ] `scanners._resolve_config_path`: cwd がサブディレクトリでもルートの `.gitleaks.toml` を使う。`gitleaks_config` の明示指定は従来どおり優先
  - [ ] `config_guard._disable_all_hooks_active`: cwd がサブディレクトリでもルートの `.claude/settings.json` の `disableAllHooks` を検知する
  - [ ] `quality_gate.resolve_commands`: cwd がサブディレクトリでもルートのマーカーファイルで auto-detect が働く
  - [ ] `quality_gate.run_checks`: 実行ディレクトリがプロジェクトルートになる(D4)
- [ ] 3 ファイルを `config.project_root(...)` 基準に変更する
- [ ] 各フックを直接実行してスモークする(`echo '{}' | uv run <hook>` が exit 0)
- [ ] `uv run pytest -q`・`uv run python scripts/verify.py quick` が exit 0
- [ ] `uv run python scripts/verify.py mutation` が exit 0(`scanners.py` 99.3 を下回らない)

### Task 4: 監査ログの出力先(症状 2)

**Files:**
- Modify: `hooks/audit/audit_log.py`(Bash 経由の python 書込)
- Modify: `tests/test_audit_and_notify.py`

**Steps:**
- [ ] 失敗するテストを先に書く:
  - [ ] `CLAUDE_PROJECT_DIR` を tmp に設定し `event["cwd"]` をその**サブディレクトリ**にしたとき、`audit-YYYYMMDD.jsonl` が `CLAUDE_PROJECT_DIR` 配下の `.claude/logs/` に出る
  - [ ] `CLAUDE_PROJECT_DIR` 未設定でも、祖先に `.git` があればそこが基準になる
  - [ ] どちらも無ければ従来どおり cwd 基準
  - [ ] `audit_log.path` に絶対パスを指定した場合は従来どおりそのまま使う(既存挙動の保持)
- [ ] `hooks/audit/audit_log.py` の相対パス解決を `Path(config.project_root(event.get("cwd")) or ".") / log_dir` に変更する
- [ ] フックを直接実行してスモークする(標準入力に合成イベントを流し、想定の場所にログが出ること)
- [ ] `uv run pytest -q` と `uv run python scripts/verify.py quick` が exit 0

### Task 5: ドキュメントとリリース

**Files:**
- Modify: `docs/hooks/audit_log.md`、`docs/configuration.md`、`docs/security-model.md`
- Modify: `CHANGELOG.md`、`pyproject.toml`、`.claude-plugin/plugin.json`

**Steps:**
- [ ] `docs/hooks/audit_log.md`: 相対 `path` の基準がプロジェクトルート(`CLAUDE_PROJECT_DIR` → git ルート → cwd)であること、絶対パス指定時は従来どおりであること
- [ ] `docs/configuration.md`: `.claude-hooks.json` はプロジェクトルートのものだけが読まれること、サブディレクトリの同名ファイルは読まれず**通知が出る**こと、基準の決定順序
- [ ] `docs/security-model.md`:
  - [ ] 「作業ディレクトリによってプロジェクト層の適用が変わらない」ことを保証として記述
  - [ ] D3(アンカーが環境変数由来であることの影響と、通知による緩和)を 1 項目として追加
  - [ ] 項目 11(基準の使い分け)に、`secrets_guard` の書込先正規化は引き続き cwd 基準であることを明記
- [ ] `CHANGELOG.md` に 0.7.1 を追加する。**修正だけでなく挙動変更も書く**:
  - Fixed: 作業ディレクトリがサブディレクトリのとき、プロジェクト設定が適用されず保護が無言で外れていた
  - Fixed: 同じ原因で `.gitleaks.toml`・`disableAllHooks` 警告・`quality_gate` の auto-detect が効かなかった
  - Fixed: 監査ログが作業ディレクトリ配下に散らばっていた
  - Added: 見つけたのに読まなかったプロジェクト設定を通知する
  - Changed: プロジェクト設定はプロジェクトルートのものだけを読む(モノレポのサブパッケージ設定は読まれなくなる)。`quality_gate` の実行ディレクトリもルートになる
  - 既存の `trusted_projects` の承認は原則として再承認不要(サブディレクトリを承認していた場合のみルートの承認が要る)
- [ ] `pyproject.toml` と `.claude-plugin/plugin.json` を 0.7.1 に(両者の一致を確認)、`uv lock`
- [ ] `uv run python scripts/verify.py all` が exit 0
- [ ] **総合実地確認**: 利用中プロジェクトで、サブディレクトリを `cwd` にしたときの `load_config` がルートと一致すること、
      監査ログがルート配下に出ること、モノレポ想定(サブディレクトリに `.claude-hooks.json` を置く)で通知が出ることを実地で確認する
