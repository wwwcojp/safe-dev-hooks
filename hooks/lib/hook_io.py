"""Hookの標準入出力処理。stdinイベント読取とJSON判定出力を担う。"""
import json
import sys


def read_event() -> dict:
    try:
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def emit(obj: dict) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def pre_tool_decision(decision: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def post_block(reason: str, context: str = "") -> dict:
    out: dict = {"decision": "block", "reason": reason}
    if context:
        out["hookSpecificOutput"] = {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }
    return out


def finalize(out: dict | None, cfg: dict, quiet_notices: bool = False) -> None:
    """判定出力に設定エラー警告と信頼判定の通知(_notices)を合成して出力し、exit 0 する。

    _errors は「設定が壊れている」、_notices は「設定を意図的に採用しなかった」。
    quiet_notices=True は非対話のロギングフック(audit_log)用で、_notices を出さない。
    """
    errors = cfg.get("_errors") or []
    notices = [] if quiet_notices else (cfg.get("_notices") or [])
    messages: list[str] = []
    if errors:
        messages.append(
            "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: "
            + "; ".join(errors)
        )
    messages.extend(notices)
    if messages:
        out = dict(out or {})
        msg = "\n".join(messages)
        existing = out.get("systemMessage")
        out["systemMessage"] = f"{existing}\n{msg}" if existing else msg
    if out:
        emit(out)
    sys.exit(0)


def fail_open(hook_name: str, exc: Exception) -> None:
    """Hook自体の異常時: ツール実行は止めないが必ず可視化する(fail-open)。"""
    emit(
        {
            "systemMessage": (
                f"[safe-dev-hooks] {hook_name} が異常終了したため検査をスキップしました: "
                f"{exc}"
            )
        }
    )
    sys.exit(0)
