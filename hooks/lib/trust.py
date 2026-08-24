"""プロジェクト設定(.claude-hooks.json)のオプトイン信頼。

リポジトリ同梱の設定は信頼できない入力である。グローバル設定の `trusted_projects` による
承認(内容ハッシュ / ピン留めなし true / 不承認 false)が無い限りマージしない。
無視したことは通知で可視化する(未承認はクールダウン、ハッシュ不一致は常に、
ピン留めなしは内容が変化した回のみ)。この module は例外を外に出さない。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

STATE_PATH = Path.home() / ".claude" / "safe-dev-hooks-state.json"
HASH_PREFIX = "sha256:"
DEFAULT_COOLDOWN_SEC = 3600
GLOBAL_CONFIG_HINT = "$HOME/.claude/claude-hooks.json"
_HEX = set("0123456789abcdef")


@dataclass
class Verdict:
    adopt: bool
    notices: list[str] = field(default_factory=list)


def content_hash(raw: bytes) -> str:
    """raw の SHA-256 を16進化し、`HASH_PREFIX` を付けて返す。"""
    return HASH_PREFIX + hashlib.sha256(raw).hexdigest()


def project_key(cwd: str | None) -> str:
    """cwd(無ければ ".")の実パスを trusted_projects のキーとして返す。"""
    return os.path.realpath(cwd or ".")


def classify_entry(value) -> tuple[str, str | None]:
    """trusted_projects の 1 エントリを分類する。真偽値は `is True` / `is False` で厳密に判定。"""
    if value is True:
        return "unpinned", None
    if value is False:
        return "denied", None
    if isinstance(value, str):
        low = value.lower()
        if low.startswith(HASH_PREFIX):
            digest = low[len(HASH_PREFIX):]
            if len(digest) == 64 and set(digest) <= _HEX:
                return "pinned", low
    return "ignored", None


def cooldown_seconds(value) -> int:
    """通知クールダウン秒数を検証する。bool でない 0 以上の int 以外は既定値。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_COOLDOWN_SEC
    return value


def untrusted_notice(key: str, digest: str) -> str:
    """未承認のため無視したことを知らせる通知文を返す。"""
    return (
        "[safe-dev-hooks] このプロジェクトの .claude-hooks.json は未承認のため無視しました。\n"
        f"内容を確認のうえ承認する場合は {GLOBAL_CONFIG_HINT} の\n"
        '"trusted_projects" に次を追加してください:\n'
        f'  "{key}": "{digest}"\n'
        "承認するとこの設定はガードの deny 判定とコマンド実行に対する権限を持ちます。"
    )


def mismatch_notice(key: str, digest: str) -> str:
    """承認後にハッシュが変わった(不一致)ため無視したことを知らせる通知文を返す。"""
    return (
        "[safe-dev-hooks] 警告: このプロジェクトの .claude-hooks.json は"
        "承認後に変更されています。\n"
        "安全のため無視しました。差分を確認し、意図した変更であれば\n"
        '"trusted_projects" のハッシュを次の値へ更新してください:\n'
        f'  "{key}": "{digest}"'
    )


def unpinned_changed_notice(key: str, digest: str) -> str:
    """ピン留めなし承認(true)で内容が変化した回にのみ出す通知文を返す。"""
    return (
        "[safe-dev-hooks] このプロジェクトの .claude-hooks.json は前回から変更されていますが、\n"
        "ピン留めなし承認(true)のため、そのまま採用しました。\n"
        "内容を確認する場合: git diff -- .claude-hooks.json\n"
        '内容ごとに承認したい場合は "trusted_projects" の値を次のハッシュへ変えてください:\n'
        f'  "{key}": "{digest}"'
    )


def load_state(path: Path | None = None) -> dict | None:
    """状態ファイルを読む。無ければ {}、読めない/壊れていれば None。"""
    p = Path(path or STATE_PATH)
    try:
        if not p.is_file():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def save_state(state: dict, path: Path | None = None) -> bool:
    """状態ファイルへ書き込む。親ディレクトリが無ければ作成し、失敗時は False を返す。"""
    p = Path(path or STATE_PATH)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return False
    return True


