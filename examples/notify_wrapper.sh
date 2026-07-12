#!/usr/bin/env bash
# notify.command 用ラッパースクリプト。実行環境を自動判別し、
# WSL(Windowsトースト) / Linuxデスクトップ(notify-send) / macOS(osascript)
# のいずれかでデスクトップ通知を出す。どれも使えなければ標準エラーへ
# ベル文字とメッセージを出力する。
#
# 設定例(.claude-hooks.json):
#   {"notify": {"command": "bash /home/USER/safe-dev-hooks/examples/notify_wrapper.sh {message}"}}
#
# 注意: notify.command はシェルを介さずに実行されるため、$HOME やチルダは
# 展開されない。スクリプトのパスは必ず絶対パスで記載すること。
set -u

message="${1:-Claude Code からの通知}"
title="Claude Code"

# WSL: PowerShell の WinRT API で Windows トースト通知(追加モジュール不要)。
# メッセージはインジェクション回避のため環境変数で渡す(WSLENV で境界を越える)。
if [[ -n "${WSL_DISTRO_NAME:-}" || ( -r /proc/version && $(</proc/version) == *[Mm]icrosoft* ) ]] \
    && command -v powershell.exe >/dev/null 2>&1; then
    if WSLENV="${WSLENV:+$WSLENV:}NOTIFY_TITLE:NOTIFY_MSG" \
        NOTIFY_TITLE="$title" NOTIFY_MSG="$message" \
        powershell.exe -NoProfile -NonInteractive -Command '
            $ErrorActionPreference = "Stop"
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $texts = $template.GetElementsByTagName("text")
            $texts.Item(0).AppendChild($template.CreateTextNode($env:NOTIFY_TITLE)) | Out-Null
            $texts.Item(1).AppendChild($template.CreateTextNode($env:NOTIFY_MSG)) | Out-Null
            $appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
                [Windows.UI.Notifications.ToastNotification]::new($template))
        ' >/dev/null 2>&1; then
        exit 0
    fi
fi

# Linux デスクトップ
if command -v notify-send >/dev/null 2>&1; then
    if notify-send "$title" "$message" >/dev/null 2>&1; then
        exit 0
    fi
fi

# macOS: メッセージは argv 経由で渡し、AppleScript への文字列埋め込みを避ける
if command -v osascript >/dev/null 2>&1; then
    if osascript \
        -e 'on run argv' \
        -e 'display notification (item 2 of argv) with title (item 1 of argv)' \
        -e 'end run' \
        "$title" "$message" >/dev/null 2>&1; then
        exit 0
    fi
fi

# フォールバック: 呼び出し元(notify.py)は標準出力・標準エラーを捕捉して捨てるため、
# 制御端末があれば /dev/tty へ直接ベル文字とメッセージを書く(devcontainer等の
# デスクトップ通知が使えない環境でもターミナルベルを鳴らすため)。
# 制御端末が無ければ標準エラーへ出力する。
if printf '\a[%s] %s\n' "$title" "$message" 2>/dev/null > /dev/tty; then
    exit 0
fi
printf '\a[%s] %s\n' "$title" "$message" >&2
exit 0
