#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Bashコマンドの破壊的操作を deny/ask の2段階でガードする。"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402


def _segments(command: str) -> list[str]:
    parts = re.split(r"(?:&&|\|\||;|\n)", command)
    return [p.strip() for p in parts if p.strip()]


def _normalize(text: str) -> str:
    # クォートによるすり抜け対策(過剰検知側に倒す)
    return text.replace('"', "").replace("'", "")


def _force_push_rules(cfg: dict) -> list[dict]:
    branches = cfg.get("protected_branches") or ["main", "master"]
    alt = "|".join(re.escape(b) for b in branches)
    return [
        {"name": "force-push-protected",
         "regex": rf"\bgit\s+push\s+[^;|&]*(--force\b|-f\b)[^;|&]*\b({alt})\b"},
        {"name": "force-push-protected-order",
         "regex": rf"\bgit\s+push\s+[^;|&]*\b({alt})\b[^;|&]*(--force\b|-f\b)"},
        {"name": "force-push-refspec",
         "regex": rf"\bgit\s+push\b[^;|&]*\s\+(?:[^\s:]*:)?(?:[^\s:]*/)?({alt})(?![\w.@:/-])"},
    ]


def evaluate(command: str, cfg: dict) -> dict | None:
    deny_rules = (
        list(patterns.load_rules("bash_deny.json"))
        + _force_push_rules(cfg)
        + [{"name": f"extra_deny:{p}", "regex": p} for p in cfg.get("extra_deny", [])]
    )
    ask_rules = list(patterns.load_rules("bash_ask.json")) + [
        {"name": f"extra_ask:{p}", "regex": p} for p in cfg.get("extra_ask", [])
    ]
    allow = cfg.get("allow", [])
    targets = [_normalize(s) for s in _segments(command)] + [_normalize(command)]
    for rule in deny_rules:
        if any(re.search(rule["regex"], t) for t in targets):
            return {
                "decision": "deny",
                "reason": f"破壊的コマンドを検出: {rule['name']}(deny層は設定で解除できません)",
            }
    for rule in ask_rules:
        for t in targets:
            if re.search(rule["regex"], t) and not any(re.search(a, t) for a in allow):
                return {
                    "decision": "ask",
                    "reason": (
                        f"注意が必要なコマンドを検出: {rule['name']}。"
                        "実行してよいか確認してください"
                    ),
                }
    return None


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") != "Bash":
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("bash_guard", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    command = (event.get("tool_input") or {}).get("command", "")
    try:
        verdict = evaluate(command, cfg)
    except Exception as exc:  # deny層の判定不能は安全側に倒す(fail-close)
        hook_io.finalize(
            hook_io.pre_tool_decision(
                "ask",
                f"bash_guard の判定に失敗したため確認してください: {exc}",
            ),
            cfg_all,
        )
        return
    out = hook_io.pre_tool_decision(verdict["decision"], verdict["reason"]) if verdict else None
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
