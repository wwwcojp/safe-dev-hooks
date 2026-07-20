import io
import json

import pytest
from helpers import load_hook
from lib import config

config_guard = load_hook("config_change/config_guard.py")


def _run(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        config_guard.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(config_guard, "_USER_SETTINGS", tmp_path / "user-settings.json")


def test_notifies_on_config_change(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    event = {"hook_event_name": "ConfigChange", "source": "project_settings",
             "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert out is not None
    assert "設定ファイルが変更されました" in out["systemMessage"]
    assert "project_settings" in out["systemMessage"]


def test_unknown_source_still_notifies(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    event = {"hook_event_name": "ConfigChange", "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert "不明" in out["systemMessage"]


def test_warns_when_disable_all_hooks_set(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"disableAllHooks": true}', encoding="utf-8")
    event = {"hook_event_name": "ConfigChange", "source": "project_settings",
             "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert "disableAllHooks" in out["systemMessage"]


def test_no_disable_warning_without_flag(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"disableAllHooks": false}', encoding="utf-8")
    event = {"hook_event_name": "ConfigChange", "source": "project_settings",
             "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert "disableAllHooks" not in out["systemMessage"]


def test_enabled_false_disables(monkeypatch, tmp_path, capsys):
    # config_guard は警告専用(deny層ではない)ため enabled:false で完全に無効化できる
    _isolate(monkeypatch, tmp_path)
    (tmp_path / ".claude-hooks.json").write_text(
        '{"config_guard": {"enabled": false}}', encoding="utf-8"
    )
    event = {"hook_event_name": "ConfigChange", "source": "user_settings",
             "cwd": str(tmp_path)}
    assert _run(monkeypatch, event, capsys) is None


def test_broken_settings_json_ignored(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{broken", encoding="utf-8")
    event = {"hook_event_name": "ConfigChange", "source": "project_settings",
             "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert "設定ファイルが変更されました" in out["systemMessage"]
