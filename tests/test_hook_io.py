import io
import json

import pytest

from hooks.lib import hook_io


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
    assert out == {
        "decision": "block",
        "reason": "直してください",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "詳細",
        },
    }


def test_post_block_without_context_omits_hook_specific_output():
    out = hook_io.post_block("直してください")
    assert out == {"decision": "block", "reason": "直してください"}


def test_emit_writes_json_line_without_ascii_escaping(capsys):
    hook_io.emit({"msg": "日本語"})
    captured = capsys.readouterr().out
    assert captured == json.dumps({"msg": "日本語"}, ensure_ascii=False) + "\n"
    # ensure_ascii=False の非エスケープを厳密に確認(\u エスケープが残っていない)
    assert "\\u" not in captured
    assert "日本語" in captured


def test_fail_open_emits_message_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        hook_io.fail_open("bash_guard", RuntimeError("boom"))
    assert e.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "systemMessage": (
            "[safe-dev-hooks] bash_guard が異常終了したため検査をスキップしました: boom"
        )
    }


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


def test_finalize_preserves_existing_system_message(capsys):
    # 既存の systemMessage を設定エラー通知で握りつぶさず、両方残す
    with pytest.raises(SystemExit):
        hook_io.finalize({"systemMessage": "既存の注記"}, {"_errors": ["broken.json"]})
    out = json.loads(capsys.readouterr().out)
    assert "既存の注記" in out["systemMessage"]
    assert "broken.json" in out["systemMessage"]


def test_finalize_config_error_message_exact_text(capsys):
    # プレフィックス文言と "; " 区切りを厳密に固定する
    with pytest.raises(SystemExit):
        hook_io.finalize(None, {"_errors": ["a.json", "b.json"]})
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "systemMessage": (
            "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: a.json; b.json"
        )
    }


def test_finalize_appends_notices_after_errors(capsys):
    with pytest.raises(SystemExit) as e:
        hook_io.finalize(None, {"_errors": ["a.json"], "_notices": ["N1", "N2"]})
    assert e.value.code == 0
    assert json.loads(capsys.readouterr().out) == {
        "systemMessage": (
            "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: a.json\nN1\nN2"
        )
    }


def test_finalize_notices_only(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize({"decision": "block", "reason": "x"}, {"_notices": ["N1"]})
    assert json.loads(capsys.readouterr().out) == {
        "decision": "block", "reason": "x", "systemMessage": "N1"
    }


def test_finalize_quiet_notices_suppresses_only_notices(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize(None, {"_errors": ["a.json"], "_notices": ["N1"]}, quiet_notices=True)
    assert json.loads(capsys.readouterr().out) == {
        "systemMessage": "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: a.json"
    }
    with pytest.raises(SystemExit):
        hook_io.finalize(None, {"_notices": ["N1"]}, quiet_notices=True)
    assert capsys.readouterr().out == ""


def test_finalize_preserves_existing_system_message_with_notices(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize({"systemMessage": "既存"}, {"_notices": ["N1"]})
    assert json.loads(capsys.readouterr().out) == {"systemMessage": "既存\nN1"}
