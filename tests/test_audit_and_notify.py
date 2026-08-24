import io
import json

import pytest
from helpers import approve_project, load_hook

from hooks.lib import config

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
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"audit_log": {"path": "/proc/forbidden"}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(tmp_path)}
    _run(audit, monkeypatch, event, capsys)  # SystemExit(0) すれば成功


def test_audit_survives_non_dict_section(monkeypatch, tmp_path, capsys):
    (tmp_path / ".claude-hooks.json").write_text('{"audit_log": true}', encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(tmp_path)}
    out = _run(audit, monkeypatch, event, capsys)
    assert out is not None and "systemMessage" in out


def test_audit_log_does_not_emit_trust_notices(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text('{"notify": {"method": "bell"}}', encoding="utf-8")
    event = {"hook_event_name": "Stop", "cwd": str(tmp_path), "session_id": "s"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit) as e:
        audit.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "未承認" not in out


def test_audit_log_does_not_emit_skipped_project_notice(monkeypatch, capsys, tmp_path):
    """D2 の「読まなかったプロジェクト設定」通知も quiet_notices=True で出ない。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".claude-hooks.json").write_text('{"notify": {"method": "bell"}}', encoding="utf-8")
    event = {"hook_event_name": "Stop", "cwd": str(cwd), "session_id": "s"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit) as e:
        audit.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "基準ディレクトリ" not in out


def test_audit_log_anchors_to_project_root_via_env(monkeypatch, tmp_path, capsys):
    """CLAUDE_PROJECT_DIR が設定されているとき、logs は
    CLAUDE_PROJECT_DIR/.claude/logs/ に出る。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    project_root = tmp_path / "project_root"
    project_root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_root))
    cwd_sub = project_root / "subdir" / "another"
    cwd_sub.mkdir(parents=True)
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Bash",
        "cwd": str(cwd_sub),
        "tool_input": {"command": "ls"},
    }
    _run(audit, monkeypatch, event, capsys)
    # Logs should be in project_root/.claude/logs/, not cwd_sub/.claude/logs/
    files = list((project_root / ".claude" / "logs").glob("audit-*.jsonl"))
    assert len(files) == 1
    assert not (cwd_sub / ".claude" / "logs").exists()
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["tool_name"] == "Bash"