def _section(state: dict, name: str) -> dict:
    value = state.get(name)
    if not isinstance(value, dict):
        value = {}
        state[name] = value
    return value


def _untrusted(key: str, digest: str, cooldown: int, now: float | None, state_path) -> list[str]:
    # 状態ファイルが使えなければ通知する側に倒す(可視性優先)
    now = time.time() if now is None else now
    state = load_state(state_path)
    if state is None:
        state = {}
    last = _section(state, "notice_last").get(key)
    if isinstance(last, (int, float)) and not isinstance(last, bool) and now - last < cooldown:
        return []
    state["notice_last"][key] = now
    save_state(state, state_path)
    return [untrusted_notice(key, digest)]


def _unpinned(key: str, digest: str, state_path) -> list[str]:
    # 状態ファイルが使えなければ「変化なし」とみなして通知しない(採用自体は承認済み)
    state = load_state(state_path)
    if state is None:
        return []
    seen = _section(state, "unpinned_seen")
    previous = seen.get(key)
    seen[key] = digest
    if not save_state(state, state_path):
        return []
    if isinstance(previous, str) and previous != digest:
        return [unpinned_changed_notice(key, digest)]
    return []


def gate(
    raw: bytes,
    cwd: str | None,
    trusted_projects,
    cooldown_sec: int,
    *,
    now: float | None = None,
    state_path: Path | None = None,
    notices: bool = True,
) -> Verdict:
    """プロジェクト設定(生バイト列)を採用するか判定し、出すべき通知を返す。例外を出さない。

    実体は `_gate`。ここでの try/except は最後の砦 — raw/cwd の型不正やハッシュ化・パス
    解決で例外が起きても、Hook プロセスごと落として deny 層を素通りさせないよう、この
    境界で捕捉して安全側(不採用)に倒す。呼び出し元(config.py)の外側の保護に頼らず、
    gate() 自身が「例外を出さない」という契約を守る。

    `notices=False` は通知を表示しない呼び出し(`audit_log`)用。通知文を作らないだけで
    なく、クールダウン(`notice_last`)と変化検知(`unpinned_seen`)の**状態を一切進めない**。
    表示しない呼び出しが枠を消費すると、後続の対話フックの通知が抑止されてしまうため。
    採用/不採用の判定は `notices` に依存しない(deny 層の挙動はこのフラグで変わらない)。
    """
    try:
        return _gate(
            raw, cwd, trusted_projects, cooldown_sec,
            now=now, state_path=state_path, notices=notices,
        )
    except Exception as e:
        return Verdict(
            False,
            [
                "safe-dev-hooks: プロジェクト設定の信頼判定に失敗したため無視しました: "
                f"{type(e).__name__}: {e}"
            ],
        )


def _gate(
    raw: bytes,
    cwd: str | None,
    trusted_projects,
    cooldown_sec: int,
    *,
    now: float | None = None,
    state_path: Path | None = None,
    notices: bool,
) -> Verdict:
    cooldown_sec = cooldown_seconds(cooldown_sec)
    key = project_key(cwd)
    digest = content_hash(raw)
    entries = trusted_projects if isinstance(trusted_projects, dict) else {}
    kind, expected = classify_entry(entries.get(key))
    if kind == "pinned":
        adopt = expected == digest
        # 通知を出さない呼び出しでも採用判定は同じ。通知文だけを作らない。
        if not notices:
            return Verdict(adopt)
        if adopt:
            return Verdict(True)
        return Verdict(False, [mismatch_notice(key, digest)])
    if kind == "denied":
        return Verdict(False)
    if not notices:
        # 静かな呼び出しでは _unpinned / _untrusted を通らない = state を書かない。
        return Verdict(kind == "unpinned")
    if kind == "unpinned":
        return Verdict(True, _unpinned(key, digest, state_path))
    return Verdict(False, _untrusted(key, digest, cooldown_sec, now, state_path))


