#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""通知イベントをターミナルベルまたは任意コマンドでユーザーへ伝える。"""
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io  # noqa: E402


def main() -> None:
    event = hook_io.read_event()
    cfg = config.load_config(event.get("cwd")).get("notify", {})
    if not cfg.get("enabled", True):
        sys.exit(0)
    command = cfg.get("command")
    if command:
        message = event.get("message", "")
        try:
            subprocess.run(
                shlex.split(command.replace("{message}", shlex.quote(message))),
                timeout=10, capture_output=True,
            )
        except Exception:
            pass
        sys.exit(0)
    hook_io.emit({"terminalSequence": ""})
    sys.exit(0)


if __name__ == "__main__":
    main()
