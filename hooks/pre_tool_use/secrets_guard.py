#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""機密ファイル(.env・秘密鍵・認証情報)への読取・編集・catを遮断する。"""
import fnmatch
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402

FILE_TOOLS = ("Read", "Edit", "Write")

_GLOB_CHARS = set("*?[")

_SEGMENT_RE = re.compile(r"(?:&&|\|\||;|\n)")
_MUTATION_RE = re.compile(
    r"(?:>|>>|\btee\b|\bsed\s+(?:-i\b|--in-place\b)|\brm\b|\bmv\b|\bcp\b"
    r"|\btruncate\b|\bdd\b|\binstall\b|\bln\b)"
)
_REDIR_RE = re.compile(r"(>>|>)")


def _mutation_target_tokens(seg: str) -> list[str]:
    """リダイレクト演算子をトークン境界として分離し、of=FILE 等の候補も展開する。"""
    padded = _REDIR_RE.sub(r" \1 ", seg)
    try:
        toks = shlex.split(padded)
    except ValueError:
        toks = padded.split()
    candidates: list[str] = []
    for tok in toks:
        candidates.append(tok)
        if "=" in tok:  # of=FILE, --output=FILE など
            candidates.append(tok.split("=", 1)[1])
    return candidates


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


def _self_protected_dirs() -> list[Path]:
    hooks_dir = Path(__file__).resolve().parent.parent
    return [hooks_dir, hooks_dir.parent / "rules"]


def check_write_protected(path_str: str, cfg: dict) -> str | None:
    rules = patterns.load_rules("sensitive_paths.json")
    wp = rules.get("write_protected", []) + cfg.get("write_protected_paths", [])
    p = os.path.expanduser(path_str)
    name = os.path.basename(p.rstrip("/"))
    for pat in wp:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(path_str, pat):
            return pat
    try:
        rp = Path(p).resolve()
    except (OSError, RuntimeError):
        rp = Path(p)
    for d in _self_protected_dirs():
        try:
            rp.relative_to(d)
            return f"{d.name}/"
        except ValueError:
            continue
    return None


def evaluate(event: dict, cfg: dict) -> dict | None:
    tool = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    if tool in FILE_TOOLS:
        target = tool_input.get("file_path", "")
        if not target:
            return None
        hit = check_path(target, cfg)
        if hit:
            return {"decision": "deny",
                    "reason": f"機密ファイルへのアクセスを遮断: {target}(該当ルール: {hit})"}
        if tool in ("Edit", "Write"):
            wp = check_write_protected(target, cfg)
            if wp:
                return {"decision": "deny",
                        "reason": f"設定/フックファイルの改変を遮断: {target}(該当ルール: {wp})"}
        return None
    if tool == "Bash":
        command = tool_input.get("command", "")
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        for tok in tokens:
            if _looks_like_path(tok) and check_path(tok, cfg):
                return {"decision": "deny",
                        "reason": f"機密ファイルへのアクセスを遮断: {tok}"
                                  f"(該当ルール: {check_path(tok, cfg)})"}
        for seg in _SEGMENT_RE.split(command):
            if not _MUTATION_RE.search(seg):
                continue
            seg_tokens = _mutation_target_tokens(seg)
            for tok in seg_tokens:
                if not _looks_like_path(tok):
                    continue
                wp = check_write_protected(tok, cfg)
                if wp:
                    return {"decision": "deny",
                            "reason": f"設定/フックファイルの改変を遮断: {tok}(該当ルール: {wp})"}
    return None


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") not in FILE_TOOLS + ("Bash",):
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("secrets_guard", {})
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
    if not cfg.get("enabled", True):
        out = dict(out or {})
        out.setdefault(
            "systemMessage",
            "[safe-dev-hooks] secrets_guard は enabled:false でも "
            "deny 層を無効化できません(検査を継続しました)",
        )
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
