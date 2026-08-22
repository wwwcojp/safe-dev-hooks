# Loop Engineering 第2段階(Mutation 自動化) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mutmut によるファイル別 mutation score のラチェット(`scripts/verify.py mutation`)を導入し、lib 4 本 + `bash_guard.py` の生き残り変異をトリアージして baseline を確定する。

**Architecture:** 前提として hook/テストの import をルート起点(`from hooks.lib import …`)に揃える(mutmut の変異キー要件)。`verify.py` に `mutation` ステージ(mutmut 実行 → `mutants/**/*.py.meta` からファイル別 score → `.loop/mutation-baseline.json` と比較。下回る/欠落で fail、向上で更新)と `all` ステージを足す。baseline は Git 追跡、`mutants/` は ignore。トリアージはファイルごとに 1 タスクで、テスト補強と理由付き `# pragma: no mutate` で score を上げ、結果をスパイク文書に追記する。

**Tech Stack:** Python 3.10+ stdlib(verify.py)、uv、pytest、ruff、mutmut 3.7

**Spec:** `docs/superpowers/specs/2026-08-22-loop-engineering-phase2-mutation-design.md`(スパイク: `docs/superpowers/specs/2026-08-22-mutation-spike-results.md`)

## Global Constraints

- `scripts/verify.py` は stdlib のみ(`pyproject.toml` の dev 依存に足すのは `mutmut>=3.7` だけ)。`requires-python = ">=3.10"`
- `quick` ステージと CI の一致原則(第1段階)は変えない。CI は変更しない。mutation は Stop ゲートに入れない
- killed の終了コード = `{1, 3, -24}`(mutmut `status_by_exit_code` の "killed")。score = `round(killed / 全変異 * 100, 1)`。「no tests」(5/33)・survived(0)・timeout 等はすべて未検出扱い
- baseline `.loop/mutation-baseline.json` の形式: `{"files": {"<repo相対パス>": <score>, …}, "updated": "<UTC ISO>"}`。**Git 追跡**(`.gitignore` は `.loop/*` + `!.loop/mutation-baseline.json`)
- ラチェット: 下回ったファイルが 1 つでもあれば fail(全件列挙)/ baseline にあって結果に無いファイルは fail / 新規ファイルは登録 / 同点以上は pass・上回った分だけ更新 / 変化が無ければ baseline ファイルを書き換えない
- `hooks/`・`rules/` は自インストールの `write_protected` で Edit/Write が deny される。**Bash 経由の python スクリプト書込**(`.claude/rules/dogfooding.md` 項目 1)で行う。`tests/`・`scripts/`・`docs/`・`.claude/rules/`・`pyproject.toml`・`.gitignore` は通常の Edit/Write でよい
- `hooks/` は**このセッションで稼働中のガードそのもの**。hook を書き換えた直後に pytest と全 hook の直接実行スモーク(Task 1 Step 5)を必ず回す。Bash 書込は loop-hooks のゲート外なので `uv run python scripts/verify.py quick` を明示実行する
- リポジトリ内に実ホームパスを書かない(`$HOME`/`~`/`/home/USER`/`/home/alice` のみ)。コミットメッセージに危険コマンドの字面を書かない
- `# pragma: no mutate` は**真の等価変異のみ**・行単位・理由コメント必須。ガードの挙動は変えない(必要なら報告に記録し、`.claude/rules/guard-rule-changes.md` に従って別途)
- 作業ブランチ: `feat/loop-engineering-phase2`(main から作成済み。spec コミット `578e3ee` を含む)

## File Structure

| ファイル | 責務 |
|---|---|
| Create `hooks/__init__.py`(空) | `hooks` を通常パッケージにする(名前空間パッケージの合流防止) |
| Modify hook スクリプト 9 本 | `sys.path.insert` をプラグインルートへ、`from hooks.lib import …` |
| Modify `tests/conftest.py`・`tests/helpers.py`・`tests/test_*.py` 9 本 | ルート起点 import、`load_hook` を `importlib.import_module` 方式に |
| Modify `pyproject.toml`・`uv.lock` | dev 依存 mutmut、`[tool.mutmut]` |
| Modify `.gitignore` | `.loop/*` + `!.loop/mutation-baseline.json`、`mutants/` |
| Modify `scripts/verify.py` | `mutation_scores` / `check_mutation_baseline` / `run_mutation` / `main` の `mutation`・`all` |
| Modify `tests/test_verify.py` | mutation ステージのテスト(偽 meta・runner 注入) |
| Create `.loop/mutation-baseline.json` | 初回実行で verify.py が生成。以降 Git 追跡 |
| Modify `tests/test_patterns.py` 等 | トリアージで追加するテスト |
| Modify `hooks/lib/*.py`・`hooks/pre_tool_use/bash_guard.py` | 等価変異の `# pragma: no mutate`(理由コメント付き) |
| Modify `CONTRIBUTING.md`・`.claude/rules/dogfooding.md`・第1段階 spec §11・スパイク文書 | 運用・結果の記録 |

---

### Task 1: import のルート起点化(hook 9 本・テスト・helpers・conftest)

