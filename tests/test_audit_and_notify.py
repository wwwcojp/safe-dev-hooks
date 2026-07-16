import io
import json

import pytest
from helpers import load_hook
from lib import config

audit = load_hook("audit/audit_log.py")
notify = load_hook("notification/notify.py")


def _run(mod, monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        mod.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_audit_appends_jsonl(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Bash",
        "cwd": str(tmp_path),
        "tool_input": {"command": "ls"},
    }
    _run(audit, monkeypatch, event, capsys)
    files = list((tmp_path / ".claude" / "logs").glob("audit-*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["tool_name"] == "Bash"
    assert record["event"] == "PreToolUse"
    assert "ts" in record


def test_audit_truncates_large_input(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"content": "x" * 5000},
    }
    _run(audit, monkeypatch, event, capsys)
    files = list((tmp_path / ".claude" / "logs").glob("audit-*.jsonl"))
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert len(record["tool_summary"]) <= 500


def test_audit_never_crashes_on_unwritable_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"audit_log": {"path": "/proc/forbidden"}}), encoding="utf-8"
    )
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(tmp_path)}
    _run(audit, monkeypatch, event, capsys)  # SystemExit(0) すれば成功


def test_audit_survives_non_dict_section(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text('{"audit_log": true}', encoding="utf-8")
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(tmp_path)}
    out = _run(audit, monkeypatch, event, capsys)
    assert out is not None and "systemMessage" in out


def test_notify_default_bell(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "hook_event_name": "Notification",
        "cwd": str(tmp_path),
        "notification_type": "permission_prompt",
        "message": "許可待ち",
    }
    out = _run(notify, monkeypatch, event, capsys)
    assert out["terminalSequence"] == "\u0007"


def test_notify_custom_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    marker = tmp_path / "notified.txt"
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"command": f"touch {marker}"}}), encoding="utf-8"
    )
    event = {
        "hook_event_name": "Notification",
        "cwd": str(tmp_path),
        "message": "done",
    }
    _run(notify, monkeypatch, event, capsys)
    assert marker.exists()


def test_is_wsl_by_env(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert notify._is_wsl() is True


def test_is_wsl_by_proc_version(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    fake = tmp_path / "version"
    fake.write_text("Linux version 6.6.0-Microsoft-standard", encoding="utf-8")
    monkeypatch.setattr(notify, "_PROC_VERSION", fake)
    assert notify._is_wsl() is True


def test_is_wsl_false_on_plain_linux(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    fake = tmp_path / "version"
    fake.write_text("Linux version 6.6.0-generic", encoding="utf-8")
    monkeypatch.setattr(notify, "_PROC_VERSION", fake)
    assert notify._is_wsl() is False


def test_windows_toast_passes_message_via_env(monkeypatch):
    captured = {}

    def fake_run(argv, env=None, capture_output=None, timeout=None):
        captured["argv"] = argv
        captured["env"] = env

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    injected = 'x"; Remove-Item -Recurse $HOME; "'
    assert notify._notify_windows_toast("Claude Code", injected) is True
    assert captured["argv"][0] == "powershell.exe"
    # メッセージは環境変数で渡り、コマンド文字列には埋め込まれない
    assert captured["env"]["NOTIFY_MSG"] == injected
    assert captured["env"]["NOTIFY_TITLE"] == "Claude Code"
    assert captured["env"]["WSLENV"].endswith("NOTIFY_TITLE:NOTIFY_MSG")
    assert "Remove-Item" not in " ".join(captured["argv"])


def test_desktop_chain_order_and_fallthrough(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "_is_wsl", lambda: True)
    monkeypatch.setattr(notify.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        notify, "_notify_windows_toast", lambda t, m: calls.append("toast") or False
    )
    monkeypatch.setattr(
        notify, "_notify_notify_send", lambda t, m: calls.append("notify-send") or True
    )
    monkeypatch.setattr(
        notify, "_notify_osascript", lambda t, m: calls.append("osascript") or True
    )
    assert notify._notify_desktop("m") is True
    # toast失敗後にnotify-sendへ進み、成功したらosascriptは呼ばない
    assert calls == ["toast", "notify-send"]


def test_desktop_chain_all_unavailable(monkeypatch):
    monkeypatch.setattr(notify, "_is_wsl", lambda: False)
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    assert notify._notify_desktop("m") is False