def skipped_notice(key: str, root: str) -> str:
    """基準ディレクトリと異なる場所にあり読まなかった .claude-hooks.json を知らせる通知文を返す。

    D2: 0.7.0 の「無視した設定は必ず通知する」原則を、この経路にも適用する。
    """
    return (
        f"[safe-dev-hooks] {key} の .claude-hooks.json は、\n"
        f"プロジェクトの基準ディレクトリ({root})とは異なる場所にあるため読みませんでした。\n"
        "プロジェクト設定は基準ディレクトリのものだけが読まれます。\n"
        "この場所の設定を有効にしたい場合は、内容を基準ディレクトリの\n"
        ".claude-hooks.json へ統合してください。"
    )


def rejected_env_notice(key: str, root: str) -> str:
    """不採用にした CLAUDE_PROJECT_DIR 配下の .claude-hooks.json を知らせる通知文を返す。

    N1: 0.7.1 の祖先制約で環境変数由来のアンカーを不採用にすると、そのプロジェクトの
    設定層が丸ごと落ちる。落ちた設定は cwd の祖先ではなく別枝にあるので
    `skipped_notice` の経路(祖先の探索)では拾えない。理由が違えば利用者が取るべき
    行動も違う(基準ディレクトリへの統合ではなく「そのプロジェクト配下で作業する」)ため、
    文面を分けている。
    """
    return (
        f"[safe-dev-hooks] {key} の .claude-hooks.json は読みませんでした。\n"
        "環境変数 CLAUDE_PROJECT_DIR はこの場所を指していますが、現在の作業ディレクトリの\n"
        f"祖先ではないため、プロジェクトの基準として採用していません(現在の基準: {root})。\n"
        "この設定を有効にしたい場合は、そのプロジェクト配下のディレクトリで作業してください。"
    )


def notify_skipped(
    skipped_dir: str,
    root: str,
    cooldown_sec: int,
    *,
    now: float | None = None,
    state_path: Path | None = None,
) -> list[str]:
    """基準ディレクトリと異なる場所にあり読まなかった .claude-hooks.json を通知する。

    D2: クールダウン付き。実体は `_notify_skipped`。
    """
    return _notify_once(skipped_dir, root, cooldown_sec, skipped_notice, now, state_path)


def notify_rejected_env(
    env_dir: str,
    root: str,
    cooldown_sec: int,
    *,
    now: float | None = None,
    state_path: Path | None = None,
) -> list[str]:
    """検証で不採用にした CLAUDE_PROJECT_DIR 配下の .claude-hooks.json を通知する(N1)。

    クールダウンと状態(`skipped_last`)は `notify_skipped` と共有する — どちらも
    「見つけたのに読まなかった設定」の通知であり、場所ごとに独立した枠でよい。
    違うのは文面(=利用者が取るべき行動)だけ。
    """
    return _notify_once(env_dir, root, cooldown_sec, rejected_env_notice, now, state_path)


def _notify_once(skipped_dir, root, cooldown_sec, build, now, state_path) -> list[str]:
    """読まなかった設定の通知をクールダウン付きで 1 件返す共通経路。

    gate() と同じ理由で、ここでの try/except が最後の砦 —
    呼び出し元(config.py)を落とさないよう、この境界で捕捉して「通知なし」に倒す。
    可視性の追加機能であり deny 判定そのものではないため、失敗時は
    (gate() と違って)失敗自体を知らせる通知は出さず、静かに「通知なし」に倒す。
    `build` は通知文を組み立てる関数(`skipped_notice` / `rejected_env_notice`)。
    """
    try:
        return _notify_skipped(
            skipped_dir, root, cooldown_sec, build, now=now, state_path=state_path,
        )
    except Exception:
        return []


def _notify_skipped(
    skipped_dir: str,
    root: str,
    cooldown_sec: int,
    build,
    *,
    now: float | None = None,
    state_path: Path | None = None,
) -> list[str]:
    # 状態ファイルが使えなければ通知する側に倒す(可視性優先。_untrusted と同じ設計)
    cooldown_sec = cooldown_seconds(cooldown_sec)
    key = project_key(skipped_dir)
    now = time.time() if now is None else now
    state = load_state(state_path)
    if state is None:
        state = {}
    last = _section(state, "skipped_last").get(key)
    if isinstance(last, (int, float)) and not isinstance(last, bool) and now - last < cooldown_sec:
        return []
    state["skipped_last"][key] = now
    save_state(state, state_path)
    return [build(key, root)]
