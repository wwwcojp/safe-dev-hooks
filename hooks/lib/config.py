"""3層マージ設定(ビルトイン既定 ← グローバル ← プロジェクト)。"""
import copy
import json
from pathlib import Path

GLOBAL_CONFIG_PATH = Path.home() / ".claude" / "claude-hooks.json"
PROJECT_CONFIG_NAME = ".claude-hooks.json"

DEFAULTS: dict = {
    "bash_guard": {
        "enabled": True, "extra_deny": [], "extra_ask": [], "allow": [],
        "protected_branches": ["main", "master", "develop", "release", "production"],
    },
    "secrets_guard": {
        "enabled": True, "protected_paths": [], "allow_paths": [],
        "write_protected_paths": [],
    },
    "exfil_guard": {
        "enabled": True,
        "mode": "detect",
        "categories": {
            "credentials": "deny",
            "pii": "ask",
            "confidential_markers": "ask",
            "custom": "ask",
            "semantic": "ask",
        },
        "semantic": {"model": "haiku"},
        "custom_patterns": [],
        "trusted_servers": [],
    },
    "exfil_output_scan": {"enabled": True, "action": "warn"},
    "quality_gate": {"enabled": True, "mode": "block", "commands": {}},
    "secrets_scan": {"enabled": True, "custom_patterns": []},
    "audit_log": {"enabled": True, "path": ".claude/logs"},
    "config_guard": {"enabled": True},
    "notify": {"enabled": True, "method": "auto", "command": None},
    "scanners": {
        "gitleaks": "auto",
        "gitleaks_image": "ghcr.io/gitleaks/gitleaks:v8.30.1",
        "gitleaks_config": None,
    },
}

_ENUM_KEYS = {
    ("exfil_guard", "mode"): {"detect", "always"},
    ("exfil_output_scan", "action"): {"warn", "redact"},
    ("quality_gate", "mode"): {"block", "warn"},
    ("notify", "method"): {"auto", "bell"},
    ("scanners", "gitleaks"): {"auto", "off", "docker"},
}
_CATEGORY_ACTIONS = {"deny", "ask", "off"}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(cwd: str | None = None) -> dict:
    """設定を読み込む。この関数は例外を送出しない。

    設定ファイルはリポジトリ由来の信頼できない入力であり、ここで例外が漏れると
    Hookが判定前に異常終了して deny 層ごと素通りする。どんな異常でもビルトイン
    既定値へフォールバックし、`_errors` に記録して可視化する(fail-safe)。
    """
    try:
        return _load_config(cwd)
    except Exception as exc:  # 想定外の異常でもガードを死なせない
        cfg = copy.deepcopy(DEFAULTS)
        cfg["_errors"] = [f"設定の読み込みに失敗したため既定値を使用します: {exc}"]
        return cfg


def _load_config(cwd: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    errors: list[str] = []
    paths = [GLOBAL_CONFIG_PATH, Path(cwd or ".") / PROJECT_CONFIG_NAME]
    for path in paths:
        try:
            if not path.is_file():
                continue
            # 不正UTF-8は UnicodeDecodeError(ValueError)、JSON構文エラーは
            # JSONDecodeError(ValueError)、深いネストは RecursionError を送出する
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path}: オブジェクトではありません")
            continue
        cfg = _merge(cfg, data)
    for key, default_value in DEFAULTS.items():
        if not isinstance(cfg.get(key), type(default_value)):
            errors.append(f"{key}: 設定値の型が不正なため既定値を使用します")
            cfg[key] = copy.deepcopy(default_value)
    for (section, sub_key), allowed in _ENUM_KEYS.items():
        value = cfg.get(section, {}).get(sub_key)
        if value not in allowed:
            errors.append(
                f"{section}.{sub_key}: 未知の値 {value!r} のため既定値を使用します"
            )
            cfg[section][sub_key] = DEFAULTS[section][sub_key]
    categories = cfg.get("exfil_guard", {}).get("categories")
    if not isinstance(categories, dict):
        errors.append("exfil_guard.categories: オブジェクトでないため既定値を使用します")
        categories = copy.deepcopy(DEFAULTS["exfil_guard"]["categories"])
        cfg["exfil_guard"]["categories"] = categories
    for cat_key, cat_value in list(categories.items()):
        if cat_value not in _CATEGORY_ACTIONS:
            errors.append(
                f"exfil_guard.categories.{cat_key}: 未知の値 {cat_value!r} のため既定値を使用します"
            )
            default_categories = DEFAULTS["exfil_guard"]["categories"]
            if cat_key in default_categories:
                categories[cat_key] = default_categories[cat_key]
            else:
                del categories[cat_key]
    pb = cfg.get("bash_guard", {}).get("protected_branches")
    if not isinstance(pb, list) or not all(isinstance(x, str) for x in pb):
        msg = "bash_guard.protected_branches: 文字列リストでないため既定値を使用します"
        errors.append(msg)
        cfg["bash_guard"]["protected_branches"] = list(
            DEFAULTS["bash_guard"]["protected_branches"]
        )
    wp = cfg.get("secrets_guard", {}).get("write_protected_paths")
    if not isinstance(wp, list) or not all(isinstance(x, str) for x in wp):
        msg = "secrets_guard.write_protected_paths: 文字列リストでないため既定値を使用します"
        errors.append(msg)
        cfg["secrets_guard"]["write_protected_paths"] = []
    sc = cfg.get("scanners", {})
    if not isinstance(sc.get("gitleaks_image"), str):
        errors.append("scanners.gitleaks_image: 文字列でないため既定値を使用します")
        cfg["scanners"]["gitleaks_image"] = DEFAULTS["scanners"]["gitleaks_image"]
    gc = sc.get("gitleaks_config")
    if gc is not None and not isinstance(gc, str):
        errors.append(
            "scanners.gitleaks_config: 文字列またはnullでないため既定値を使用します"
        )
        cfg["scanners"]["gitleaks_config"] = None
    cfg["_errors"] = errors
    return cfg
