"""scripts/verify.py(検証ランナー)のテスト。

実 uv/ruff/pytest は呼ばず、ダミーコマンドを注入する。
"""
import json
import re
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


def test_main_exit_code_on_pass(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["verify.py"])  # 引数なし → 既定の "quick"
    monkeypatch.setattr(verify, "run_stage", lambda *a, **k: True)
    with pytest.raises(SystemExit) as exc:
        verify.main()
    assert exc.value.code == 0


def test_main_exit_code_on_fail(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["verify.py"])
    monkeypatch.setattr(verify, "run_stage", lambda *a, **k: False)
    with pytest.raises(SystemExit) as exc:
        verify.main()
    assert exc.value.code == 1


def test_error_exit_code_fails_stage_even_with_inverted_ok_codes(tmp_path):
    # git grep 流儀の ok_codes={1} でも、想定外の終了コード(エラー終了の2など)は失敗扱い
    errors2 = verify.Check(
        "leak", [PY, "-c", "import sys; sys.exit(2)"], ok_codes=frozenset({1})
    )

    assert verify.run_stage("quick", [errors2], repo_root=tmp_path) is False


def test_oserror_records_evidence_and_fails(tmp_path, capsys):
    missing = verify.Check("missing", ["/nonexistent/definitely-missing-binary-xyz"])

    ok = verify.run_stage("quick", [missing], repo_root=tmp_path)

    assert ok is False
    (rec,) = _read_evidence(tmp_path)
    assert rec["pass"] is False
    (check,) = rec["checks"]
    assert check == {"name": "missing", "ok": False, "ms": check["ms"]}
    assert "/nonexistent/definitely-missing-binary-xyz" in capsys.readouterr().err


def test_repo_root_default_points_at_repo_root():
    assert verify.REPO_ROOT == Path(__file__).resolve().parent.parent


def _extract_ci_run_steps(ci_text: str) -> list[str]:
    """ci.yml の各ステップの `run:` 本文を、出現順のリストで返す。

    2つの記法に対応する: ブロックスカラー(`run: |` の後、より深い字下げが続く行すべて)と
    単一行スカラー(`run: <コマンド>`)。stdlib(re)のみで書く。
    """
    lines = ci_text.splitlines()
    steps: list[str] = []
    i = 0
    while i < len(lines):
        block_start = re.match(r"^(\s*)run:\s*\|\s*$", lines[i])
        if block_start:
            indent = len(block_start.group(1))
            i += 1
            block_lines = []
            while i < len(lines) and (
                lines[i].strip() == "" or len(lines[i]) - len(lines[i].lstrip()) > indent
            ):
                block_lines.append(lines[i])
                i += 1
            steps.append("\n".join(block_lines))
            continue
        single = re.match(r"^\s*run:\s*(.+)$", lines[i])
        if single:
            steps.append(single.group(1))
        i += 1
    return steps


def test_quick_stage_mirrors_ci():
    """spec §4: quick は CI と同じコマンド・同じ順序。片方を変えたらもう片方も変える。

    片方向(部分文字列が含まれるか)だけの検査だと、CI にステップが増えても・
    フラグが変わっても・順序が入れ替わっても気付けない。CI の `run:` 本文を出現順に
    全数抽出し、quick の3チェックと1対1・順序も一致させることで両方向にする。
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    names = [c.name for c in verify.STAGES["quick"]]

    assert names == ["leak", "lint", "tests"]
    leak, lint, tests = verify.STAGES["quick"]
    assert leak.cmd[:3] == ["git", "grep", "-nP"] and leak.ok_codes == frozenset({1})
    assert lint.cmd == ["uv", "run", "ruff", "check", "hooks", "tests", "scripts"]
    assert tests.cmd == ["uv", "run", "pytest", "-q"]

    run_steps = _extract_ci_run_steps(ci)
    assert len(run_steps) == 3, f"CI の run ステップ数が想定と違う: {run_steps!r}"

    leak_step, lint_step, tests_step = run_steps
    assert f"git grep -nP '{verify.LEAK_REGEX}' --" in leak_step
    assert re.search(r"\bif\b.*\bexit 1\b", leak_step, re.S), leak_step
    assert lint_step.strip() == "uv run ruff check hooks tests scripts"
    assert tests_step.strip() == "uv run pytest -q"