**Files:**
- Create: `hooks/__init__.py`
- Modify: `hooks/pre_tool_use/bash_guard.py`, `hooks/pre_tool_use/secrets_guard.py`, `hooks/pre_tool_use/exfil_guard.py`, `hooks/post_tool_use/secrets_scan.py`, `hooks/post_tool_use/quality_gate.py`, `hooks/post_tool_use/exfil_output_scan.py`, `hooks/notification/notify.py`, `hooks/config_change/config_guard.py`, `hooks/audit/audit_log.py`(各 2 行)
- Modify: `tests/conftest.py:8`, `tests/helpers.py`, `tests/test_audit_and_notify.py`, `tests/test_config.py`, `tests/test_config_guard.py`, `tests/test_exfil_guard.py`, `tests/test_hook_io.py`, `tests/test_patterns.py`, `tests/test_quality_gate.py`, `tests/test_scanners.py`, `tests/test_secrets_scan.py`(各 1 行)

**Interfaces:**
- Produces: 実行時モジュール名が `hooks.lib.<name>`、hook スクリプトが `hooks.<dir>.<name>`(`tests.helpers.load_hook("pre_tool_use/bash_guard.py")` は `hooks.pre_tool_use.bash_guard` を fresh に import して返す)。Task 2 の mutmut はこれを前提に変異キーを照合する

- [ ] **Step 1: 現状確認**

Run: `grep -rn "sys.path.insert" hooks/*/*.py | wc -l; grep -rln "^from lib import" hooks/*/*.py | wc -l; grep -rln "^from lib import" tests/*.py | wc -l`
Expected: `9` / `9` / `9`

- [ ] **Step 2: hook 9 本と `hooks/__init__.py` を Bash 経由の python で書き換える(検証してから書く 2 パス)**

```bash
python3 - <<'EOF'
from pathlib import Path
OLD_PATH = "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))"
NEW_PATH = "sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))"
targets = [p for p in sorted(Path("hooks").glob("*/*.py")) if p.parent.name != "lib"]
assert len(targets) == 9, targets
# pass 1: 全ファイルが期待どおりの形か検証(1 つでも違えば何も書かない)
for p in targets:
    s = p.read_text(encoding="utf-8")
    assert s.count(OLD_PATH) == 1, f"{p}: sys.path 行が 1 つでない"
    assert s.count("from lib import ") == 1, f"{p}: from lib import が 1 つでない"
# pass 2: 書き換え
for p in targets:
    s = p.read_text(encoding="utf-8")
    s = s.replace(OLD_PATH, NEW_PATH).replace("from lib import ", "from hooks.lib import ")
    p.write_text(s, encoding="utf-8")
    print("rewrote", p)
Path("hooks/__init__.py").write_text("", encoding="utf-8")
print("created hooks/__init__.py")
EOF
```

Expected: `rewrote …` が 9 行 + `created hooks/__init__.py`。(このコマンドが `secrets_guard` に deny された場合は BLOCKED として報告する — ユーザーがプラグインを一時無効化する必要がある)

- [ ] **Step 3: テスト側を Edit で書き換える**

`tests/conftest.py`:

```python
sys.path.insert(0, str(REPO_ROOT / "hooks"))
```
→
```python
sys.path.insert(0, str(REPO_ROOT))
```

`tests/helpers.py` 全体を次にする:

```python
import importlib
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def load_hook(relpath: str):
    """hooks/配下のスクリプトを `hooks.<dir>.<name>` として読み込む(__main__ガード前提)。

    呼び出しごとに fresh なモジュールを返す(以前の spec_from_file_location 方式と同じ契約)。
    ルート起点の完全修飾名で import するのは mutmut が変異キーをファイルパス由来
    (hooks.pre_tool_use.bash_guard)で照合するため。
    """
    name = "hooks." + relpath[: -len(".py")].replace("/", ".")
    sys.modules.pop(name, None)
    return importlib.import_module(name)
```

テスト 9 本: `from lib import X` → `from hooks.lib import X`(各ファイル 1 行。`sed -i 's/^from lib import/from hooks.lib import/' tests/test_*.py` でよい。`tests/` は保護対象外)

- [ ] **Step 4: pytest・ruff**

Run: `uv run pytest -q && uv run ruff check hooks tests scripts`
Expected: 239 passed、ruff clean。ruff が I001(import 並び)を `tests/` で指摘したら `uv run ruff check --fix tests` で直す。`hooks/` で指摘したら Step 2 と同じ python 書込方式で並びを直す(`# noqa: E402` は残す)

- [ ] **Step 5: 全 hook の直接実行スモーク(稼働中ガードを壊していないこと)**

```bash
for f in hooks/*/*.py; do
  case "$f" in hooks/lib/*) continue;; esac
  out=$(echo '{}' | uv run "$f" 2>&1); code=$?
  printf '%s exit=%s\n' "$f" "$code"
  printf '%s\n' "$out" | grep -q Traceback && printf '  TRACEBACK in %s\n' "$f"
done
```

Expected: 9 行とも `exit=0`、`TRACEBACK` 行なし(import が壊れていればモジュール先頭で traceback + exit 1 になる)

- [ ] **Step 6: 第1段階のゲートを明示実行(Bash 書込はゲート外)**

Run: `uv run python scripts/verify.py quick; echo "exit=$?"`
Expected: `exit=0`

