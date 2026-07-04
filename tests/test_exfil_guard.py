import io
import json

import pytest

from helpers import load_hook
from lib import config

exfil_guard = load_hook("pre_tool_use/exfil_guard.py")


def _cfg(**over):
    import copy
    cfg = copy.deepcopy(config.DEFAULTS["exfil_guard"])
    for k, v in over.items():
        if isinstance(v, dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    return cfg


def test_is_target():
    assert exfil_guard.is_target("mcp__github__create_issue")
    assert exfil_guard.is_target("WebFetch")
    assert exfil_guard.is_target("WebSearch")
    assert not exfil_guard.is_target("Bash")
    assert not exfil_guard.is_target("Edit")


def test_server_prefix():
    assert exfil_guard.server_prefix("mcp__internal-kb__search") == "mcp__internal-kb"


def test_credentials_default_deny():
    v = exfil_guard.evaluate("query with AKIAIOSFODNN7EXAMPLE", _cfg())
    assert v["decision"] == "deny"
    assert "AKIAIOSFODNN7EXAMPLE" not in v["reason"]  # 値そのものは理由に出さない


def test_pii_default_ask():
    v = exfil_guard.evaluate("連絡先は taro@example.co.jp です", _cfg())
    assert v["decision"] == "ask"


def test_confidential_marker_ask():
    v = exfil_guard.evaluate("この資料は社外秘です", _cfg())
    assert v["decision"] == "ask"


def test_custom_pattern():
    cfg = _cfg(custom_patterns=[{"name": "internal-domain", "regex": "[\\w.-]+\\.corp\\.example\\.jp"}])
    v = exfil_guard.evaluate("http://wiki.corp.example.jp/page", cfg)
    assert v["decision"] == "ask"
    assert "internal-domain" in v["reason"]


def test_category_off_disables():
    cfg = _cfg(categories={"pii": "off"})
    assert exfil_guard.evaluate("taro@example.co.jp", cfg) is None


def test_clean_payload_passes():
    assert exfil_guard.evaluate("普通の検索クエリ python asyncio", _cfg()) is None


def _run_main(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        exfil_guard.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_main_always_mode_asks_everything(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8"
    )
    event = {
        "tool_name": "mcp__foo__bar",
        "cwd": str(tmp_path),
        "tool_input": {"q": "安全な内容"},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_main_always_mode_deny_not_downgraded(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8"
    )
    event = {
        "tool_name": "mcp__foo__bar",
        "cwd": str(tmp_path),
        "tool_input": {"q": "AKIAIOSFODNN7EXAMPLE"},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_trusted_server_skipped_even_in_always(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always", "trusted_servers": ["mcp__foo"]}}),
        encoding="utf-8",
    )
    event = {
        "tool_name": "mcp__foo__bar",
        "cwd": str(tmp_path),
        "tool_input": {"q": "AKIAIOSFODNN7EXAMPLE"},
    }
    assert _run_main(monkeypatch, event, capsys) is None
