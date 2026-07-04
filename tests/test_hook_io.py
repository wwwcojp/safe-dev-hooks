import io
import json

import pytest
from lib import hook_io


def test_read_event_parses_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"tool_name": "Bash"}'))
    assert hook_io.read_event() == {"tool_name": "Bash"}


def test_read_event_returns_empty_on_broken_json(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("not-json"))
    assert hook_io.read_event() == {}


def test_pre_tool_decision_shape():
    out = hook_io.pre_tool_decision("deny", "理由")
    assert out == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "理由",
        }
    }


def test_post_block_shape():
    out = hook_io.post_block("直してください", context="詳細")
    assert out["decision"] == "block"
    assert out["reason"] == "直してください"
    assert out["hookSpecificOutput"]["additionalContext"] == "詳細"


def test_finalize_emits_and_exits(capsys):
    with pytest.raises(SystemExit) as e:
        hook_io.finalize({"decision": "block", "reason": "x"}, {})
    assert e.value.code == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_finalize_appends_config_errors(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize(None, {"_errors": ["broken.json"]})
    out = json.loads(capsys.readouterr().out)
    assert "broken.json" in out["systemMessage"]


def test_finalize_silent_when_nothing(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize(None, {})
    assert capsys.readouterr().out == ""