- [ ] **Step 7: Commit**

```bash
git add hooks/__init__.py hooks/*/*.py tests/conftest.py tests/helpers.py tests/test_*.py
git commit -m "refactor: hook/テストの import をルート起点(hooks.lib)に統一 — mutmut の変異キー要件に合わせる"
```

---

### Task 2: mutmut 導入(依存・設定・gitignore)と実走確認

**Files:**
- Modify: `pyproject.toml`(dev 依存、`[tool.mutmut]`)、`uv.lock`(`uv add` が更新)
- Modify: `.gitignore:18-19`

**Interfaces:**
- Consumes: Task 1(ルート起点 import)
- Produces: `uv run mutmut run` が完走し `mutants/<path>.py.meta` を生成する。Task 3 の `mutation_scores` はこの meta を読む

- [ ] **Step 1: dev 依存に mutmut**

Run: `uv add --dev "mutmut>=3.7"`
Expected: `pyproject.toml` の `dev = [...]` に `"mutmut>=3.7"` が加わり、`uv.lock` が更新される(`pytest`・`ruff` の指定は変えない。`uv add` が並びを変えても中身が同じなら可)

- [ ] **Step 2: `[tool.mutmut]` を追加**

`pyproject.toml` 末尾に追記:

```toml

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

- [ ] **Step 3: `.gitignore`**

```
# loop-hooks のゲート状態・evidence(ローカル作業証跡。最終判定者はCI)
.loop/
```
→
```
# loop-hooks のゲート状態・evidence(ローカル作業証跡。最終判定者はCI)
# mutation baseline だけは Git 追跡する(ラチェットを clone 間で共有し、PR の diff でレビューする)
.loop/*
!.loop/mutation-baseline.json

# mutmut の作業ディレクトリ
mutants/
```

- [ ] **Step 4: mutmut 実走(約 11 秒)**

Run: `rm -rf mutants; uv run mutmut run 2>&1 | tr '\r' '\n' | grep -E "mutations/second|Stopping|Recorded|Expected" ; ls mutants/hooks/lib/*.meta mutants/hooks/pre_tool_use/*.meta`
Expected: `… mutations/second` の行が出て `Stopping`/`Recorded`/`Expected` は出ない。meta が 5 つ(`config.py.meta`・`hook_io.py.meta`・`patterns.py.meta`・`scanners.py.meta`・`bash_guard.py.meta`)。

Run: `git status --short`
Expected: `mutants/` は出ない(ignore 済み)。変更は `pyproject.toml`・`uv.lock`・`.gitignore` のみ

- [ ] **Step 5: quick とテスト**

Run: `uv run python scripts/verify.py quick; echo "exit=$?"`
Expected: `exit=0`(pytest は `testpaths=["tests"]` なので `mutants/tests` を拾わない)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore
git commit -m "chore(mutation): mutmut 3.7 を導入(source_paths=hooks、lib 4本+bash_guard を対象)、mutants/ を ignore、baseline を Git 追跡"
```

---

### Task 3: `verify.py mutation` / `all` ステージとハーネステスト

**Files:**
- Modify: `scripts/verify.py`(`main` の前に関数群を追加、`main` を差し替え)
- Modify: `tests/test_verify.py`(末尾に追加)

**Interfaces:**
- Consumes: `mutants/**/*.py.meta`(Task 2)、既存の `_append_evidence(repo_root, stage, ok, checks)`・`FAIL_OUTPUT_TAIL`・`REPO_ROOT`・`run_stage`
- Produces:
  - `MUTATION_KILLED_CODES: frozenset[int] = frozenset({1, 3, -24})`
  - `MUTMUT_CMD: list[str] = ["uv", "run", "mutmut", "run"]`
  - `mutation_scores(repo_root: Path) -> dict[str, dict[str, Any]]` — `{"hooks/lib/a.py": {"score": 42.9, "killed": 3, "total": 7}}`
  - `check_mutation_baseline(repo_root: Path, scores: dict) -> tuple[bool, list[str]]` — ok なら baseline を(変化があれば)書き、問題列は空
  - `run_mutation(repo_root: Path = REPO_ROOT, runner: Callable[[Path], tuple[int, str]] | None = None) -> bool`
  - `main()` が `mutation` / `all` を受け付ける

- [ ] **Step 1: 失敗するテストを書く**(`tests/test_verify.py` 末尾に追加)

```python
# ---- mutation ステージ(mutmut は実行せず runner を注入する) ----


def _write_meta(repo_root, rel, codes):
    meta = repo_root / "mutants" / (rel + ".meta")
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"exit_code_by_key": codes}), encoding="utf-8")


def _baseline(repo_root):
    return json.loads((repo_root / ".loop" / "mutation-baseline.json").read_text(encoding="utf-8"))


def _ok_runner(root):
    return 0, ""


def test_mutation_scores_counts_killed_codes_per_file(tmp_path):
    # 1/3/-24 = killed、0 = survived、33 = no tests、36 = timeout、None = not checked
    _write_meta(tmp_path, "hooks/lib/a.py",
                {"k1": 1, "k2": 3, "k3": -24, "k4": 0, "k5": 33, "k6": 36, "k7": None})
    _write_meta(tmp_path, "hooks/lib/b.py", {"k1": 1, "k2": 1})

    assert verify.mutation_scores(tmp_path) == {
        "hooks/lib/a.py": {"score": 42.9, "killed": 3, "total": 7},
        "hooks/lib/b.py": {"score": 100.0, "killed": 2, "total": 2},
    }


