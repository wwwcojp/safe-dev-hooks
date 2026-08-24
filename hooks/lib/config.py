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
    優先し(ただし `_env_root` の検証を通ったものだけ)、無ければ cwd の最近傍の git
    ルート、それも無ければ cwd に戻す(D1)。
    例外は投げない。cwd が None のときは git 探索を行わず None を返す。
    """
    env_root = _env_root(cwd)
    if env_root is not None:
        return env_root
    if cwd is None:
        return None
    git_root = _nearest_git_root(cwd)
    if git_root is not None:
        return git_root
    return cwd


def _env_root(cwd: str | None) -> str | None:
    """CLAUDE_PROJECT_DIR を検証し、基準として採用できる場合のみ返す(D3)。

    採用条件は次の 3 つ。満たさなければ None を返し、呼び出し元は git 探索へ落ちる。

    1. 空文字でない(空文字は未設定と同じ扱い)
    2. 実在するディレクトリである(相対パス・存在しないパス・通常ファイルを弾く)
    3. cwd 自身、または cwd の祖先である

    3 が要である。この環境変数はリポジトリ同梱の `.claude/settings.json` の `env` で
    差し替えられ得るため、無検証だと敵対的リポジトリが基準を無関係な場所へずらして
    (a) 本来のプロジェクト層を落とす、(b) 利用者が別途承認済みの他プロジェクトの
    緩和設定(`allow_paths`・`bash_guard.allow`)を持ち込む、の両方ができてしまう。
    ハーネスが注入する正規の値はセッション開始ディレクトリなので通常は cwd の祖先で
    あり、この制約は正当な用途を壊さない。`/add-dir` などでセッションルートの外を
    作業している場合はここで不採用になるが、その場合は「実際に作業しているディレクトリ
    の git ルート」が基準になる — 異常ではなく、むしろそちらが正しいアンカーである。
    不採用にした値は無言では捨てない: そこに `.claude-hooks.json` があれば
    `_rejected_env_dir` が拾い、落ちたプロジェクト層として通知する(N1)。
    例外は投げない。
    """
    value = os.environ.get(PROJECT_DIR_ENV)
    if not value:
        return None
    try:
        if not os.path.isdir(value):
            return None
        if cwd is None:
            return value  # 比較対象が無い(祖先判定ができない)ので値をそのまま使う
        env_path = Path(os.path.realpath(value))
        cwd_path = Path(os.path.realpath(cwd))
        if cwd_path == env_path or env_path in cwd_path.parents:
            return value
    except (OSError, ValueError):
        return None
    return None


def _rejected_env_dir(cwd: str | None) -> str | None:
    """検証で不採用にした CLAUDE_PROJECT_DIR のうち、通知すべき値を返す(N1)。

    `_env_root` の祖先制約は「別プロジェクトの承認済み設定の持ち込み」を塞ぐが、
    プロジェクト外へ `cd` しただけ(`cd /tmp`・`cd ~`・git リポジトリでない親へ `cd ..`)
    でも env は不採用になり、本来のプロジェクト層が丸ごと落ちる。落ちた設定は cwd の
    祖先ではなく別の枝にあるため、`_skipped_config_dirs` の祖先探索では拾えない。
    ここで別枝として拾い、「見つけたのに読まなかった設定は必ず通知する」を経路に
    よらず成立させる。**採用はしない** — 祖先制約(=持ち込み封じ)は一切緩めず、
    可視性だけを足す。
    そこに `.claude-hooks.json` が無ければ落ちた保護も無いので何も返さない。
    例外は投げない(`os.path.isfile` は OSError/ValueError を飲む)。
    """
    value = os.environ.get(PROJECT_DIR_ENV)
    if not value:
        return None
    if _env_root(cwd) is not None:
        return None  # 採用された = 落ちていない
    if not os.path.isfile(os.path.join(value, PROJECT_CONFIG_NAME)):
        return None
    return value


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
    """基準(root)以外の場所にあるため読まなかった .claude-hooks.json を通知する(D2)。

    0.7.1 でプロジェクト設定の探索基準を event["cwd"] から project_root(cwd) へ移した
    ことにより、モノレポのサブパッケージ設定などが無言で読まれなくなる経路ができた。
    無視した設定は必ず通知する(0.7.0 の原則)ため、ここで存在だけを確認し
    (JSONとして解析はしない — 未承認の内容を解析対象にする必要は無い)、通知文と
    クールダウンの管理は trust.py に委ねる。通知は場所ごとに独立したクールダウンを持つ。
    対象は 2 経路ある。cwd とその祖先(`_skipped_config_dirs`)と、cwd の祖先ではない
    別の枝にあるため祖先探索では拾えない「不採用にした CLAUDE_PROJECT_DIR」
    (`_rejected_env_dir`。N1)である。
    root は project_root(cwd) の戻り値。project_root は cwd が非 None のとき常に str を
    返す契約なので、cwd が None でない限り root も None にはならない。cwd が None のとき
    (event に cwd が無い呼び出し)は比較する対象自体が無いため何もしない。
    """
    if cwd is None:
        return []
    cooldown = trust.cooldown_seconds(cooldown_raw)
    out: list[str] = []
    for skipped in _skipped_config_dirs(cwd, root):
        out.extend(trust.notify_skipped(skipped, root, cooldown))
    rejected = _rejected_env_dir(cwd)
    if rejected is not None:
        out.extend(trust.notify_rejected_env(rejected, root, cooldown))
    return out


def _skipped_config_dirs(cwd: str, root: str | None) -> list[str]:
    """cwd とその祖先のうち、基準(root)以外で .claude-hooks.json を持つ場所を列挙する。

    cwd 直下だけを見ると、次の 2 つの「無言の保護喪失」を通知できない。どちらも
    cwd 自身には設定ファイルが無く、落ちる設定は cwd の祖先にあるためである。

    - 環境変数 CLAUDE_PROJECT_DIR で基準を上位ディレクトリへずらされ、本来の
      プロジェクトルート直下の設定が落ちる(D3。`_env_root` の祖先制約と対になる)
    - vendored clone・submodule・worktree のようにネストした `.git` が基準を
      下位へ移し、親プロジェクトの設定が落ちる

    祖先も含めて列挙することで「見つけたのに読まなかった設定は必ず通知する」を
    経路によらず成立させる。比較は realpath 化した実パスで行う(trust.project_key
    と同じ基準なので、通知のキーと承認キーが同じ表記になる)。
    """
    root_real = os.path.realpath(root)
    start = Path(os.path.realpath(cwd))
    found: list[str] = []
    for d in [start, *start.parents]:
        if str(d) == root_real:
            continue
        # Path.is_file() は 3.10-3.13 で読めない祖先に対し PermissionError を送出する
        # (3.14+ は飲む。実測: 3.10.12/3.11.15/3.12.13/3.13.14 は送出、3.14.6 は飲む)。
        # os.path.isfile は全 OSError/ValueError を飲むのでこちらを使う。
        if os.path.isfile(d / PROJECT_CONFIG_NAME):
            found.append(str(d))
    return found


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
