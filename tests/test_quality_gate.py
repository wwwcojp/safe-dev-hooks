import io
import json
import shlex

import pytest

from helpers import load_hook
from lib import config

qg = load_hook("post_tool_use/quality_gate.py")


def test_resolve_commands_from_config(tmp_path):
    cfg = {"commands": {"*.py": ["mylint {file}"]}}
    got = qg.resolve_commands(str(tmp_path / "app.py"), cfg, str(tmp_path))
    assert got == [f"mylint {tmp_path / 'app.py'}"]


def test_resolve_commands_no_match(tmp_path):
    cfg = {"commands": {"*.py": ["mylint {file}"]}}
    assert qg.resolve_commands(str(tmp_path / "app.md"), cfg, str(tmp_path)) == []


def test_resolve_commands_quotes_spaced_paths(tmp_path):
    cfg = {"commands": {"*.py": ["mylint {file}"]}}
    spaced = str(tmp_path / "my dir" / "app.py")
    got = qg.resolve_commands(spaced, cfg, str(tmp_path))
    assert got and shlex.split(got[0])[-1] == spaced


def test_autodetect_requires_project_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    assert qg.resolve_commands(str(tmp_path / "a.py"), {"commands": {}}, str(tmp_path)) == []
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    got = qg.resolve_commands(str(tmp_path / "a.py"), {"commands": {}}, str(tmp_path))
    assert got and got[0].startswith("ruff check")


def test_run_checks_collects_failures(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    failures = qg.run_checks([f"python3 -m py_compile {bad}"], str(tmp_path))
    assert len(failures) == 1


def test_run_checks_passes(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1\n", encoding="utf-8")
    assert qg.run_checks([f"python3 -m py_compile {ok}"], str(tmp_path)) == []


def _run_main(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        qg.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_main_block_mode(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {"commands": {"*.py": ["python3 -m py_compile {file}"]}}}),
        encoding="utf-8",
    )
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(bad)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"


def test_main_warn_mode(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {
            "mode": "warn",
            "commands": {"*.py": ["python3 -m py_compile {file}"]},
        }}),
        encoding="utf-8",
    )
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(bad)}}
    out = _run_main(monkeypatch, event, capsys)
    assert "decision" not in out
    assert "additionalContext" in out["hookSpecificOutput"]


def test_main_skips_missing_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "tool_name": "Write", "cwd": str(tmp_path),
        "tool_input": {"file_path": str(tmp_path / "gone.py")},
    }
    assert _run_main(monkeypatch, event, capsys) is None
