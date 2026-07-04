import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(rel):
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def test_plugin_manifest():
    m = _load(".claude-plugin/plugin.json")
    assert m["name"] == "safe-dev-hooks"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["version"])


def test_hooks_json_references_existing_scripts():
    h = _load("hooks/hooks.json")
    for event, entries in h["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["type"] == "command"
                m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/(\S+?\.py)", hook["command"])
                assert m, hook["command"]
                assert (REPO / m.group(1)).is_file(), m.group(1)


def test_hooks_json_wires_all_events():
    h = _load("hooks/hooks.json")
    assert set(h["hooks"]) == {
        "PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "Stop", "Notification",
    }


def test_marketplace_manifest():
    m = _load("marketplace.json")
    assert m["plugins"][0]["name"] == "safe-dev-hooks"


def test_examples_are_valid_json():
    full = _load("examples/settings.full.json")
    minimal = _load("examples/settings.minimal.json")
    assert "hooks" in full and "hooks" in minimal
    # minimal は bash_guard / secrets_guard のみ
    assert set(minimal["hooks"]) == {"PreToolUse"}


def test_all_rules_json_parse_and_regex_compile():
    for path in (REPO / "rules").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else []
        for item in items:
            assert "name" in item and "regex" in item, path.name
            re.compile(item["regex"])
