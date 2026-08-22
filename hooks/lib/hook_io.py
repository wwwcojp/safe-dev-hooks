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


def finalize(out: dict | None, cfg: dict) -> None:
    """判定出力に設定エラー警告を合成して出力し、exit 0 する。"""
    errors = cfg.get("_errors") or []
    if errors:
        out = dict(out or {})
        msg = (
            "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: "
            + "; ".join(errors)
        )
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
