#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""MCP/WebFetch/WebSearch の応答に含まれるシークレット・PIIを検出し、警告またはマスキングする。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402

BUILTIN_TARGETS = ("WebFetch", "WebSearch")


def is_target(tool_name: str) -> bool:
    return tool_name.startswith("mcp__") or tool_name in BUILTIN_TARGETS


def evaluate(output_text: str, cfg: dict) -> list:
    rules = list(patterns.load_rules("secret_patterns.json")) + list(
        patterns.load_rules("pii_patterns.json")
    )
    return patterns.scan_text(output_text, rules)


def build_output(findings: list, raw_output, cfg: dict) -> dict | None:
    if not findings:
        return None
    names = ", ".join(sorted({f["rule"] for f in findings}))
    if cfg.get("action") == "redact" and isinstance(raw_output, str):
        updated = raw_output
        for f in findings:
            updated = updated.replace(f["match"], f"[REDACTED:{f['rule']}]")
        return {
            "systemMessage": f"[safe-dev-hooks] 外部応答内の機微情報をマスキングしました: {names}",
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": updated,
            },
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"[safe-dev-hooks] この外部応答には機微情報の可能性があります({names})。"
                "この値をファイル・コミット・別の外部ツールへ転記しないでください。"
            ),
        }
    }


def main() -> None:
    event = hook_io.read_event()
    if not is_target(event.get("tool_name", "")):
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("exfil_output_scan", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    raw = event.get("tool_output", event.get("tool_response", ""))
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    try:
        findings = evaluate(text, cfg)
        out = build_output(findings, raw, cfg)
    except Exception as exc:
        hook_io.fail_open("exfil_output_scan", exc)
        return
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
