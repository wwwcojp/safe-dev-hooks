"""3層マージ設定(ビルトイン既定 ← グローバル ← プロジェクト)。"""
import copy
import json
from pathlib import Path

GLOBAL_CONFIG_PATH = Path.home() / ".claude" / "claude-hooks.json"
PROJECT_CONFIG_NAME = ".claude-hooks.json"

DEFAULTS: dict = {
    "bash_guard": {"enabled": True, "extra_deny": [], "extra_ask": [], "allow": []},
    "secrets_guard": {"enabled": True, "protected_paths": [], "allow_paths": []},
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
    "secrets_scan": {"enabled": True},
    "audit_log": {"enabled": True, "path": ".claude/logs"},
    "notify": {"enabled": True, "command": None},
}

_ENUM_KEYS = {
    ("exfil_guard", "mode"): {"detect", "always"},
    ("exfil_output_scan", "action"): {"warn", "redact"},
    ("quality_gate", "mode"): {"block", "warn"},
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
    cfg = copy.deepcopy(DEFAULTS)
    errors: list[str] = []
    paths = [GLOBAL_CONFIG_PATH, Path(cwd or ".") / PROJECT_CONFIG_NAME]
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
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
    categories = cfg.get("exfil_guard", {}).get("categories", {})
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
    cfg["_errors"] = errors
    return cfg
