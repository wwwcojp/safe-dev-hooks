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
