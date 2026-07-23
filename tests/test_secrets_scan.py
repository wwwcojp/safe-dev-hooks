import io
import json

import pytest
from helpers import load_hook
from lib import config

scan = load_hook("post_tool_use/secrets_scan.py")


def test_extract_from_write():
    assert scan.extract_written_text({"content": "abc"}) == "abc"


def test_extract_from_edit():
    assert scan.extract_written_text({"old_string": "x", "new_string": "y"}) == "y"


def _run_main(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        scan.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_blocks_secret_write(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.py", "content": 'KEY = "AKIAIOSFODNN7EXAMPLE"'},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"
    assert "aws-access-key" in out["reason"]
    assert "AKIAIOSFODNN7EXAMPLE" not in out["reason"]


def test_clean_write_passes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.py", "content": "print('hello')"},
    }
    assert _run_main(monkeypatch, event, capsys) is None


def _write_project_config(tmp_path, custom_patterns):
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"secrets_scan": {"custom_patterns": custom_patterns}}),
        encoding="utf-8",
    )


def test_custom_pattern_blocks_write(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    _write_project_config(
        tmp_path, [{"name": "internal-host", "regex": r"\binternal\.example\b"}]
    )
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.md", "content": "接続先: internal.example"},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"
    assert "internal-host" in out["reason"]


def test_custom_pattern_clean_write_passes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    _write_project_config(
        tmp_path, [{"name": "internal-host", "regex": r"\binternal\.example\b"}]
    )
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.md", "content": "接続先: public.example"},
    }
    assert _run_main(monkeypatch, event, capsys) is None


def test_custom_pattern_and_builtin_both_apply(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    _write_project_config(
        tmp_path, [{"name": "internal-host", "regex": r"\binternal\.example\b"}]
    )
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.py", "content": 'KEY = "AKIAIOSFODNN7EXAMPLE"'},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"
    assert "aws-access-key" in out["reason"]


def test_invalid_custom_pattern_fails_open(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    _write_project_config(tmp_path, [{"name": "regex-missing"}])
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.md", "content": "なんでもない内容"},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert "decision" not in (out or {})
    assert "secrets_scan" in out["systemMessage"]


def test_gitleaks_finding_in_block(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(
        scan.scanners, "scan_secrets",
        lambda text, sc, cwd: [{"rule": "gitleaks:generic", "match": "STUB"}],
    )
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.py", "content": "hello world"},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"
    assert "gitleaks:generic" in out["reason"]
