#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""セッション中の設定変更(ConfigChange)を可視化する。検知専用でブロックしない。

write_protected(予防層)を素通りする経路(インタプリタレベルの書込・外部プロセス等)で
設定が変更されても、変更の発生自体をユーザーへ必ず通知する検知層。ブロックしないのは、
disableAllHooks という正規の解除手段や人間自身の設定編集を妨げないため(warn→block の
段階導入原則。docs/best-practices.md セクション6.3)。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io  # noqa: E402

# 変更元識別子のフィールド名は公式ドキュメントに明記が無いため、候補を防御的に読む
_SOURCE_KEYS = ("source", "config_source", "matcher")

_USER_SETTINGS = Path.home() / ".claude" / "settings.json"
_PROJECT_SETTINGS = (
    Path(".claude") / "settings.json",
    Path(".claude") / "settings.local.json",
)


def _change_source(event: dict) -> str:
    for key in _SOURCE_KEYS:
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return "不明"


def _disable_all_hooks_active(cwd: str | None) -> bool:
    base = Path(cwd or ".")
    candidates = [_USER_SETTINGS] + [base / p for p in _PROJECT_SETTINGS]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and data.get("disableAllHooks") is True:
            return True
    return False


def main() -> None:
    event = hook_io.read_event()
    cfg_all = config.load_config(event.get("cwd"))
    if not cfg_all.get("config_guard", {}).get("enabled", True):
        hook_io.finalize(None, cfg_all)
    try:
        msg = (
            "[safe-dev-hooks] セッション中に設定ファイルが変更されました"
            f"(変更元: {_change_source(event)})。意図した変更か確認してください。"
        )
        if _disable_all_hooks_active(event.get("cwd")):
            msg += (
                "\n[safe-dev-hooks] 警告: disableAllHooks が有効です。"
                "全Hooks(本ガードを含む)が無効化されます。"
            )
        hook_io.finalize({"systemMessage": msg}, cfg_all)
    except Exception as exc:  # 検知専用のため fail-open
        hook_io.fail_open("config_guard", exc)


if __name__ == "__main__":
    main()
