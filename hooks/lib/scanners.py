"""秘密検出バックエンドの集約。内蔵patterns(floor)にgitleaksをunion加算する。

gitleaks は「あれば使う」任意バックエンド。内蔵検出は常に走る floor であり、
gitleaks の結果は上に加算されるのみ(不在・失敗時も floor は不変=deny 保証を弱めない)。
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import patterns

GITLEAKS_TIMEOUT_SEC = 15
DEFAULT_IMAGE = "ghcr.io/gitleaks/gitleaks:v8.30.1"
_COMMON_FLAGS = [
    "stdin", "--report-format", "json", "--report-path", "-",
    "--no-banner", "-l", "error",
]


def _resolve_config_path(sc: dict, cwd: str | None) -> str | None:
    explicit = sc.get("gitleaks_config")
    if explicit:
        return explicit
    if cwd:
        candidate = Path(cwd) / ".gitleaks.toml"
        if candidate.is_file():
            return str(candidate)
    return None


def _gitleaks_argv(sc: dict, cwd: str | None) -> list | None:
    mode = sc.get("gitleaks", "auto")
    if mode == "off":
        return None
    cfg_path = _resolve_config_path(sc, cwd)
    if mode == "auto":
        if shutil.which("gitleaks") is None:
            return None
        argv = ["gitleaks", *_COMMON_FLAGS]
        if cfg_path:
            argv += ["-c", cfg_path]
        return argv
    if mode == "docker":
        if shutil.which("docker") is None:
            return None
        image = sc.get("gitleaks_image") or DEFAULT_IMAGE
        argv = ["docker", "run", "--rm", "-i"]
        tail: list = []
        if cfg_path:
            argv += ["-v", f"{os.path.abspath(cfg_path)}:/tmp/gl.toml:ro"]
            tail = ["-c", "/tmp/gl.toml"]
        argv += [image, *_COMMON_FLAGS, *tail]
        return argv
    return None


def _run_gitleaks(argv: list, text: str) -> list:
    try:
        r = subprocess.run(
            argv, input=text, capture_output=True, text=True,
            timeout=GITLEAKS_TIMEOUT_SEC,
        )
    except Exception:
        return []
    if r.returncode not in (0, 1):
        return []
    if r.returncode == 0:
        return []
    try:
        data = json.loads(r.stdout)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for f in data:
        if not isinstance(f, dict):
            continue
        rule_id = f.get("RuleID")
        secret = f.get("Secret")
        if rule_id and secret:
            out.append({"rule": f"gitleaks:{rule_id}", "match": secret})
    return out


def scan_secrets(text: str, scanners_cfg: dict | None = None,
                 cwd: str | None = None) -> list:
    # 内蔵 floor は常に走る(例外は呼び出し側の fail_open に委ねるため try で囲まない)
    findings = patterns.scan_text(text, patterns.load_rules("secret_patterns.json"))
    sc = scanners_cfg or {}
    argv = _gitleaks_argv(sc, cwd)
    if argv is not None:
        findings = findings + _run_gitleaks(argv, text)
    seen = set()
    deduped = []
    for f in findings:
        key = (f["rule"], f["match"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return deduped
