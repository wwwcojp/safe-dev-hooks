#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""通知イベントをデスクトップ通知・ターミナルベル・任意コマンドでユーザーへ伝える。"""
import os
import shlex
import shutil
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


TITLE = "Claude Code"
_BACKEND_TIMEOUT = 5

# メッセージはインジェクション回避のため環境変数で渡す(WSLENVで境界を越える)
_TOAST_PS_SCRIPT = (
    '$ErrorActionPreference = "Stop"\n'
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType = WindowsRuntime] | Out-Null\n"
    "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
    '$texts = $template.GetElementsByTagName("text")\n'
    "$texts.Item(0).AppendChild($template.CreateTextNode($env:NOTIFY_TITLE)) | Out-Null\n"
    "$texts.Item(1).AppendChild($template.CreateTextNode($env:NOTIFY_MSG)) | Out-Null\n"
    '$appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell'
    '\\v1.0\\powershell.exe"\n'
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show("
    "[Windows.UI.Notifications.ToastNotification]::new($template))\n"
)


def _run_backend(argv: list[str], env: dict | None = None) -> bool:
    try:
        proc = subprocess.run(
            argv, env=env, capture_output=True, timeout=_BACKEND_TIMEOUT
        )
        return proc.returncode == 0
    except Exception:
        return False


def _notify_windows_toast(title: str, message: str) -> bool:
    env = dict(os.environ)
    env["NOTIFY_TITLE"] = title
    env["NOTIFY_MSG"] = message
    wslenv = env.get("WSLENV", "")
    env["WSLENV"] = (wslenv + ":" if wslenv else "") + "NOTIFY_TITLE:NOTIFY_MSG"
    return _run_backend(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS_SCRIPT],
        env=env,
    )


def _notify_notify_send(title: str, message: str) -> bool:
    return _run_backend(["notify-send", title, message])


def _notify_osascript(title: str, message: str) -> bool:
    # AppleScriptへの文字列埋め込みを避け、argv経由で渡す
    return _run_backend(
        [
            "osascript",
            "-e", "on run argv",
            "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e", "end run",
            title,
            message,
        ]
    )


def _notify_desktop(message: str) -> bool:
    if _is_wsl() and shutil.which("powershell.exe"):
        if _notify_windows_toast(TITLE, message):
            return True
    if shutil.which("notify-send"):
        if _notify_notify_send(TITLE, message):
            return True
    if shutil.which("osascript"):
        if _notify_osascript(TITLE, message):
            return True
    return False


def main() -> None:
    event = hook_io.read_event()
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("notify", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    message = event.get("message", "")
    command = cfg.get("command")
    if command:
        try:
            subprocess.run(
                shlex.split(command.replace("{message}", shlex.quote(message))),
                timeout=10, capture_output=True,
            )
        except Exception:
            pass
        hook_io.finalize(None, cfg_all)
    if cfg.get("method", "auto") == "auto" and _notify_desktop(message):
        hook_io.finalize(None, cfg_all)
    hook_io.finalize({"terminalSequence": "\u0007"}, cfg_all)


if __name__ == "__main__":
    main()
