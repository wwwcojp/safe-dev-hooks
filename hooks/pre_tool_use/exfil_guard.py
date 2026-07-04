#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""外部送信ガード: MCP/WebFetch/WebSearch への引数に含まれる機微情報を検査する。"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402

BUILTIN_TARGETS = ("WebFetch", "WebSearch")


def is_target(tool_name: str) -> bool:
    return tool_name.startswith("mcp__") or tool_name in BUILTIN_TARGETS


def server_prefix(tool_name: str) -> str:
    parts = tool_name.split("__")
    return "__".join(parts[:2]) if len(parts) >= 2 else tool_name


def _mask(value: str) -> str:
    return value[:4] + "…"


def evaluate(payload_text: str, cfg: dict) -> dict | None:
    cats = cfg.get("categories", {})
    findings: list[dict] = []

    def add(category: str, items: list) -> None:
        action = cats.get(category, "ask")
        if action == "off":
            return
        for f in items:
            findings.append({"category": category, "action": action, "rule": f["rule"]})

    add(
        "credentials",
        patterns.scan_text(payload_text, patterns.load_rules("secret_patterns.json")),
    )
    add(
        "pii",
        patterns.scan_text(payload_text, patterns.load_rules("pii_patterns.json")),
    )
    lowered = payload_text.lower()
    markers = [
        m for m in patterns.load_rules("confidential_markers.json")["markers"]
        if m.lower() in lowered
    ]
    add("confidential_markers", [{"rule": m, "match": m} for m in markers])
    add("custom", patterns.scan_text(
        payload_text,
        [{"name": p["name"], "regex": p["regex"]} for p in cfg.get("custom_patterns", [])],
    ))
    if not findings:
        return None
    decision = "deny" if any(f["action"] == "deny" for f in findings) else "ask"
    detail = ", ".join(f"{f['category']}:{f['rule']}" for f in findings[:5])
    return {
        "decision": decision,
        "reason": f"外部送信ペイロードに機微情報の可能性を検出: {detail}",
    }


SEMANTIC_ENV_GUARD = "SAFE_DEV_HOOKS_SEMANTIC"
SEMANTIC_TIMEOUT_SEC = 30
SEMANTIC_MAX_PAYLOAD = 4000


def semantic_check(payload_text: str, cfg: dict) -> dict | None:
    """ヘッドレスClaudeで機微情報の可能性を判定する。判定不能時はNone(fail-open)。"""
    sem = cfg.get("semantic", {})
    if len(payload_text) < sem.get("min_payload_chars", 200):
        return None
    if os.environ.get(SEMANTIC_ENV_GUARD) == "1":  # 再帰防止
        return None
    if shutil.which("claude") is None:
        return None
    template = (patterns.RULES_DIR / "semantic_prompt.md").read_text(encoding="utf-8")
    prompt = template.replace("{payload}", payload_text[:SEMANTIC_MAX_PAYLOAD])
    env = dict(os.environ, **{SEMANTIC_ENV_GUARD: "1"})
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", sem.get("model", "haiku")],
            capture_output=True, text=True, timeout=SEMANTIC_TIMEOUT_SEC, env=env,
        )
        if r.returncode != 0:
            return None
        m = re.search(r"\{.*\}", r.stdout, re.DOTALL)
        data = json.loads(m.group()) if m else {}
    except Exception:
        return None
    return data if data.get("sensitive") else None


def main() -> None:
    event = hook_io.read_event()
    tool = event.get("tool_name", "")
    if not is_target(tool):
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("exfil_guard", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    if tool.startswith("mcp__") and server_prefix(tool) in cfg.get("trusted_servers", []):
        hook_io.finalize(None, cfg_all)
    payload_text = json.dumps(event.get("tool_input") or {}, ensure_ascii=False)
    try:
        if cfg.get("mode") == "always":
            verdict = evaluate(payload_text, cfg)
            if verdict is None or verdict["decision"] != "deny":  # denyは降格させない
                verdict = {
                    "decision": "ask",
                    "reason": (
                        f"外部送信({tool})を検出しました(mode=always)。"
                        "送信してよいか確認してください"
                    ),
                }
        else:
            verdict = evaluate(payload_text, cfg)
            if verdict is None and cfg.get("categories", {}).get("semantic", "ask") != "off":
                s = semantic_check(payload_text, cfg)
                if s:
                    verdict = {
                        "decision": "ask",
                        "reason": (
                            f"LLM判定: 機微情報を含む可能性 — "
                            f"{s.get('reason', '(理由なし)')}"
                        ),
                    }
    except Exception as exc:  # 外部送信ガードは fail-open + 可視化
        hook_io.fail_open("exfil_guard", exc)
        return
    out = hook_io.pre_tool_decision(verdict["decision"], verdict["reason"]) if verdict else None
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
