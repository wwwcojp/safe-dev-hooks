import io
import json
import os
import shlex

import pytest
from helpers import approve_project, load_hook

from hooks.lib import config

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


# ---- プロジェクトルートの基準差し替え(project_root) ----


def test_autodetect_finds_marker_from_project_root_in_subdirectory(tmp_path, monkeypatch):
    """回帰: cwd がサブディレクトリでもプロジェクトルートのマーカーファイルで検出する。"""
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    got = qg.resolve_commands(str(sub / "app.py"), {"commands": {}}, str(sub))
    assert got and got[0].startswith("ruff check")


def test_autodetect_ignores_marker_outside_project_root(tmp_path, monkeypatch):
    """正当な挙動の保持: プロジェクトルート外(無関係な祖先)のマーカーは拾わない。"""
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    got = qg.resolve_commands(str(sub / "app.py"), {"commands": {}}, str(sub))
    assert got == []


def test_run_checks_executes_in_project_root_not_subdirectory(tmp_path):
    """D4: 実行ディレクトリもプロジェクトルート基準になる。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    stub = tmp_path / "record_cwd.py"
    stub.write_text(
        "import os, sys\n"
        "open(sys.argv[1], 'w', encoding='utf-8').write(os.getcwd())\n",
        encoding="utf-8",
    )
    marker = tmp_path / "cwd-marker.txt"
    qg.run_checks([f"python3 {stub} {marker}"], str(sub))
    assert marker.read_text(encoding="utf-8") == os.path.realpath(str(root))


def test_run_checks_uses_cwd_when_no_project_root_found(tmp_path):
    """正当な挙動の保持: git ルートが無ければ従来どおり cwd で実行する。"""
    stub = tmp_path / "record_cwd.py"
    stub.write_text(
        "import os, sys\n"
        "open(sys.argv[1], 'w', encoding='utf-8').write(os.getcwd())\n",
        encoding="utf-8",
    )
    marker = tmp_path / "cwd-marker.txt"
    qg.run_checks([f"python3 {stub} {marker}"], str(tmp_path))
    assert marker.read_text(encoding="utf-8") == os.path.realpath(str(tmp_path))


def _run_main(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        qg.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_main_block_mode(monkeypatch, tmp_path, capsys):
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {"commands": {"*.py": ["python3 -m py_compile {file}"]}}}),
        encoding="utf-8",
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(bad)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"


def test_main_warn_mode(monkeypatch, tmp_path, capsys):
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {
            "mode": "warn",
            "commands": {"*.py": ["python3 -m py_compile {file}"]},
        }}),
        encoding="utf-8",
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
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
