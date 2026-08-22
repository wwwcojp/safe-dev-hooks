# Loop Engineering 第1段階(決定論的ゲート) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ターン終了時(Stop)に `scripts/verify.py quick`(leak → ruff → pytest)を強制する loop-hooks ゲートをこのリポジトリに導入し、検証結果を `.loop/evidence.jsonl` に機械記録する。

**Architecture:** 既存のローカルプラグイン `~/loop-hooks`(変更しない)が `.loop-hooks.json` の `gate.command` を Stop で実行する。リポジトリ側は stdlib のみの検証ランナー `scripts/verify.py` と、その設定・保護・ドキュメントを追加する。`quick` は CI の3ステップと同じコマンド・同じ順序にする。

**Tech Stack:** Python 3.10+(stdlib のみ)、uv、pytest、ruff、loop-hooks プラグイン(`~/loop-hooks`、PostToolUse/Stop フック)

**Spec:** `docs/superpowers/specs/2026-08-22-loop-engineering-phase1-design.md`

## Global Constraints

- `scripts/verify.py` は stdlib のみ(依存を `pyproject.toml` に足さない)。`requires-python = ">=3.10"`
- `quick` の中身は CI(`.github/workflows/ci.yml`)と**同じコマンド・同じ順序**: ①leak(`git grep -nP <regex> --`) ②`uv run ruff check hooks tests scripts` ③`uv run pytest -q`
- evidence 行のフォーマットは loop-hooks README の契約: `{"ts","rev","stage","pass","checks":[{"name","ok","ms"}]}`
- `.loop/` は gitignore(公開リポジトリに evidence を載せない)
- リポジトリ内に実ホームパスを書かない(`.claude/rules/no-personal-paths.md`)。プランやコード内では `$HOME`/`~`/`/home/USER` を使う
- `.claude-hooks.json`・`.claude/settings.local.json` は safe-dev-hooks 自身の write_protected で**エージェントは変更できない**。これらはユーザー手動(Task 5)。回避しない
- コミットメッセージに危険コマンドの字面を書かない(`.claude/rules/dogfooding.md`)
- 作業ブランチ: `feat/loop-engineering-phase1`(main から作成済み。spec コミット `895e160` を含む)

## File Structure

| ファイル | 責務 |
|---|---|
| Create `scripts/verify.py` | 検証ランナー本体。`Check` 定義、`STAGES`、`run_stage()`(実行+evidence 追記+失敗出力の転送)、`main()` |
| Create `tests/test_verify.py` | ランナーのテスト(ダミーコマンド注入。実 uv/ruff/pytest は呼ばない)+ CI との一致テスト |
| Modify `tests/conftest.py` | `scripts/` を `sys.path` に追加(`hooks/` と同じ方式) |
| Create `.loop-hooks.json` | ゲート設定(command / timeout / watch / ignore) |
| Modify `.gitignore` | `.loop/` を追加 |
| Modify `.github/workflows/ci.yml` | `ruff check` の対象に `scripts` を追加 |
| Modify `CONTRIBUTING.md` | ゲートの説明と `verify.py quick` の案内 |
| Modify `.claude/rules/dogfooding.md` | ゲートで止まったときの扱い、Bash 経由書込がゲート外になる注意 |

---

### Task 1: verify ランナー本体とテスト

**Files:**
- Create: `scripts/verify.py`
- Create: `tests/test_verify.py`
- Modify: `tests/conftest.py:7-8`

**Interfaces:**
- Produces:
  - `Check(name: str, cmd: list[str], ok_codes: frozenset[int] = frozenset({0}))` — frozen dataclass。`ok_codes` に終了コードが含まれれば成功
  - `LEAK_REGEX: str` — CI と同一の実ホームパス正規表現
  - `STAGES: dict[str, list[Check]]` — `"quick"` のみ
  - `run_stage(stage: str, checks: Sequence[Check] | None = None, repo_root: Path = Path(".")) -> bool`
  - `main() -> None` — `sys.argv[1]`(既定 `quick`)を実行し `sys.exit(0|1)`。未知ステージは `SystemExit`(非ゼロ)
  - evidence は `repo_root/.loop/evidence.jsonl` に1行追記