def test_audit_log_anchors_to_git_root(monkeypatch, tmp_path, capsys):
    """CLAUDE_PROJECT_DIR 未設定でも、祖先に .git があればそこが基準になる。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    git_root = tmp_path / "git_project"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    cwd_sub = git_root / "subdir" / "deep"
    cwd_sub.mkdir(parents=True)
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Bash",
        "cwd": str(cwd_sub),
        "tool_input": {"command": "ls"},
    }
    _run(audit, monkeypatch, event, capsys)
    # Logs should be in git_root/.claude/logs/, not cwd_sub/.claude/logs/
    files = list((git_root / ".claude" / "logs").glob("audit-*.jsonl"))
    assert len(files) == 1
    assert not (cwd_sub / ".claude" / "logs").exists()
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["tool_name"] == "Bash"


def test_audit_log_uses_cwd_when_no_project_root(monkeypatch, tmp_path, capsys):
    """CLAUDE_PROJECT_DIR なく .git も無ければ cwd を基準にする(既存挙動の保持)。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cwd = tmp_path / "isolated"
    cwd.mkdir()
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Bash",
        "cwd": str(cwd),
        "tool_input": {"command": "ls"},
    }
    _run(audit, monkeypatch, event, capsys)
    files = list((cwd / ".claude" / "logs").glob("audit-*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["tool_name"] == "Bash"


def test_audit_log_preserves_absolute_path(monkeypatch, tmp_path, capsys):
    """audit_log.path に絶対パスを指定した場合は従来どおりそのまま使う。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    abs_log_dir = tmp_path / "custom_logs"
    abs_log_dir.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_root))
    cwd = project_root / "sub"
    cwd.mkdir()
    (project_root / ".claude-hooks.json").write_text(
        json.dumps({"audit_log": {"path": str(abs_log_dir)}}), encoding="utf-8"
    )
    approve_project(monkeypatch, project_root / "global.json", project_root)
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Bash",
        "cwd": str(cwd),
        "tool_input": {"command": "ls"},
    }
    _run(audit, monkeypatch, event, capsys)
    # Logs should be in abs_log_dir, not project_root/.claude/logs/
    files = list(abs_log_dir.glob("audit-*.jsonl"))
    assert len(files) == 1
    assert not (project_root / ".claude" / "logs").exists()
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["tool_name"] == "Bash"


def test_notify_default_auto_falls_back_to_bell(monkeypatch, tmp_path, capsys):
    """既定(auto)でデスクトップ通知が全滅した場合はベルへフォールバックする。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(notify, "_notify_desktop", lambda msg: False)
    event = {
        "hook_event_name": "Notification",
        "cwd": str(tmp_path),
        "notification_type": "permission_prompt",
        "message": "許可待ち",
    }
    out = _run(notify, monkeypatch, event, capsys)
    assert out["terminalSequence"] == "\u0007"


def test_notify_auto_desktop_success_outputs_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    received = []
    monkeypatch.setattr(notify, "_notify_desktop", lambda msg: received.append(msg) or True)
    event = {"hook_event_name": "Notification", "cwd": str(tmp_path), "message": "許可待ち"}
    out = _run(notify, monkeypatch, event, capsys)
    assert out is None
    assert received == ["許可待ち"]


def test_notify_method_bell_skips_desktop(monkeypatch, tmp_path, capsys):
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "bell"}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)

    def _boom(msg):
        raise AssertionError("method=bellではデスクトップチェーンを呼ばない")

    monkeypatch.setattr(notify, "_notify_desktop", _boom)
    event = {"hook_event_name": "Notification", "cwd": str(tmp_path), "message": "m"}
    out = _run(notify, monkeypatch, event, capsys)
    assert out["terminalSequence"] == "\u0007"


def test_notify_command_skips_desktop(monkeypatch, tmp_path, capsys):
    """notify.command設定時はmethodに関わらずコマンドが最優先(互換性)。"""
    marker = tmp_path / "notified.txt"
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"command": f"touch {marker}"}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)

    def _boom(msg):
        raise AssertionError("command設定時はデスクトップチェーンを呼ばない")

    monkeypatch.setattr(notify, "_notify_desktop", _boom)
    event = {"hook_event_name": "Notification", "cwd": str(tmp_path), "message": "done"}
    out = _run(notify, monkeypatch, event, capsys)
    assert marker.exists()
    assert out is None


def test_notify_disabled_outputs_nothing(monkeypatch, tmp_path, capsys):
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"enabled": False}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)

    def _boom(msg):
        raise AssertionError("enabled=falseでは何も実行しない")

    monkeypatch.setattr(notify, "_notify_desktop", _boom)
    event = {"hook_event_name": "Notification", "cwd": str(tmp_path), "message": "m"}
    out = _run(notify, monkeypatch, event, capsys)
    assert out is None


def test_notify_custom_command(monkeypatch, tmp_path, capsys):
    marker = tmp_path / "notified.txt"
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"command": f"touch {marker}"}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
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


def test_desktop_chain_wsl_without_powershell_falls_through(monkeypatch):
    """WSL判定がTrueでもpowershell.exeが無ければトーストを飛ばしnotify-sendへ進む。"""
    calls = []
    monkeypatch.setattr(notify, "_is_wsl", lambda: True)
    monkeypatch.setattr(
        notify.shutil,
        "which",
        lambda name: None if name == "powershell.exe" else f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        notify, "_notify_windows_toast", lambda t, m: calls.append("toast") or True
    )
    monkeypatch.setattr(
        notify, "_notify_notify_send", lambda t, m: calls.append("notify-send") or True
    )
    assert notify._notify_desktop("m") is True
    assert calls == ["notify-send"]


def test_desktop_chain_all_unavailable(monkeypatch):
    monkeypatch.setattr(notify, "_is_wsl", lambda: False)
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    assert notify._notify_desktop("m") is False
