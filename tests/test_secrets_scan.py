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
