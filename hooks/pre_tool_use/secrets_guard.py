#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""機密ファイル(.env・秘密鍵・認証情報)への読取・編集・catを遮断する。"""
import fnmatch
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402

FILE_TOOLS = ("Read", "Edit", "Write")

_GLOB_CHARS = set("*?[")


def _looks_like_path(token: str) -> bool:
    """パス/ファイル名の形をしたトークンだけ検査対象にする(検索語やglobパターンを除外)。"""
    if not token or _GLOB_CHARS & set(token):
        return False
    return "/" in token or token[0] in ".~" or "." in token


def check_path(path_str: str, cfg: dict) -> str | None:
    rules = patterns.load_rules("sensitive_paths.json")
    protected = rules["protected"] + cfg.get("protected_paths", [])
    allow = rules["allow"] + cfg.get("allow_paths", [])
    p = os.path.expanduser(path_str)
    name = os.path.basename(p.rstrip("/"))
    for pat in allow:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(path_str, pat):
            return None
    for pat in protected:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(path_str, pat):
            return pat
    for d in rules["protected_dirs"]:
        d_exp = os.path.expanduser(d)
        if p == d_exp or p.startswith(d_exp + os.sep):
            return d
    return None


def evaluate(event: dict, cfg: dict) -> dict | None:
    tool = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    hit = None
    target = ""
    if tool in FILE_TOOLS:
        target = tool_input.get("file_path", "")
        hit = check_path(target, cfg) if target else None
    elif tool == "Bash":
        command = tool_input.get("command", "")
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        for tok in tokens:
            if not _looks_like_path(tok):
                continue
            hit = check_path(tok, cfg)
            if hit:
                target = tok
                break
    if hit:
        return {
            "decision": "deny",
            "reason": f"機密ファイルへのアクセスを遮断: {target}(該当ルール: {hit})",
        }
    return None


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") not in FILE_TOOLS + ("Bash",):
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("secrets_guard", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    try:
        verdict = evaluate(event, cfg)
    except Exception as exc:  # fail-close(ask)
        hook_io.finalize(
            hook_io.pre_tool_decision(
                "ask",
                f"secrets_guard の判定に失敗したため確認してください: {exc}",
            ),
            cfg_all,
        )
        return
    out = hook_io.pre_tool_decision(verdict["decision"], verdict["reason"]) if verdict else None
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
