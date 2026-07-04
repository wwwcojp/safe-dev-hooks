#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""編集されたファイルへ lint/format チェックを実行し、失敗をClaudeへフィードバックする。"""
import fnmatch
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io  # noqa: E402

WRITE_TOOLS = ("Edit", "Write")
COMMAND_TIMEOUT_SEC = 45
OUTPUT_TAIL_CHARS = 1500

# 自動検出: (globパターン, 必要な実行ファイル, 前提設定ファイル(いずれか必須), コマンド)
AUTO_DETECT = [
    ("*.py", "ruff", ("pyproject.toml", "ruff.toml", ".ruff.toml"), "ruff check {file}"),
    ("*.rs", "rustfmt", ("Cargo.toml",), "rustfmt --check {file}"),
    ("*.js|*.jsx|*.ts|*.tsx", "npx", ("package.json",), "npx --no-install eslint {file}"),
]


def resolve_commands(file_path: str, cfg: dict, cwd: str) -> list:
    name = Path(file_path).name
    quoted = shlex.quote(file_path)
    commands = []
    for pattern, cmds in (cfg.get("commands") or {}).items():
        if fnmatch.fnmatch(name, pattern):
            commands += [c.replace("{file}", quoted) for c in cmds]
    if commands:
        return commands
    for patterns_str, exe, markers, cmd in AUTO_DETECT:
        if not any(fnmatch.fnmatch(name, p) for p in patterns_str.split("|")):
            continue
        if shutil.which(exe) is None:
            continue
        if not any((Path(cwd) / m).is_file() for m in markers):
            continue
        commands.append(cmd.replace("{file}", quoted))
    return commands


def run_checks(commands: list, cwd: str) -> list:
    failures = []
    for cmd in commands:
        try:
            r = subprocess.run(
                shlex.split(cmd), cwd=cwd, capture_output=True, text=True,
                timeout=COMMAND_TIMEOUT_SEC,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            failures.append(f"$ {cmd}\n実行できませんでした: {exc}")
            continue
        if r.returncode != 0:
            tail = (r.stdout + r.stderr)[-OUTPUT_TAIL_CHARS:]
            failures.append(f"$ {cmd}\n{tail}")
    return failures


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") not in WRITE_TOOLS:
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("quality_gate", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    file_path = (event.get("tool_input") or {}).get("file_path", "")
    cwd = event.get("cwd") or "."
    if not file_path or not Path(file_path).is_file():
        hook_io.finalize(None, cfg_all)
    try:
        commands = resolve_commands(file_path, cfg, cwd)
        failures = run_checks(commands, cwd) if commands else []
    except Exception as exc:
        hook_io.fail_open("quality_gate", exc)
        return
    out = None
    if failures:
        detail = "\n\n".join(failures)
        if cfg.get("mode", "block") == "block":
            out = hook_io.post_block(
                f"品質チェックが失敗しました。修正してください:\n{detail}"
            )
        else:
            out = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": f"[safe-dev-hooks] 品質チェック警告:\n{detail}",
                }
            }
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
