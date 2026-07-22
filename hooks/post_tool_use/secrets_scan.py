#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Edit/Write で書き込まれた内容のシークレットを検出し、除去を促す(block)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns, scanners  # noqa: E402

WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
CONTENT_KEYS = ("content", "new_string", "new_source")


def extract_written_text(tool_input: dict) -> str:
    return "\n".join(str(tool_input[k]) for k in CONTENT_KEYS if tool_input.get(k))


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") not in WRITE_TOOLS:
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("secrets_scan", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    text = extract_written_text(event.get("tool_input") or {})
    try:
        custom_rules = [
            {"name": p["name"], "regex": p["regex"]}
            for p in cfg.get("custom_patterns", [])
        ]
        findings = scanners.scan_secrets(
            text, cfg_all.get("scanners"), event.get("cwd")
        ) + patterns.scan_text(text, custom_rules)
    except Exception as exc:
        hook_io.fail_open("secrets_scan", exc)
        return
    out = None
    if findings:
        names = ", ".join(sorted({f["rule"] for f in findings}))
        file_path = (event.get("tool_input") or {}).get("file_path", "(不明)")
        out = hook_io.post_block(
            f"書き込み内容にシークレットの可能性を検出しました({names})。"
            f"{file_path} から該当箇所を除去し、環境変数など安全な方法に置き換えてください"
        )
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
