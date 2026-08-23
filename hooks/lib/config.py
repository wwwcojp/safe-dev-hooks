"""3層マージ設定(ビルトイン既定 ← グローバル ← プロジェクト)。"""
import copy
import json
from pathlib import Path

from . import trust

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
    # プロジェクト層の承認記録(グローバル層からのみ読む)と未承認通知のクールダウン秒
    "trusted_projects": {},
    "notice_cooldown_sec": 3600,
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
        cfg["_notices"] = []
        return cfg


def _load_config(cwd: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    errors: list[str] = []
    notices: list[str] = []
    project_path = Path(cwd or ".") / PROJECT_CONFIG_NAME
    for path in (GLOBAL_CONFIG_PATH, project_path):
        try:
            if not path.is_file():
                continue
            raw = path.read_bytes()
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        if path is project_path:
            # プロジェクト層は信頼できない入力。グローバル層の検証後に承認を判定し、
            # 非承認なら JSON として解析しない(ハッシュ計算のため生バイト列だけ読む)。
            verdict = trust.gate(
                raw, cwd, cfg["trusted_projects"],
                trust.cooldown_seconds(cfg["notice_cooldown_sec"]),
            )
            notices.extend(verdict.notices)
            if not verdict.adopt:
                continue
        try:
            # 不正UTF-8は UnicodeDecodeError(ValueError)、JSON構文エラーは
            # JSONDecodeError(ValueError)、深いネストは RecursionError を送出する。
            # 承認済みなら手順で読んだバイト列そのものを解析する(再オープンしない)
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path}: オブジェクトではありません")
            continue
        # 層ごとにマージ直後へ検証を挟む。不正値の縮退先は最下層(ビルトイン既定)
        # ではなく「その層をマージする前の状態」= 直下の層である。
        # deepcopyは _merge が未変更セクションを base と共有するための別名化を断つ。
        cfg = _validate(copy.deepcopy(_merge(cfg, data)), cfg, errors)
    cfg["_errors"] = errors
    cfg["_notices"] = notices
    return cfg


def _validate(cfg: dict, fallback: dict, errors: list) -> dict:
    """1層分のマージ結果を検証し、不正値を`fallback`(直下の層)の値へ戻す。

    層構造の意味は「上位が下位を上書きする」であり、上位層が壊れた値を持ち込んだ
    ときの縮退先はビルトイン既定ではなく直下の層である。最下層へ戻すと、中間層
    (グローバル設定)でユーザーが行った強化までプロジェクト設定から消せてしまう。
    型のスキーマは`DEFAULTS`から取り、採用する値は`fallback`から取る。
    `fallback` は常に検証済みの完全な設定(初層は DEFAULTS の deepcopy、以降は前層の
    検証結果)なので、全キーが正しい型で存在することを前提に直接参照する。
    """
    def revert(container: dict, key, source, label: str, value) -> None:
        errors.append(f"{label}: 不正な値 {value!r} のため無視しました(下位層の値を使用)")
        container[key] = copy.deepcopy(source)

    for key, default_value in DEFAULTS.items():
        if not isinstance(cfg.get(key), type(default_value)):
            revert(cfg, key, fallback[key], key, cfg.get(key))
    for (section, sub_key), allowed in _ENUM_KEYS.items():
        value = cfg[section].get(sub_key)
        if value not in allowed:
            revert(cfg[section], sub_key, fallback[section][sub_key], f"{section}.{sub_key}", value)
    fallback_categories = fallback["exfil_guard"]["categories"]
    categories = cfg["exfil_guard"].get("categories")
    if not isinstance(categories, dict):
        revert(
            cfg["exfil_guard"], "categories", fallback_categories,
            "exfil_guard.categories", categories,
        )
        categories = cfg["exfil_guard"]["categories"]
    for cat_key, cat_value in list(categories.items()):
        if cat_value in _CATEGORY_ACTIONS:
            continue
        label = f"exfil_guard.categories.{cat_key}"
        if cat_key in fallback_categories:
            revert(categories, cat_key, fallback_categories[cat_key], label, cat_value)
        else:
            errors.append(f"{label}: 不正な値 {cat_value!r} のため無視しました")
            del categories[cat_key]
    for section, sub_key in (
        ("bash_guard", "protected_branches"),
        ("secrets_guard", "write_protected_paths"),
    ):
        value = cfg[section].get(sub_key)
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            revert(cfg[section], sub_key, fallback[section][sub_key], f"{section}.{sub_key}", value)
    image = cfg["scanners"].get("gitleaks_image")
    if not isinstance(image, str):
        source = fallback["scanners"]["gitleaks_image"]
        revert(cfg["scanners"], "gitleaks_image", source, "scanners.gitleaks_image", image)
    gitleaks_config = cfg["scanners"].get("gitleaks_config")
    if gitleaks_config is not None and not isinstance(gitleaks_config, str):
        source = fallback["scanners"]["gitleaks_config"]
        revert(
            cfg["scanners"], "gitleaks_config", source,
            "scanners.gitleaks_config", gitleaks_config,
        )
    return cfg