def test_mutation_first_run_registers_baseline(tmp_path, capsys):
    _write_meta(tmp_path, "hooks/lib/a.py", {"k1": 1, "k2": 0})

    ok = verify.run_mutation(tmp_path, runner=_ok_runner)

    assert ok is True
    assert _baseline(tmp_path)["files"] == {"hooks/lib/a.py": 50.0}
    assert _baseline(tmp_path)["updated"].endswith("Z")
    (rec,) = _read_evidence(tmp_path)
    assert rec["stage"] == "mutation" and rec["pass"] is True
    assert [c["name"] for c in rec["checks"]] == ["mutmut", "baseline"]
    assert rec["checks"][1]["scores"]["hooks/lib/a.py"] == {"score": 50.0, "killed": 1, "total": 2}
    assert "hooks/lib/a.py: 50.0 (1/2)" in capsys.readouterr().out


def test_mutation_regression_fails_and_keeps_baseline(tmp_path, capsys):
    _write_meta(tmp_path, "hooks/lib/a.py", {"k1": 1, "k2": 1})
    _write_meta(tmp_path, "hooks/lib/b.py", {"k1": 1, "k2": 1})
    assert verify.run_mutation(tmp_path, runner=_ok_runner) is True
    _write_meta(tmp_path, "hooks/lib/b.py", {"k1": 1, "k2": 0})  # b: 100 → 50

    ok = verify.run_mutation(tmp_path, runner=_ok_runner)

    assert ok is False
    assert _baseline(tmp_path)["files"] == {"hooks/lib/a.py": 100.0, "hooks/lib/b.py": 100.0}
    err = capsys.readouterr().err
    assert "hooks/lib/b.py: score 50.0 < baseline 100.0" in err
    assert "hooks/lib/a.py" not in err  # 下回っていないファイルは列挙しない
    assert "mutmut results" in err
    assert [r["pass"] for r in _read_evidence(tmp_path)] == [True, False]


def test_mutation_improvement_updates_only_that_file(tmp_path):
    _write_meta(tmp_path, "hooks/lib/a.py", {"k1": 1, "k2": 0})
    _write_meta(tmp_path, "hooks/lib/b.py", {"k1": 1, "k2": 0})
    verify.run_mutation(tmp_path, runner=_ok_runner)
    _write_meta(tmp_path, "hooks/lib/b.py", {"k1": 1, "k2": 1})  # b: 50 → 100

    assert verify.run_mutation(tmp_path, runner=_ok_runner) is True
    assert _baseline(tmp_path)["files"] == {"hooks/lib/a.py": 50.0, "hooks/lib/b.py": 100.0}


def test_mutation_unchanged_scores_do_not_rewrite_baseline(tmp_path):
    import os

    _write_meta(tmp_path, "hooks/lib/a.py", {"k1": 1, "k2": 0})
    verify.run_mutation(tmp_path, runner=_ok_runner)
    path = tmp_path / ".loop" / "mutation-baseline.json"
    os.utime(path, (1, 1))

    assert verify.run_mutation(tmp_path, runner=_ok_runner) is True
    assert path.stat().st_mtime == 1  # 変化が無ければ書き換えない(git の diff を汚さない)


def test_mutation_missing_file_in_results_fails(tmp_path, capsys):
    _write_meta(tmp_path, "hooks/lib/a.py", {"k1": 1})
    _write_meta(tmp_path, "hooks/lib/b.py", {"k1": 1})
    verify.run_mutation(tmp_path, runner=_ok_runner)
    (tmp_path / "mutants" / "hooks" / "lib" / "b.py.meta").unlink()  # only_mutate から外れた想定

    assert verify.run_mutation(tmp_path, runner=_ok_runner) is False
    err = capsys.readouterr().err
    assert "hooks/lib/b.py" in err and "baseline を手で外す" in err
    assert _baseline(tmp_path)["files"] == {"hooks/lib/a.py": 100.0, "hooks/lib/b.py": 100.0}


def test_mutation_runner_failure_records_evidence_and_keeps_baseline(tmp_path, capsys):
    _write_meta(tmp_path, "hooks/lib/a.py", {"k1": 1})

    ok = verify.run_mutation(tmp_path, runner=lambda root: (1, "mutmut exploded: THE-MUTMUT-ERROR"))

    assert ok is False
    assert not (tmp_path / ".loop" / "mutation-baseline.json").exists()
    (rec,) = _read_evidence(tmp_path)
    assert rec["pass"] is False
    assert [(c["name"], c["ok"]) for c in rec["checks"]] == [("mutmut", False)]
    assert "THE-MUTMUT-ERROR" in capsys.readouterr().err


def test_mutation_no_results_fails(tmp_path, capsys):
    ok = verify.run_mutation(tmp_path, runner=_ok_runner)  # meta が 1 つも無い

    assert ok is False
    assert "変異結果" in capsys.readouterr().err
    (rec,) = _read_evidence(tmp_path)
    assert rec["pass"] is False


