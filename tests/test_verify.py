"""scripts/verify.py(検証ランナー)のテスト。

実 uv/ruff/pytest は呼ばず、ダミーコマンドを注入する。
"""
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
