#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""全ツール実行・セッション境界をJSONLで監査記録する。失敗しても開発を止めない。"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io  # noqa: E402

SUMMARY_MAX_CHARS = 500


def main() -> None:
    event = hook_io.read_event()
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("audit_log", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    try:
        log_dir = Path(cfg.get("path", ".claude/logs"))
        if not log_dir.is_absolute():
            log_dir = Path(event.get("cwd") or ".") / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.timezone.utc)
        record = {
            "ts": now.isoformat(),
            "session_id": event.get("session_id", ""),
            "event": event.get("hook_event_name", ""),
            "tool_name": event.get("tool_name", ""),
            "tool_summary": json.dumps(
                event.get("tool_input") or {}, ensure_ascii=False
            )[:SUMMARY_MAX_CHARS],
        }
        log_file = log_dir / f"audit-{now.strftime('%Y%m%d')}.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 監査ログの失敗は開発を止めない(spec セクション8)
    hook_io.finalize(None, cfg_all)


if __name__ == "__main__":
    main()