def test_main_mutation_stage_maps_exit_code(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["verify.py", "mutation"])
    monkeypatch.setattr(verify, "run_mutation", lambda *a, **k: False)
    with pytest.raises(SystemExit) as exc:
        verify.main()
    assert exc.value.code == 1


def test_main_all_skips_mutation_when_quick_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["verify.py", "all"])
    monkeypatch.setattr(verify, "run_stage", lambda *a, **k: calls.append("quick") or False)
    monkeypatch.setattr(verify, "run_mutation", lambda *a, **k: calls.append("mutation") or True)
    with pytest.raises(SystemExit) as exc:
        verify.main()
    assert exc.value.code == 1 and calls == ["quick"]


def test_main_all_runs_mutation_after_quick(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["verify.py", "all"])
    monkeypatch.setattr(verify, "run_stage", lambda *a, **k: calls.append("quick") or True)
    monkeypatch.setattr(verify, "run_mutation", lambda *a, **k: calls.append("mutation") or True)
    with pytest.raises(SystemExit) as exc:
        verify.main()
    assert exc.value.code == 0 and calls == ["quick", "mutation"]
```

- [ ] **Step 2: 落ちることを確認**

Run: `uv run pytest tests/test_verify.py -q 2>&1 | tail -3`
Expected: `mutation_scores`/`run_mutation` が無い AttributeError 等で新規テストが FAIL(`all`/`mutation` の main テストは未知ステージ扱いで FAIL)

- [ ] **Step 3: `scripts/verify.py` に実装を追加**

`from collections.abc import Sequence` を `from collections.abc import Callable, Sequence` に変更。`FAIL_OUTPUT_TAIL = 2000` の直後に追加:

```python
# mutmut の終了コード→状態(mutmut/__main__.py status_by_exit_code)のうち "killed" のもの。
# survived(0)・no tests(5/33)・timeout・suspicious 等はすべて「検出できていない」として数える
MUTATION_KILLED_CODES = frozenset({1, 3, -24})
MUTMUT_CMD = ["uv", "run", "mutmut", "run"]
BASELINE_REL = Path(".loop") / "mutation-baseline.json"
```

`main()` の直前に追加:

```python
def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mutation_scores(repo_root: Path) -> dict[str, dict[str, Any]]:
    """mutants/ 配下の *.py.meta からファイル別 {score, killed, total} を集計する。

    キーはリポジトリ相対パス(例: hooks/lib/patterns.py)。
    """
    mutants = Path(repo_root) / "mutants"
    scores: dict[str, dict[str, Any]] = {}
    for meta in sorted(mutants.rglob("*.py.meta")):
        codes = json.loads(meta.read_text(encoding="utf-8"))["exit_code_by_key"]
        if not codes:
            continue
        total = len(codes)
        killed = sum(1 for c in codes.values() if c in MUTATION_KILLED_CODES)
        rel = meta.relative_to(mutants).as_posix()[: -len(".meta")]
        scores[rel] = {"score": round(killed / total * 100, 1), "killed": killed, "total": total}
    return scores


