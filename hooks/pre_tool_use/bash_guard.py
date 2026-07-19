#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Bashコマンドの破壊的操作を deny/ask の2段階でガードする。"""
import fnmatch
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402


def _segments(command: str) -> list[str]:
    parts = re.split(r"(?:&&|\|\||;|\n)", command)
    return [p.strip() for p in parts if p.strip()]


def _normalize(text: str) -> str:
    # クォートによるすり抜け対策(過剰検知側に倒す)
    return text.replace('"', "").replace("'", "")


_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=([^\s'\"$`(){}]+)$")


def _expand_simple_assignments(command: str) -> str:
    """同一コマンド内の単純な定数代入(VAR=value)を後続の $VAR/${VAR} に展開する。"""
    assignments: dict[str, str] = {}
    for seg in _segments(command):
        m = _ASSIGN_RE.match(seg)
        if m:
            assignments[m.group(1)] = m.group(2)
    if not assignments:
        return command
    expanded = command
    for name, value in assignments.items():
        pattern = r"\$\{" + re.escape(name) + r"\}"
        expanded = re.sub(pattern, lambda m, v=value: v, expanded)
        pattern = r"\$" + re.escape(name) + r"(?![A-Za-z0-9_])"
        expanded = re.sub(pattern, lambda m, v=value: v, expanded)
    return expanded


_SEND_CMD_RE = re.compile(
    r"\b(curl|wget)\b[^;|&]*?"
    r"(-d\b|--data\b|--data-[a-z]+\b|-F\b|--form\b|-T\b|--upload-file\b"
    r"|--post-data\b|--post-file\b|--body-data\b|--body-file\b)"
)
_ENV_REF_RE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")
_CMD_SUBST_RE = re.compile(r"\$\(|`")


def _has_sensitive_operand(segment: str) -> bool:
    if _ENV_REF_RE.search(segment) or _CMD_SUBST_RE.search(segment):
        return True
    protected = patterns.load_rules("sensitive_paths.json")["protected"]
    for tok in segment.split():
        name = os.path.basename(tok.strip("\"'").rstrip("/"))
        if any(fnmatch.fnmatch(name, pat) for pat in protected):
            return True
    return False


def _exfil_ask(segment: str) -> dict | None:
    if not _SEND_CMD_RE.search(segment):
        return None
    if _has_sensitive_operand(segment):
        return {
            "decision": "ask",
            "reason": (
                "外部送信コマンドに機微オペランド(環境変数/コマンド置換/機密ファイル)を検出。"
                "送信内容を確認してください"
            ),
        }
    return None


def _force_push_rules(cfg: dict) -> list[dict]:
    branches = cfg.get("protected_branches")
    if branches is None:  # キー未指定(直接呼び出し等)は最小限の既定で保護
        branches = ["main", "master"]
    if not branches:  # 空リスト = 保護ブランチ無し(force-push規則を生成しない)
        return []
    alt = "|".join(re.escape(b) for b in branches)
    return [
        {"name": "force-push-protected",
         "regex": rf"\bgit\s+push\s+[^;|&]*(--force\b|-f\b)[^;|&]*\b({alt})\b"},
        {"name": "force-push-protected-order",
         "regex": rf"\bgit\s+push\s+[^;|&]*\b({alt})\b[^;|&]*(--force\b|-f\b)"},
        {"name": "force-push-refspec",
         "regex": rf"\bgit\s+push\b[^;|&]*\s\+(?:[^\s:]*:)?(?:[^\s:]*/)?({alt})(?![\w.@:/-])"},
    ]


def evaluate(command: str, cfg: dict) -> dict | None:
    enabled = cfg.get("enabled", True)
    deny_rules = (
        list(patterns.load_rules("bash_deny.json"))
        + _force_push_rules(cfg)
        + [{"name": f"extra_deny:{p}", "regex": p} for p in cfg.get("extra_deny", [])]
    )
    ask_rules = list(patterns.load_rules("bash_ask.json")) + [
        {"name": f"extra_ask:{p}", "regex": p} for p in cfg.get("extra_ask", [])
    ]
    allow = cfg.get("allow", [])
    targets = [_normalize(s) for s in _segments(command)] + [_normalize(command)]
    expanded = _expand_simple_assignments(command)
    if expanded != command:
        targets += [_normalize(s) for s in _segments(expanded)] + [_normalize(expanded)]
    for rule in deny_rules:
        if any(re.search(rule["regex"], t) for t in targets):
            return {
                "decision": "deny",
                "reason": f"破壊的コマンドを検出: {rule['name']}(deny層は設定で解除できません)",
            }
    if not enabled:
        return None
    for rule in ask_rules:
        for t in targets:
            if re.search(rule["regex"], t) and not any(re.search(a, t) for a in allow):
                return {
                    "decision": "ask",
                    "reason": (
                        f"注意が必要なコマンドを検出: {rule['name']}。"
                        "実行してよいか確認してください"
                    ),
                }
    for seg in _segments(command):
        nseg = _normalize(seg)
        verdict = _exfil_ask(nseg)
        if verdict and not any(re.search(a, nseg) for a in allow):
            return verdict
    return None


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") != "Bash":
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("bash_guard", {})
    command = (event.get("tool_input") or {}).get("command", "")
    try:
        verdict = evaluate(command, cfg)
    except Exception as exc:  # deny層の判定不能は安全側に倒す(fail-close)
        hook_io.finalize(
            hook_io.pre_tool_decision(
                "ask",
                f"bash_guard の判定に失敗したため確認してください: {exc}",
            ),
            cfg_all,
        )
        return
    out = hook_io.pre_tool_decision(verdict["decision"], verdict["reason"]) if verdict else None
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
