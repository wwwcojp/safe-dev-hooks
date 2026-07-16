#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""通知イベントをターミナルベルまたは任意コマンドでユーザーへ伝える。"""
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io  # noqa: E402

_PROC_VERSION = Path("/proc/version")


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in _PROC_VERSION.read_text(encoding="utf-8").lower()
    except OSError:
        return False


def main() -> None:
    event = hook_io.read_event()
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("notify", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
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
        hook_io.finalize(None, cfg_all)
    hook_io.finalize({"terminalSequence": "\u0007"}, cfg_all)


if __name__ == "__main__":
    main()