def check_mutation_baseline(repo_root: Path, scores: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    """ファイル別ラチェット。(ok, 問題の一覧) を返す。ok で変化があれば baseline を書き換える。

    - 下回ったファイル / baseline にあって結果に無いファイル → fail(全件列挙)
    - 新規ファイルは登録、上回った分だけ更新。変化が無ければファイルに触らない
    """
    path = Path(repo_root) / BASELINE_REL
    baseline: dict[str, float] = {}
    if path.exists():
        baseline = json.loads(path.read_text(encoding="utf-8")).get("files", {})
    problems: list[str] = []
    for f, b in sorted(baseline.items()):
        if f not in scores:
            problems.append(
                f"{f}: baseline {b} にあるが今回の結果に無い(only_mutate から外れている?"
                " 対象の縮小は baseline を手で外す必要がある)"
            )
        elif scores[f]["score"] < b:
            problems.append(f"{f}: score {scores[f]['score']} < baseline {b}")
    if problems:
        return False, problems
    new = {f: max(s["score"], baseline.get(f, 0.0)) for f, s in scores.items()}
    if new != baseline:
        path.parent.mkdir(exist_ok=True)
        payload = {"files": dict(sorted(new.items())), "updated": _utc_now()}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, []


def _run_mutmut(repo_root: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            MUTMUT_CMD, capture_output=True, encoding="utf-8", errors="replace",
            cwd=repo_root, check=False,
        )
    except OSError as e:
        return 1, f"{MUTMUT_CMD[0]}: {e}"
    return proc.returncode, proc.stdout + proc.stderr


def run_mutation(
    repo_root: Path = REPO_ROOT, runner: Callable[[Path], tuple[int, str]] | None = None
) -> bool:
    """mutmut を実行し、ファイル別 score を baseline とラチェット比較して evidence に記録する。"""
    run = runner or _run_mutmut
    start = time.monotonic()
    code, output = run(repo_root)
    checks: list[dict[str, Any]] = [
        {"name": "mutmut", "ok": code == 0, "ms": int((time.monotonic() - start) * 1000)}
    ]
    if code != 0:
        _append_evidence(repo_root, "mutation", False, checks)
        print(output[-FAIL_OUTPUT_TAIL:], file=sys.stderr)
        return False
    scores = mutation_scores(repo_root)
    if not scores:
        checks.append({"name": "baseline", "ok": False, "scores": {}})
        _append_evidence(repo_root, "mutation", False, checks)
        print("mutants/ に変異結果(*.py.meta)が無い。[tool.mutmut] の only_mutate を確認する", file=sys.stderr)
        return False
    ok, problems = check_mutation_baseline(repo_root, scores)
    checks.append({"name": "baseline", "ok": ok, "scores": scores})
    _append_evidence(repo_root, "mutation", ok, checks)
    if ok:
        for f, s in sorted(scores.items()):
            print(f"{f}: {s['score']} ({s['killed']}/{s['total']})")
    else:
        print(
            "mutation score が baseline を下回りました:\n  " + "\n  ".join(problems)
            + "\n生き残りは `uv run mutmut results` / `uv run mutmut show <id>` で確認し、"
            "テストを補強してください(等価変異のみ理由付き `# pragma: no mutate`)。",
            file=sys.stderr,
        )
    return ok
```

`main()` を差し替え:

```python
def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "quick"
    if stage == "mutation":
        sys.exit(0 if run_mutation() else 1)
    if stage == "all":
        sys.exit(0 if (run_stage("quick") and run_mutation()) else 1)
    if stage not in STAGES:
        raise SystemExit(
            f"unknown stage: {stage} (available: {', '.join(STAGES)}, mutation, all)"
        )
    sys.exit(0 if run_stage(stage) else 1)
```

モジュール docstring の 2〜3 行目に `mutation`(mutmut → ファイル別 score → baseline ラチェット)と `all` の説明を 1 行ずつ足す。

- [ ] **Step 4: 通ることを確認・mutation 確認**

Run: `uv run pytest tests/test_verify.py -q`
Expected: 全件 PASS(既存 13 + 新規 11)

受け入れ条件: `check_mutation_baseline` の `scores[f]["score"] < b` を `<=` にして `test_mutation_improvement_updates_only_that_file`(または同点が fail になり他のテスト)が落ちること、`MUTATION_KILLED_CODES` から `3` を外して `test_mutation_scores_counts_killed_codes_per_file` が落ちることを確認し、**両方とも元に戻す**。

- [ ] **Step 5: 実 baseline の初回登録**

Run: `uv run python scripts/verify.py mutation; echo "exit=$?"; cat .loop/mutation-baseline.json; git status --short`
Expected: `exit=0`、5 ファイルの score が表示され(スパイク値の近傍: config ≈77 / hook_io ≈81 / patterns ≈83 / scanners ≈87 / bash_guard ≈66)、`.loop/mutation-baseline.json` が `"files"` に 5 件を持つ。`git status` に `.loop/mutation-baseline.json` が **untracked として現れる**(追跡対象)

- [ ] **Step 6: quick・lint**

Run: `uv run python scripts/verify.py quick; echo "exit=$?"`
Expected: `exit=0`

- [ ] **Step 7: Commit**

```bash
git add scripts/verify.py tests/test_verify.py .loop/mutation-baseline.json
git commit -m "feat(verify): mutation ステージ(mutmut→ファイル別scoreのbaselineラチェット)と all ステージを追加、初回 baseline を登録"
```

---

### Task 4: ドキュメント(CONTRIBUTING・dogfooding・第1段階 spec §11)

**Files:**
- Modify: `CONTRIBUTING.md`(検証ゲート節の箇条書き、PR 前チェックリスト)
- Modify: `.claude/rules/dogfooding.md`(項目 3)
- Modify: `docs/superpowers/specs/2026-08-22-loop-engineering-phase1-design.md:197`

- [ ] **Step 1: CONTRIBUTING.md 検証ゲート節の箇条書きに追記**

`- 手動で回すとき: …` の行の直後に追加:

```markdown
- **テストを書いた/変えたタスクの完了条件**: `uv run python scripts/verify.py mutation`(mutmut でファイル別 mutation score を計測し、`.loop/mutation-baseline.json` を下回ると fail。上回れば自動更新。baseline は Git 追跡で PR の diff に出る)。対象は `pyproject.toml` `[tool.mutmut] only_mutate`。生き残りは `uv run mutmut results` / `uv run mutmut show <id>` で読み、厳密な期待値のテストで仕留める。真の等価変異のみ `# pragma: no mutate` を行単位で付け、理由をコメントする
- コミット前: `uv run python scripts/verify.py all`(quick → mutation)
```

- [ ] **Step 2: PR 前チェックリストに 1 行**

`- [ ] \`uv run python scripts/verify.py quick\` が通る…` の直後に追加:

```markdown
- [ ] テストを追加・変更した場合、`uv run python scripts/verify.py mutation` が通る(baseline を下回らない。等価変異の `# pragma: no mutate` には理由コメントがある)
```

- [ ] **Step 3: dogfooding.md 項目 3 の保護対象に baseline を足す**

項目 3 本文の `\`.loop-hooks.json\`・\`.loop/state.json\` は` を `\`.loop-hooks.json\`・\`.loop/state.json\`・\`.loop/mutation-baseline.json\` は` に変更し、同段落末尾(「…最終的な判定者は CI とブランチレビュー(人間)。」の直後)に次の文を追加:

```markdown
mutation の baseline は `scripts/verify.py mutation` 自身が向上時に書き換える(それ以外の経路で下げない)。対象ファイルを `only_mutate` から外すと baseline との不一致で fail するので、対象の縮小はユーザーが baseline も手で外す。
```

- [ ] **Step 4: 第1段階 spec §11 の第2段階行を更新**

```markdown
- **第2段階 mutation 自動化**: mutmut のスパイク → `verify.py mutation` ステージ + `.loop/mutation-baseline.json` のラチェット(下回ったら fail。Stop には入れない。テストを書いたタスクの完了条件にする)。
```
→
```markdown
- **第2段階 mutation 自動化** → **着手済み(2026-08-22)**: `2026-08-22-loop-engineering-phase2-mutation-design.md` を参照(mutmut、ファイル別 score のラチェット、baseline は Git 追跡、対象は lib 4本 + bash_guard)。以下は当初の想定: mutmut のスパイク → `verify.py mutation` ステージ + `.loop/mutation-baseline.json` のラチェット(下回ったら fail。Stop には入れない。テストを書いたタスクの完了条件にする)。
```

- [ ] **Step 5: リークチェックと Commit**

Run: `uv run python scripts/verify.py quick; echo "exit=$?"`
Expected: `exit=0`

```bash
git add CONTRIBUTING.md .claude/rules/dogfooding.md docs/superpowers/specs/2026-08-22-loop-engineering-phase1-design.md
git commit -m "docs: mutation ゲートの運用(タスク完了条件・all・pragma 規約・baseline 保護)を CONTRIBUTING と規約に追記"
```

---

### Task 5〜9: 生き残りのトリアージ(ファイルごとに 1 タスク)

対象と順序: **Task 5** `hooks/lib/patterns.py`(`tests/test_patterns.py`) → **Task 6** `hooks/lib/hook_io.py`(`tests/test_hook_io.py`) → **Task 7** `hooks/lib/scanners.py`(`tests/test_scanners.py`) → **Task 8** `hooks/lib/config.py`(`tests/test_config.py`) → **Task 9** `hooks/pre_tool_use/bash_guard.py`(`tests/test_bash_guard.py`)。

各タスクの手順は同一(以下を「対象ファイル」「テストファイル」「モジュール名」を置き換えて実行する。モジュール名は `hooks.lib.patterns` / `hooks.lib.hook_io` / `hooks.lib.scanners` / `hooks.lib.config` / `hooks.pre_tool_use.bash_guard`)。

**Files:**
- Modify: テストファイル(テスト追加。Edit 可)
- Modify: 対象ファイル(理由付き `# pragma: no mutate` のみ。**write_protected なので Bash 経由の python 書込**)
- Modify: `.loop/mutation-baseline.json`(verify.py が更新)、`docs/superpowers/specs/2026-08-22-mutation-spike-results.md`(結果の追記)

**Interfaces:**
- Consumes: Task 3 の `verify.py mutation`、Task 2 の mutmut 設定
- Produces: 対象ファイルの baseline 値(次タスクは触らない)

- [ ] **Step 1: 現状 score と生き残り一覧**

Run: `uv run python scripts/verify.py mutation; echo "exit=$?"`
Expected: `exit=0`(前タスクまでの baseline と同点以上)。対象ファイルの `score (killed/total)` を控える

```bash
S=<scratchpad>/survivors-<name>.txt   # リポジトリ外(scratchpad)に置く
uv run mutmut results 2>/dev/null | grep "<モジュール名>\." | grep -v ": killed" > "$S.list"
wc -l "$S.list"
for m in $(awk -F: '{print $1}' "$S.list"); do echo "=== $m"; uv run mutmut show "$m" 2>/dev/null | grep -E "^[-+][^-+]"; done > "$S"
```

`$S` を**全件**読む。変更行(`-`/`+`)だけが並ぶので、正体をパターン分けする(例: 「`in` の部分一致で書かれたテスト」「境界値を見ていない」「関数を呼ぶテストが無い(no tests)」「真の等価変異」)

- [ ] **Step 2: パターンごとにテストを追加(テストファイルに Edit)**

原則: **厳密な期待値**で書く(等価比較・部分一致・「例外が出ること」だけ、は穴になる)。境界(空文字・末尾スラッシュ・大文字小文字・Unicode・None)を明示する。`no tests` の関数(`main` 等)は `tests/helpers.load_hook` で読み込み、`monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))` と `capsys` で `main()` を直接呼ぶ(既存テストの流儀に合わせる)。
各パターンを潰すごとに:

Run: `uv run pytest <テストファイル> -q && uv run mutmut run 2>&1 | tr '\r' '\n' | grep -E "mutations/second" && uv run python scripts/verify.py mutation`
Expected: テスト PASS、対象ファイルの score が上がる(mutmut は関数ハッシュで増分実行する)

- [ ] **Step 3: 真の等価変異だけ `# pragma: no mutate`**

判定基準: 変異後のコードが**あらゆる入力**で元と同じ挙動になる(例: エンコーディング名の大小、`rstrip(None)` と `rstrip("/")` が同値になる入力しか来ない、は後者に該当しない — 入力次第で違うなら**テストで仕留める**)。価値ある変異と同居する行は先に文を分割する。書込は Bash 経由:

```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("hooks/lib/<name>.py")
s = p.read_text(encoding="utf-8")
old = '<元の行(完全一致)>'
new = '<元の行>  # pragma: no mutate  (<理由: なぜ等価か>)'
assert s.count(old) == 1, "対象行が一意でない"
p.write_text(s.replace(old, new), encoding="utf-8")
print("ok")
EOF
```

- [ ] **Step 4: 到達確認**

Run: `uv run mutmut run 2>&1 | tr '\r' '\n' | grep -E "mutations/second"; uv run python scripts/verify.py mutation; echo "exit=$?"; uv run mutmut results 2>/dev/null | grep "<モジュール名>\." | grep -v ": killed"`
Expected: `exit=0`、対象ファイルの score は **100 か、残りが全件「理由を説明できる」状態**(Step 5 に記録)。`.loop/mutation-baseline.json` の対象ファイル値が更新されている。

Run: `uv run python scripts/verify.py quick; echo "exit=$?"`
Expected: `exit=0`(ruff・pytest・leak)

- [ ] **Step 5: スパイク文書に結果を追記**

`docs/superpowers/specs/2026-08-22-mutation-spike-results.md` 末尾の「第2段階本体に持ち越すもの」の**前**に節を追加(最初のタスクが `## トリアージの結果(2026-08-22 追記)` 見出しを作り、以降のタスクは同じ節に行を足す):

```markdown
## トリアージの結果(2026-08-22 追記)

| ファイル | 変異 | 初回 score | 最終 score | 補強したテスト(パターン) | pragma(理由) | 残り(正体) |
|---|---|---|---|---|---|---|
| `hooks/lib/<name>.py` | <N> | <x> | <y> | <パターンを 1 行で> | <件数と理由の要約、無ければ 0> | <件数と正体、無ければ 0> |

**教訓**(このファイルで見つかった、一般化できるもの): …(無ければ「特になし」)
```

`bash_guard` でガードの**挙動変更が必要**と判明した穴(テスト追加では塞げない)があれば、この表の「残り」に `要ガード変更: <内容>` と書き、コードは変えない(`.claude/rules/guard-rule-changes.md` に従い別途ユーザー確認)。

- [ ] **Step 6: Commit**

```bash
git add <テストファイル> <対象ファイル> .loop/mutation-baseline.json docs/superpowers/specs/2026-08-22-mutation-spike-results.md
git commit -m "test(<name>): mutation 生き残りを補強(score <x>→<y>)"
```

---

### Task 10: 全体確認と引き渡し

**Files:** なし(確認と報告のみ)

- [ ] **Step 1: ブランチ全体の検証**

Run: `uv run python scripts/verify.py all; echo "exit=$?"; cat .loop/mutation-baseline.json; git status --short; git log --oneline main..HEAD`
Expected: `exit=0`、baseline 5 ファイル、working tree clean(`docs/superpowers/specs/2026-07-26-project-config-trust-optin-design.md` は別作業の untracked なので放置)

- [ ] **Step 2: ユーザー手動作業を報告に含める**

`.claude-hooks.json` の `secrets_guard.write_protected_paths` に `"*.loop/mutation-baseline.json"` を追加(第1段階の 2 件が未実施なら合わせて):

```json
"secrets_guard": {
  "write_protected_paths": [".loop-hooks.json", "*.loop/state.json", "*.loop/mutation-baseline.json"]
}
```

確認: `python3 -c "import json; json.load(open('.claude-hooks.json'))"`

- [ ] **Step 3: 次の拡大候補を報告に含める**

`only_mutate` に `hooks/pre_tool_use/secrets_guard.py`・`exfil_guard.py` → `post_tool_use/*` を 1 本ずつ足して Task 5〜9 と同じ手順でトリアージする(各 1 タスク)。

---

## Self-Review

- **Spec coverage**: §3 Task 0 → Task 1(安全手順 Step 5/6 含む)/ §4 mutmut 設定 → Task 2 / §5.1–5.3 ステージ → Task 3 / §5.4 ハーネステスト → Task 3 Step 1・4 / §6 Git・保護 → Task 2 Step 3 + Task 10 Step 2(手動)/ §7 トリアージ → Task 5〜9 / §8 ドキュメント → Task 4、結果記録は Task 5〜9 Step 5 / §9・§10 はタスク無し(作らない・リスク)
- **Placeholder scan**: Task 5〜9 の `<name>`・`<モジュール名>` 等はファイル別に置き換える指示つきのテンプレート変数であり、値は冒頭に列挙済み。他に TBD 無し
- **Type consistency**: `mutation_scores(repo_root) -> dict[str, dict]`・`check_mutation_baseline(repo_root, scores) -> (bool, list[str])`・`run_mutation(repo_root, runner)`・`MUTATION_KILLED_CODES`・`BASELINE_REL` を Task 3 のテストと実装で同名使用。`_append_evidence(repo_root, stage, ok, checks)` は既存シグネチャ。Task 1 の `load_hook(relpath)` の呼び出し形(`"pre_tool_use/bash_guard.py"`)は既存テストと同じ
