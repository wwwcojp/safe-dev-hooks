"""3層マージ設定(ビルトイン既定 ← グローバル ← プロジェクト)。"""
import copy
import json
import os
from pathlib import Path

from . import trust

GLOBAL_CONFIG_PATH = Path.home() / ".claude" / "claude-hooks.json"
PROJECT_CONFIG_NAME = ".claude-hooks.json"
PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"

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


def project_root(cwd: str | None = None) -> str | None:
    """相対パスとプロジェクト設定の基準を返す。

    event["cwd"] は Bash の cd に追従する一時的な作業ディレクトリなので基準にできない。
    Claude Code がフックに渡すセッション開始時のプロジェクトルート CLAUDE_PROJECT_DIR を
    優先し、無ければ cwd の最近傍の git ルート、それも無ければ cwd に戻す(D1)。
    例外は投げない。cwd が None のときは git 探索を行わず None を返す。
    """
    env_root = os.environ.get(PROJECT_DIR_ENV)
    if env_root:
        return env_root
    if cwd is None:
        return None
    git_root = _nearest_git_root(cwd)
    if git_root is not None:
        return git_root
    return cwd


def _nearest_git_root(cwd: str) -> str | None:
    """cwd から見た最近傍の git ルート(祖先に `.git` を持つ最初のディレクトリ)を返す。

    `.git` は worktree ではファイルなので `is_dir()` でなく `exists()` で判定する。
    途中で `OSError` が起きても例外を外へ出さず探索を打ち切る(None を返す)。
    """
    try:
        candidates = [Path(cwd), *Path(cwd).parents]
        for d in candidates:
            if (d / ".git").exists():
                return str(d)
    except OSError:
        return None
    return None


def load_config(cwd: str | None = None, *, notices: bool = True) -> dict:
    """設定を読み込む。この関数は例外を送出しない。

    設定ファイルはリポジトリ由来の信頼できない入力であり、ここで例外が漏れると
    Hookが判定前に異常終了して deny 層ごと素通りする。どんな異常でもビルトイン
    既定値へフォールバックし、`_errors` に記録して可視化する(fail-safe)。

    `notices=False` は通知を表示しない呼び出し(`audit_log`)用。通知の生成そのものを
    行わないため、クールダウン(`notice_last`/`skipped_last`)と変化検知(`unpinned_seen`)
    の状態も進めない。`audit_log` は SessionStart と全 PreToolUse/PostToolUse で走る
    最頻フックであり、表示しないのに枠だけ消費すると以後 1 時間、対話フック側の通知が
    まるごと抑止されてしまう(0.7.1 以前の実挙動)。**採用/不採用の判定はこのフラグに
    依存しない** — deny 層の挙動が通知の有無で変わってはならない。
    """
    try:
        return _load_config(cwd, notices=notices)
    except Exception as exc:  # 想定外の異常でもガードを死なせない
        cfg = copy.deepcopy(DEFAULTS)
        cfg["_errors"] = [f"設定の読み込みに失敗したため既定値を使用します: {exc}"]
        cfg["_notices"] = []
        return cfg


def _read_layer(path: Path, errors: list) -> bytes | None:
    """層のファイルを生バイト列で読む。無ければ None、読めなければ errors に記録して None。"""
    try:
        if not path.is_file():
            return None
        return path.read_bytes()
    except OSError as exc:
        errors.append(f"{path}: {exc}")
        return None


def _apply_layer(cfg: dict, path: Path, raw: bytes, errors: list) -> dict:
    """1層分の生バイト列を解析・マージ・検証して新しい cfg を返す。解析できなければ cfg のまま。"""
    try:
        # 不正UTF-8は UnicodeDecodeError(ValueError)、JSON構文エラーは
        # JSONDecodeError(ValueError)、深いネストは RecursionError を送出する。
        # bytes.decode() の既定は言語仕様で utf-8(open() と違いロケール非依存)。
        data = json.loads(raw.decode())
    except (ValueError, RecursionError) as exc:
        errors.append(f"{path}: {exc}")
        return cfg
    if not isinstance(data, dict):
        errors.append(f"{path}: オブジェクトではありません")
        return cfg
    # 層ごとにマージ直後へ検証を挟む。不正値の縮退先は最下層(ビルトイン既定)
    # ではなく「その層をマージする前の状態」= 直下の層である。
    # deepcopyは _merge が未変更セクションを base と共有するための別名化を断つ。
    return _validate(copy.deepcopy(_merge(cfg, data)), cfg, errors)


def _load_config(cwd: str | None = None, *, notices: bool) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    errors: list[str] = []
    collected: list[str] = []
    # グローバル層: ユーザー自身の設定。読めれば無条件にマージする。
    raw = _read_layer(GLOBAL_CONFIG_PATH, errors)
    if raw is not None:
        cfg = _apply_layer(cfg, GLOBAL_CONFIG_PATH, raw, errors)
    # プロジェクト層: 信頼できない入力。グローバル層の検証後に承認を判定し、
    # 非承認なら JSON として解析しない(ハッシュ計算のため生バイト列だけ読む)。
    # 承認済みなら手順で読んだバイト列そのものを解析する(再オープンしない)。
    # 基準は cwd そのものではなく project_root(cwd)(D1): event["cwd"] は Bash の cd に
    # 追従する一時的な値なので、サブディレクトリに居るだけで設定が読めなくなるのを防ぐ。
    root = project_root(cwd)
    project_path = Path(root or ".") / PROJECT_CONFIG_NAME
    raw = _read_layer(project_path, errors)
    if raw is not None:
        verdict = trust.gate(
            raw, root, cfg["trusted_projects"],
            trust.cooldown_seconds(cfg["notice_cooldown_sec"]),
            notices=notices,
        )
        collected.extend(verdict.notices)
        if verdict.adopt:
            cfg = _apply_layer(cfg, project_path, raw, errors)
    if notices:
        collected.extend(_skipped_notices(cwd, root, cfg["notice_cooldown_sec"]))
    cfg["_errors"] = errors
    cfg["_notices"] = collected
    return cfg


def _skipped_notices(cwd: str | None, root: str | None, cooldown_raw) -> list[str]:
    """cwd に .claude-hooks.json があるのに基準(root)が別ディレクトリで読まなかった場合の通知(D2)。

    0.7.1 でプロジェクト設定の探索基準を event["cwd"] から project_root(cwd) へ移した
    ことにより、モノレポのサブパッケージ設定などが無言で読まれなくなる経路ができた。
    無視した設定は必ず通知する(0.7.0 の原則)ため、ここで cwd 側の存在だけを確認し
    (JSONとして解析はしない — 未承認の内容を解析対象にする必要は無い)、通知文と
    クールダウンの管理は trust.py に委ねる。
    root は project_root(cwd) の戻り値。project_root は cwd が非 None のとき常に str を
    返す契約なので、cwd が None でない限り root も None にはならない。cwd が None のとき
    (event に cwd が無い呼び出し)は比較する対象自体が無いため何もしない。
    """
    if cwd is None:
        return []
    if os.path.realpath(cwd) == os.path.realpath(root):
        return []
    if not (Path(cwd) / PROJECT_CONFIG_NAME).is_file():
        return []
    return trust.notify_skipped(cwd, root, trust.cooldown_seconds(cooldown_raw))


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