- [ ] **Step 1: conftest に `scripts/` を追加**

`tests/conftest.py` の

```python
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))
```

を

```python
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
```

にする。

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_verify.py` を作成:

```python
"""scripts/verify.py(検証ランナー)のテスト。実 uv/ruff/pytest は呼ばず、ダミーコマンドを注入する。"""
import json
import sys
from pathlib import Path

import pytest

import verify

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _ok(name):
    return verify.Check(name, [PY, "-c", "print('fine')"])


def _fail(name, msg="boom"):
    return verify.Check(name, [PY, "-c", f"import sys; print({msg!r}); sys.exit(1)"])


def _read_evidence(repo_root):
    lines = (repo_root / ".loop" / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_all_pass_records_pass(tmp_path):
    ok = verify.run_stage("quick", [_ok("a"), _ok("b")], repo_root=tmp_path)

    assert ok is True
    (rec,) = _read_evidence(tmp_path)
    assert rec["stage"] == "quick"
    assert rec["pass"] is True
    assert [c["name"] for c in rec["checks"]] == ["a", "b"]
    assert all(c["ok"] for c in rec["checks"])
    assert all(isinstance(c["ms"], int) for c in rec["checks"])
    assert rec["ts"].endswith("Z")
    assert isinstance(rec["rev"], str) and rec["rev"]


def test_failing_check_stops_and_records_fail(tmp_path):
    ok = verify.run_stage("quick", [_ok("a"), _fail("b"), _ok("c")], repo_root=tmp_path)

    assert ok is False
    (rec,) = _read_evidence(tmp_path)
    assert rec["pass"] is False
    # 失敗したチェックで打ち切り。後続 "c" は走らない
    assert [(c["name"], c["ok"]) for c in rec["checks"]] == [("a", True), ("b", False)]


def test_failure_output_goes_to_stderr(tmp_path, capsys):
    verify.run_stage("quick", [_fail("b", "THE-ERROR-TEXT")], repo_root=tmp_path)

    assert "THE-ERROR-TEXT" in capsys.readouterr().err


def test_success_prints_nothing_to_stderr(tmp_path, capsys):
    verify.run_stage("quick", [_ok("a")], repo_root=tmp_path)

    assert capsys.readouterr().err == ""


def test_ok_codes_inverts_success(tmp_path):
    # git grep 流儀: 終了コード1(不一致)が成功、0(一致=漏洩あり)が失敗
    exits1 = verify.Check("leak", [PY, "-c", "import sys; sys.exit(1)"], ok_codes=frozenset({1}))
    exits0 = verify.Check("leak", [PY, "-c", "print('match found')"], ok_codes=frozenset({1}))

    assert verify.run_stage("quick", [exits1], repo_root=tmp_path) is True
    assert verify.run_stage("quick", [exits0], repo_root=tmp_path) is False
    assert [r["pass"] for r in _read_evidence(tmp_path)] == [True, False]


def test_evidence_appends(tmp_path):
    verify.run_stage("quick", [_ok("a")], repo_root=tmp_path)
    verify.run_stage("quick", [_ok("a")], repo_root=tmp_path)

    assert len(_read_evidence(tmp_path)) == 2


def test_unknown_stage_exits_nonzero(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["verify.py", "nope"])
    with pytest.raises(SystemExit) as exc:
        verify.main()
    assert exc.value.code not in (0, None)


def test_quick_stage_mirrors_ci():
    """spec §4: quick は CI と同じコマンド・同じ順序。片方を変えたらもう片方も変える。"""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    names = [c.name for c in verify.STAGES["quick"]]

    assert names == ["leak", "lint", "tests"]
    assert verify.LEAK_REGEX in ci
    assert "uv run ruff check hooks tests scripts" in ci
    assert "uv run pytest -q" in ci
    leak, lint, tests = verify.STAGES["quick"]
    assert leak.cmd[:3] == ["git", "grep", "-nP"] and leak.ok_codes == frozenset({1})
    assert lint.cmd == ["uv", "run", "ruff", "check", "hooks", "tests", "scripts"]
    assert tests.cmd == ["uv", "run", "pytest", "-q"]
```

- [ ] **Step 3: テストが落ちることを確認**

Run: `uv run pytest tests/test_verify.py -q`
Expected: 収集時に `ModuleNotFoundError: No module named 'verify'` で ERROR

- [ ] **Step 4: `scripts/verify.py` を実装**

```python
"""検証ランナー。チェックを束ねて実行し、結果を .loop/evidence.jsonl に記録する。

loop-hooks の Stop ゲートから `uv run python scripts/verify.py quick` として呼ばれる。
`quick` の中身は CI(.github/workflows/ci.yml)と同じコマンド・同じ順序に保つこと
(tests/test_verify.py::test_quick_stage_mirrors_ci が一致を検査する)。
stdlib のみで書く(hooks/ と同じ流儀)。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# CI の「実ホームパスのリークチェック」と同一。変えるときは ci.yml も変える
LEAK_REGEX = r"/(home|Users)/(?!USER\b|alice\b|user\b)[A-Za-z_][A-Za-z0-9._-]*"


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]
    # 終了コードがこの集合に含まれれば成功。git grep は「不一致=1」が成功なので反転に使う
    ok_codes: frozenset[int] = frozenset({0})


STAGES: dict[str, list[Check]] = {
    "quick": [
        Check("leak", ["git", "grep", "-nP", LEAK_REGEX, "--"], ok_codes=frozenset({1})),
        Check("lint", ["uv", "run", "ruff", "check", "hooks", "tests", "scripts"]),
        Check("tests", ["uv", "run", "pytest", "-q"]),
    ],
}

FAIL_OUTPUT_TAIL = 2000


def run_stage(
    stage: str, checks: Sequence[Check] | None = None, repo_root: Path = Path(".")
) -> bool:
    """チェックを順に実行し、最初の失敗で打ち切る。結果を evidence に1行追記して成否を返す。"""
    checks = STAGES[stage] if checks is None else checks
    results: list[dict[str, Any]] = []
    ok_all = True
    fail_output = ""
    for check in checks:
        start = time.monotonic()
        proc = subprocess.run(
            check.cmd, capture_output=True, text=True, cwd=repo_root, check=False
        )
        ms = int((time.monotonic() - start) * 1000)
        ok = proc.returncode in check.ok_codes
        results.append({"name": check.name, "ok": ok, "ms": ms})
        if not ok:
            ok_all = False
            fail_output = (proc.stdout + proc.stderr)[-FAIL_OUTPUT_TAIL:]
            break
    _append_evidence(repo_root, stage, ok_all, results)
    if not ok_all:
        print(fail_output, file=sys.stderr)
    return ok_all


def _git_rev(repo_root: Path) -> str:
    rev = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, cwd=repo_root, check=False,
    ).stdout.strip()
    if not rev:
        return "unknown"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=repo_root, check=False,
    ).stdout.strip()
    return rev + ("+dirty" if dirty else "")


def _append_evidence(
    repo_root: Path, stage: str, ok: bool, checks: Sequence[dict[str, Any]]
) -> None:
    loop_dir = Path(repo_root) / ".loop"
    loop_dir.mkdir(exist_ok=True)
    line = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "rev": _git_rev(repo_root),
        "stage": stage,
        "pass": ok,
        "checks": list(checks),
    }
    with open(loop_dir / "evidence.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "quick"
    if stage not in STAGES:
        raise SystemExit(f"unknown stage: {stage} (available: {', '.join(STAGES)})")
    sys.exit(0 if run_stage(stage) else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: テストが通ることを確認**

Run: `uv run pytest tests/test_verify.py -v`
Expected: 8件 PASS

(注: `test_quick_stage_mirrors_ci` は Task 2 で `ci.yml` に `scripts` を足すまで **FAIL** する。この時点では 7 PASS / 1 FAIL が正しい。Task 2 Step 3 で全通過を確認する)

- [ ] **Step 6: mutation 確認(受け入れ条件)**

`run_stage` の `break` を一時的にコメントアウトして `uv run pytest tests/test_verify.py -q` を実行し、
`test_failing_check_stops_and_records_fail` が落ちることを確認する。次に `ok = proc.returncode in check.ok_codes` を
`ok = proc.returncode == 0` に変えて `test_ok_codes_inverts_success` が落ちることを確認する。
**両方とも元に戻す。**

- [ ] **Step 7: lint**

Run: `uv run ruff check hooks tests scripts`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add scripts/verify.py tests/test_verify.py tests/conftest.py
git commit -m "feat(verify): 検証ランナー scripts/verify.py を追加(leak→ruff→pytest、evidence記録)"
```

---

### Task 2: ゲート設定・gitignore・CI

**Files:**
- Create: `.loop-hooks.json`
- Modify: `.gitignore`(「Hooks runtime output」節)
- Modify: `.github/workflows/ci.yml:22`

**Interfaces:**
- Consumes: `scripts/verify.py`(Task 1)。`uv run python scripts/verify.py quick` が終了コードで成否を返すこと

- [ ] **Step 1: `.loop-hooks.json` を作成**

```json
{
  "gate": {
    "command": "uv run python scripts/verify.py quick",
    "timeout_sec": 120,
    "watch": ["*.py", "*.json", "pyproject.toml"],
    "ignore": [".loop/*", ".superpowers/*"]
  }
}
```

- [ ] **Step 2: `.gitignore` に `.loop/` を追加**

```
# Hooks runtime output (ローカル実行時に生成されるログ・状態ファイル)
logs/
.claude/logs/
*.jsonl
```

を

```
# Hooks runtime output (ローカル実行時に生成されるログ・状態ファイル)
logs/
.claude/logs/
*.jsonl

# loop-hooks のゲート状態・evidence(ローカル作業証跡。最終判定者はCI)
.loop/
```

にする。

- [ ] **Step 3: CI の ruff 対象に `scripts` を追加**

`.github/workflows/ci.yml` の

```yaml
      - name: Lint
        run: uv run ruff check hooks tests
```

を

```yaml
      - name: Lint
        run: uv run ruff check hooks tests scripts
```

にする。

- [ ] **Step 4: 全テスト・lint 通過を確認**

Run: `uv run pytest -q && uv run ruff check hooks tests scripts`
Expected: 全件 PASS(`test_quick_stage_mirrors_ci` を含む)、`All checks passed!`

- [ ] **Step 5: ランナーを実際に走らせ evidence を確認**

Run: `uv run python scripts/verify.py quick; echo "exit=$?"; tail -1 .loop/evidence.jsonl; git status --short .loop`
Expected: `exit=0`、evidence の最終行が `"stage": "quick", "pass": true` で `checks` に leak/lint/tests の3つ、`git status` に `.loop/` が**出ない**(ignore 済み)

- [ ] **Step 6: loop-hooks の手動スモーク(プラグイン未有効のまま、フックを stdin で直接叩く)**

```bash
echo '{"tool_name":"Edit","cwd":"'$PWD'","tool_input":{"file_path":"'$PWD'/scripts/verify.py"}}' \
  | uv run ~/loop-hooks/hooks/post_tool_use/mark_dirty.py
cat .loop/state.json
echo '{"cwd":"'$PWD'","stop_hook_active":false}' | uv run ~/loop-hooks/hooks/stop/gate.py; echo "exit=$?"
cat .loop/state.json
echo '{"tool_name":"Edit","cwd":"'$PWD'","tool_input":{"file_path":"'$PWD'/README.md"}}' \
  | uv run ~/loop-hooks/hooks/post_tool_use/mark_dirty.py
cat .loop/state.json
```

Expected: 1回目 `cat` → `{"dirty": true}`(`.py` は watch 対象)。gate → `exit=0` で `{"dirty": false}`。
`.md` の編集後 → `{"dirty": false}` のまま(watch 対象外)。

- [ ] **Step 7: Commit**

```bash
git add .loop-hooks.json .gitignore .github/workflows/ci.yml
git commit -m "chore(loop): loop-hooks ゲート設定を追加、.loop/ を ignore、CI lint に scripts を追加"
```

---

### Task 3: ドキュメント(CONTRIBUTING・dogfooding 規約)

**Files:**
- Modify: `CONTRIBUTING.md`(「前提環境」の直後と「PRを出す前の確認事項」)
- Modify: `.claude/rules/dogfooding.md`

- [ ] **Step 1: CONTRIBUTING.md に検証ゲートの節を追加**

「### ドッグフーディング時の注意」節の**直前**に次を挿入:

```markdown
### 検証ゲート(loop-hooks)

このリポジトリは [loop-hooks](~/loop-hooks) プラグインによる「ターン終了時の検証ゲート」を前提に開発する。`.py`/`.json`/`pyproject.toml` を Edit/Write で変更したターンの終了時に `uv run python scripts/verify.py quick`(実ホームパスのリークチェック → `ruff check` → `pytest`。CI と同じコマンド・同じ順序)が強制され、失敗するとターンを終われない。結果は `.loop/evidence.jsonl`(gitignore)に1実行1行で記録される。

- 手動で回すとき: `uv run python scripts/verify.py quick`(約1秒)
- 設定は `.loop-hooks.json`。ゲート設定と `.loop/state.json` は書き込み保護されており、エージェントは変更できない(ゲートに詰まったらコードを直す)
- 有効化はプロジェクト単位: `.claude/settings.local.json` の `enabledPlugins` に `"loop-hooks@loop-hooks": true`(設計: `docs/superpowers/specs/2026-08-22-loop-engineering-phase1-design.md`)
```

- [ ] **Step 2: PR 前チェックリストを更新**

```markdown
- [ ] `uv run pytest -q` が green である
- [ ] `uv run ruff check hooks tests` がクリーンである(CI と同じコマンド、`.github/workflows/ci.yml` 参照)
```

を

```markdown
- [ ] `uv run python scripts/verify.py quick` が通る(= `pytest -q` green、`ruff check hooks tests scripts` クリーン、実ホームパスのリークなし。CI と同じ3チェック、`.github/workflows/ci.yml` 参照)
```

にする。また「危険パターン…の追加手順」の手順 3・4

```markdown
3. **`uv run pytest -q` を実行し、全テストが green であることを確認する**
4. **`uv run ruff check hooks tests` でlintエラーが無いことを確認する**
```

を

```markdown
3. **`uv run python scripts/verify.py quick` を実行し、全チェック(リーク・lint・テスト)が通ることを確認する**
```

にし、続く「5.」を「4.」に振り直す。

- [ ] **Step 3: dogfooding.md にゲートの扱いを追記**

`.claude/rules/dogfooding.md` の「## よくある遮断と回避」の項目 2 の後(「## 参照」の前)に次を追加:

```markdown
3. **ターン終了時に loop-hooks の検証ゲート(`scripts/verify.py quick`)で止められる** — `.py`/`.json`/`pyproject.toml` を Edit/Write で変更したターンは、終了時に leak → ruff → pytest が強制される。失敗したら**コードを直して再度終了する**。`.loop-hooks.json`・`.loop/state.json` は書込保護であり、ゲート設定を変えて通そうとしない(保護は「エージェントに回避できない」ことが設計)。

   **注意:** ゲートの dirty 判定は `Edit|Write` のみで、**項目 1 の Bash 経由の python 書込にはゲートが掛からない**。`hooks/`・`rules/` を変更する開発作業では、Bash 回避ではなく `CONTRIBUTING.md` の選択肢 1(プラグインを一時的に無効化して通常の Edit/Write を使う)を優先すること。Bash で書いた場合は自分で `uv run python scripts/verify.py quick` を回す。
```

- [ ] **Step 4: 実ホームパスのリークチェック**

Run: `uv run python scripts/verify.py quick; echo "exit=$?"`
Expected: `exit=0`(`~/loop-hooks` 表記はリークに該当しない)

- [ ] **Step 5: Commit**

```bash
git add CONTRIBUTING.md .claude/rules/dogfooding.md
git commit -m "docs: 検証ゲート(loop-hooks)の運用を CONTRIBUTING と dogfooding 規約に追記"
```

---

### Task 4: 全体確認・手動作業の引き渡し

**Files:**
- なし(確認と報告のみ)

- [ ] **Step 1: ブランチ全体の検証**

Run: `uv run python scripts/verify.py quick; echo "exit=$?"; git status --short; git log --oneline main..HEAD`
Expected: `exit=0`、working tree clean(`docs/superpowers/specs/2026-07-26-project-config-trust-optin-design.md` は別作業の untracked ファイルなので放置)、コミットは spec + Task 1〜3 の4件

- [ ] **Step 2: ユーザー手動作業の提示**

実装完了の報告に、spec §6 の2件をそのまま転記する(エージェントは write_protected により実施できない):

1. `.claude-hooks.json` に `secrets_guard` を追加:
   ```json
   "secrets_guard": {
     "write_protected_paths": [".loop-hooks.json", "*.loop/state.json"]
   }
   ```
   確認: `python3 -c "import json; json.load(open('.claude-hooks.json'))"`
2. `.claude/settings.local.json` に `"enabledPlugins": {"loop-hooks@loop-hooks": true}` を追加 → **新しいセッション**で有効

- [ ] **Step 3: 新セッションでの実発火確認手順を報告に含める**

(rakuten-optimizer 申し送り §2 と同じ3件。有効化後の新セッションで実施)

1. `scripts/verify.py` に無害な1行(例: 末尾に `# gate check` コメント)を Edit で追加 → ターン終了 → `.loop/evidence.jsonl` に `"pass":true` の行が増えること
2. `tests/test_verify.py` に `assert False` を含むテストを Edit で追加 → ターン終了がブロックされ、pytest の失敗出力が Claude に届く → 直して再終了で通過、evidence に `"pass":false` と `"pass":true` の2行
3. `README.md` だけを Edit → ターン終了時にゲートが走らず evidence が増えないこと
4. 後始末(1〜3 の変更を戻す)

---

## Self-Review

- **Spec coverage**: §4 ランナー → Task 1 / §4.1 evidence・gitignore → Task 1+2 / §5 設定 → Task 2 / §6 保護・有効化 → Task 4 Step 2(手動) / §7 CI・CONTRIBUTING・dogfooding → Task 2+3 / §8.1 Bash 書込の注意 → Task 3 Step 3 / §9 ハーネステスト(mutation 確認含む) → Task 1 Step 2・6 / §10・§11 は作らない・ロードマップなのでタスク無し
- **Placeholder scan**: なし
- **Type consistency**: `Check(name, cmd, ok_codes)`・`run_stage(stage, checks, repo_root)`・`STAGES`・`LEAK_REGEX` を Task 1 のテストと実装で同名使用。Task 2 の `.loop-hooks.json` の `command` は Task 1 の `main()` の引数形式(`quick`)と一致
