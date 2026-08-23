# プロジェクト設定のオプトイン信頼(0.7.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** リポジトリ同梱の `.claude-hooks.json`(プロジェクト層)を、グローバル設定 `trusted_projects` による明示的な承認(内容ハッシュ / ピン留めなし `true` / 不承認 `false`)が無い限りマージせず、無視したことを通知(クールダウン付き)で可視化する。

**Architecture:** 承認判定・ハッシュ・通知文面・状態ファイル(クールダウン / ピン留めなしの変化検出)を新モジュール `hooks/lib/trust.py` に閉じ込め、`config._load_config` はプロジェクト層のバイト列を読んだ直後に `trust.gate()` を呼んで「採用するか・通知は何か」だけを受け取る(非承認なら JSON として解析しない)。通知は `cfg["_notices"]` に載せ、`hook_io.finalize` が `_errors` と同様に一元合成する(`audit_log` のみ `quiet_notices=True`)。0.6.1 の層ごと検証(`_validate`)はそのまま使い、`DEFAULTS` に `trusted_projects` / `notice_cooldown_sec` を足すことで型検証に乗せる。

**Tech Stack:** Python 3.10+ stdlib(hashlib / json / os / time / pathlib / dataclasses)、uv、pytest、ruff、mutmut(`scripts/verify.py mutation`)

**Spec:** `docs/superpowers/specs/2026-07-26-project-config-trust-optin-design.md`(第4版)

## Global Constraints

- 実行時依存ゼロ(stdlib のみ)。`requires-python = ">=3.10"`
- **非承認のプロジェクト設定は JSON として解析しない**(原則1)。ハッシュ計算のためのバイト列読取のみ
- 承認は内容に対して行う。キーは `os.path.realpath(cwd)`、値は `"sha256:" + 生バイト列の SHA-256(16進64桁)` / `true`(ピン留めなし)/ `false`(明示的な不承認)。真偽値は `value is True` / `value is False` で厳密判定(`"true"`・`1`・`[]` は承認にならない)
- **信頼判定の機構は例外を投げない**(原則4)。`load_config` の外側 except(全層が DEFAULTS へ落ちる)へ到達させない
- `trusted_projects` はグローバル層からのみ読む(承認判定はグローバル層の `_validate` 後・プロジェクト層のマージ前)。`DEFAULTS["trusted_projects"] = {}`、`DEFAULTS["notice_cooldown_sec"] = 3600`
- 通知の配線は `cfg["_notices"]` → `hook_io.finalize` の 1 箇所。`audit_log` は通知を出さない(`quiet_notices=True`)。未承認はクールダウン(既定 3600 秒、`notice_cooldown_sec`、`0` で毎回)、ハッシュ不一致は常に通知、ピン留めなしは内容が変化した回のみ通知、`false` は通知しない
- 状態ファイル `$HOME/.claude/safe-dev-hooks-state.json`(`{"notice_last": {...}, "unpinned_seen": {...}}`)。読めない/壊れている/書けないとき: 未承認通知は**通知する側**に倒す、ピン留めなしの変化通知は**出さない**(採用は継続)
- 通知文面は spec #3 の 3 種をそのまま使う(承認エントリ `"<realpath>": "sha256:..."` をフックが計算して印字。`$HOME/.claude/claude-hooks.json` は文字どおり `$HOME` 表記)
- `hooks/`・`rules/` は自インストールの write_protected で Edit/Write 不可 → **Bash 経由の python 書込(読み→置換→書き戻し)**。`hooks/` は稼働中のガードなので書換直後に pytest と hook の直接実行スモーク。Bash 書込はゲート外なので `uv run python scripts/verify.py quick` を明示実行
- Loop Engineering: `hooks/lib/config.py`(baseline 99.1)・`hooks/lib/hook_io.py`(98.7)は mutation 対象。新モジュール `hooks/lib/trust.py` を `only_mutate` に追加し、各タスク完了時に `uv run python scripts/verify.py mutation` が exit 0(baseline を下回らない。等価変異は pragma でなく設計で消す — 到達不能な既定値つき `.get` を書かない)
- 実ホームパスをリポジトリに書かない(テストは `tmp_path` / `/home/alice`)。コミットメッセージに危険コマンドの字面や保護ファイルへのリダイレクト字面を書かない
- テストでは状態ファイルを実 `$HOME` に書かない(conftest の autouse fixture で `trust.STATE_PATH` を tmp に向ける)
- 作業ブランチ: `feat/project-config-trust-optin`(main から作成。main = 0.6.1 + spec 第4版)

## File Structure

| ファイル | 責務 |
|---|---|
| Create `hooks/lib/trust.py` | 承認判定(`gate`)、ハッシュ、エントリ分類、通知文面、状態ファイル(クールダウン / unpinned_seen)。例外を外に出さない |
| Modify `hooks/lib/config.py` | `DEFAULTS` 2 キー追加、`_load_config` で `read_bytes` → プロジェクト層のみ `trust.gate` → 採用時のみ同じバイト列を解析、`cfg["_notices"]` |
| Modify `hooks/lib/hook_io.py` | `finalize(out, cfg, quiet_notices=False)`: `_notices` を合成 |
| Modify `hooks/audit/audit_log.py` | 2 箇所の `finalize` に `quiet_notices=True` |
| Create `tests/test_trust.py` | trust.py の単体テスト(分類・判定・通知文面・状態ファイル・クールダウン) |
| Modify `tests/conftest.py` | autouse: `trust.STATE_PATH` を tmp へ |
| Modify `tests/helpers.py` | `approve_project(monkeypatch, global_path, proj, global_cfg=None, pinned=False)` |
| Modify `tests/test_config.py` + 他テスト | 既存「プロジェクト設定を適用する」テストを承認付きに移行。新規: 非承認で無効・自己承認不可・型不正の縮退・不正 UTF-8 の扱い |
| Create `tests/test_trust_blackbox.py` | 非承認プロジェクト設定の全シンクが無効(unit)+ hook のサブプロセス deny 維持(blackbox) |
| Modify `tests/test_hook_io.py`・`tests/test_audit_and_notify.py` | `_notices` 合成・`quiet_notices` |
| Modify `pyproject.toml` | `only_mutate` に `hooks/lib/trust.py`、`0.7.0` |
| Modify `docs/configuration.md`・`docs/security-model.md`・`CONTRIBUTING.md`・`README.md`・`README.ja.md`・`CHANGELOG.md`・`.claude-plugin/plugin.json` | 信頼層・承認手順・破壊的変更・0.7.0 |

---

### Task 1: `hooks/lib/trust.py` — 承認判定・通知文面・状態ファイル

**Files:**
- Create: `hooks/lib/trust.py`(Bash 経由の python 書込 — `hooks/lib/` は write_protected)
- Create: `tests/test_trust.py`
- Modify: `tests/conftest.py`(autouse fixture)
- Modify: `pyproject.toml`(`only_mutate` に追加)

**Interfaces:**
- Produces(Task 2 以降が使う):
  - `STATE_PATH: Path`(既定 `Path.home()/".claude"/"safe-dev-hooks-state.json"`、テストは monkeypatch で差し替え)
  - `DEFAULT_COOLDOWN_SEC = 3600`、`HASH_PREFIX = "sha256:"`
  - `content_hash(raw: bytes) -> str`(`"sha256:"+hex`)
  - `project_key(cwd: str | None) -> str`(`os.path.realpath(cwd or ".")`)
  - `classify_entry(value) -> tuple[str, str | None]`(`("pinned", "sha256:…小文字")` / `("unpinned", None)` / `("denied", None)` / `("ignored", None)`)
  - `cooldown_seconds(value) -> int`(bool でない 0 以上の int 以外は 3600)
  - `untrusted_notice(key, digest) -> str`・`mismatch_notice(key, digest) -> str`・`unpinned_changed_notice(key, digest) -> str`
  - `load_state(path=None) -> dict | None`(無ければ `{}`、壊れていれば `None`)・`save_state(state, path=None) -> bool`
  - `@dataclass Verdict(adopt: bool, notices: list[str])`
  - `gate(raw: bytes, cwd: str | None, trusted_projects, cooldown_sec: int, *, now: float | None = None, state_path: Path | None = None) -> Verdict`

- [ ] **Step 1: conftest に状態ファイル隔離の autouse fixture を追加**

`tests/conftest.py` の既存 `_hide_external_secret_scanners` fixture の**後**に追加(`from hooks.lib import trust` はファイル先頭の import 群の最後に。`sys.path.insert(0, str(REPO_ROOT))` より後でないと import できないので、関数内 import にする):

```python
@pytest.fixture(autouse=True)
def _isolate_trust_state(monkeypatch, tmp_path):
    """信頼判定の状態ファイルを実 $HOME に書かない(テストごとに tmp へ)。"""
    from hooks.lib import trust

    monkeypatch.setattr(trust, "STATE_PATH", tmp_path / "safe-dev-hooks-state.json")
```

- [ ] **Step 2: 失敗するテストを書く** — `tests/test_trust.py` を作成:

```python
"""hooks/lib/trust.py(プロジェクト設定のオプトイン信頼)のテスト。"""
import json
import os

import pytest
from hooks.lib import trust

RAW = b'{"bash_guard": {"allow": ["x"]}}'
DIGEST = "sha256:" + __import__("hashlib").sha256(RAW).hexdigest()


def test_content_hash_is_sha256_of_raw_bytes_with_prefix():
    assert trust.content_hash(RAW) == DIGEST
    assert trust.content_hash(b"") == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_project_key_is_realpath(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert trust.project_key(str(link)) == os.path.realpath(str(real))
    assert trust.project_key(None) == os.path.realpath(".")


@pytest.mark.parametrize("value, expected", [
    (True, ("unpinned", None)),
    (False, ("denied", None)),
    (DIGEST, ("pinned", DIGEST)),
    (DIGEST.upper().replace("SHA256:", "sha256:"), ("pinned", DIGEST)),  # 16進は大小無視
    ("SHA256:" + DIGEST[7:], ("pinned", DIGEST)),                        # 接頭辞も大小無視
    ("true", ("ignored", None)),
    (1, ("ignored", None)),
    (0, ("ignored", None)),
    ([], ("ignored", None)),
    ({}, ("ignored", None)),
    (None, ("ignored", None)),
    ("yes", ("ignored", None)),
    ("md5:" + "a" * 32, ("ignored", None)),
    ("sha256:" + "a" * 63, ("ignored", None)),
    ("sha256:" + "a" * 65, ("ignored", None)),
    ("sha256:" + "g" * 64, ("ignored", None)),
    (DIGEST[7:], ("ignored", None)),  # 接頭辞なし
])
def test_classify_entry(value, expected):
    assert trust.classify_entry(value) == expected


@pytest.mark.parametrize("value, expected", [
    (3600, 3600), (0, 0), (5, 5),
    (-1, 3600), (True, 3600), (False, 3600), ("60", 3600), (None, 3600), (1.5, 3600),
])
def test_cooldown_seconds(value, expected):
    assert trust.cooldown_seconds(value) == expected


def test_untrusted_notice_exact_text():
    assert trust.untrusted_notice("/home/alice/proj", DIGEST) == (
        "[safe-dev-hooks] このプロジェクトの .claude-hooks.json は未承認のため無視しました。\n"
        "内容を確認のうえ承認する場合は $HOME/.claude/claude-hooks.json の\n"
        '"trusted_projects" に次を追加してください:\n'
        f'  "/home/alice/proj": "{DIGEST}"\n'
        "承認するとこの設定はガードの deny 判定とコマンド実行に対する権限を持ちます。"
    )


def test_mismatch_notice_exact_text():
    assert trust.mismatch_notice("/home/alice/proj", DIGEST) == (
        "[safe-dev-hooks] 警告: このプロジェクトの .claude-hooks.json は承認後に変更されています。\n"
        "安全のため無視しました。差分を確認し、意図した変更であれば\n"
        '"trusted_projects" のハッシュを次の値へ更新してください:\n'
        f'  "/home/alice/proj": "{DIGEST}"'
    )


def test_unpinned_changed_notice_exact_text():
    assert trust.unpinned_changed_notice("/home/alice/proj", DIGEST) == (
        "[safe-dev-hooks] このプロジェクトの .claude-hooks.json は前回から変更されていますが、\n"
        "ピン留めなし承認(true)のため、そのまま採用しました。\n"
        "内容を確認する場合: git diff -- .claude-hooks.json\n"
        '内容ごとに承認したい場合は "trusted_projects" の値を次のハッシュへ変えてください:\n'
        f'  "/home/alice/proj": "{DIGEST}"'
    )


# ---- 状態ファイル ----


def test_load_state_missing_is_empty_dict(tmp_path):
    assert trust.load_state(tmp_path / "none.json") == {}


def test_load_state_broken_is_none(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{broken", encoding="utf-8")
    assert trust.load_state(p) is None
    p.write_text("[]", encoding="utf-8")
    assert trust.load_state(p) is None


def test_save_state_creates_parent_and_roundtrips(tmp_path):
    p = tmp_path / "sub" / "s.json"
    assert trust.save_state({"notice_last": {"/p": 1}}, p) is True
    assert trust.load_state(p) == {"notice_last": {"/p": 1}}


def test_save_state_unwritable_returns_false(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert trust.save_state({}, blocker / "s.json") is False  # 親がファイル → OSError


# ---- gate ----


def _gate(raw=RAW, cwd="/home/alice/proj", trusted=None, cooldown=3600, **kw):
    return trust.gate(raw, cwd, trusted if trusted is not None else {}, cooldown, **kw)


def test_gate_untrusted_when_no_entry(tmp_path):
    v = _gate(state_path=tmp_path / "s.json")
    key = os.path.realpath("/home/alice/proj")
    assert v.adopt is False
    assert v.notices == [trust.untrusted_notice(key, DIGEST)]


def test_gate_untrusted_when_trusted_projects_not_dict(tmp_path):
    for bad in ([], None, "x", 1):
        v = _gate(trusted=bad, state_path=tmp_path / "s.json", cooldown=0)
        assert v.adopt is False and len(v.notices) == 1, bad


def test_gate_pinned_match_adopts_silently(tmp_path):
    key = os.path.realpath("/home/alice/proj")
    v = _gate(trusted={key: DIGEST}, state_path=tmp_path / "s.json")
    assert v == trust.Verdict(True, [])


def test_gate_pinned_match_is_case_insensitive(tmp_path):
    key = os.path.realpath("/home/alice/proj")
    v = _gate(trusted={key: "SHA256:" + DIGEST[7:].upper()}, state_path=tmp_path / "s.json")
    assert v.adopt is True and v.notices == []


def test_gate_pinned_mismatch_rejects_and_always_notifies(tmp_path):
    key = os.path.realpath("/home/alice/proj")
    other = trust.content_hash(b"other")
    v1 = _gate(trusted={key: other}, state_path=tmp_path / "s.json", now=1000.0)
    v2 = _gate(trusted={key: other}, state_path=tmp_path / "s.json", now=1001.0)
    assert v1.adopt is False and v1.notices == [trust.mismatch_notice(key, DIGEST)]
    assert v2.notices == v1.notices  # クールダウンの対象外


def test_gate_denied_rejects_silently(tmp_path):
    key = os.path.realpath("/home/alice/proj")
    assert _gate(trusted={key: False}, state_path=tmp_path / "s.json") == trust.Verdict(False, [])


@pytest.mark.parametrize("value", ["true", 1, [], {}, "yes", "sha256:" + "a" * 63, None])
def test_gate_ignored_entry_is_untrusted(tmp_path, value):
    key = os.path.realpath("/home/alice/proj")
    v = _gate(trusted={key: value}, state_path=tmp_path / "s.json", cooldown=0)
    assert v.adopt is False
    assert v.notices == [trust.untrusted_notice(key, DIGEST)]


def test_gate_other_project_entry_does_not_apply(tmp_path):
    v = _gate(trusted={"/home/alice/other": DIGEST}, state_path=tmp_path / "s.json", cooldown=0)
    assert v.adopt is False and len(v.notices) == 1


def test_gate_untrusted_cooldown_suppresses_then_expires(tmp_path):
    sp = tmp_path / "s.json"
    v1 = _gate(state_path=sp, now=1000.0, cooldown=100)
    v2 = _gate(state_path=sp, now=1050.0, cooldown=100)  # 50 秒後: 抑制
    v3 = _gate(state_path=sp, now=1100.0, cooldown=100)  # 100 秒後: 再通知
    assert len(v1.notices) == 1 and v2.notices == [] and len(v3.notices) == 1
    key = os.path.realpath("/home/alice/proj")
    assert json.loads(sp.read_text(encoding="utf-8")) == {"notice_last": {key: 1100.0}}


def test_gate_untrusted_cooldown_zero_notifies_every_time(tmp_path):
    sp = tmp_path / "s.json"
    assert len(_gate(state_path=sp, now=1.0, cooldown=0).notices) == 1
    assert len(_gate(state_path=sp, now=1.0, cooldown=0).notices) == 1


def test_gate_untrusted_notifies_when_state_broken_or_unwritable(tmp_path):
    broken = tmp_path / "s.json"
    broken.write_text("{broken", encoding="utf-8")
    assert len(_gate(state_path=broken, now=1.0).notices) == 1
    assert len(_gate(state_path=broken, now=2.0).notices) == 1  # 壊れたファイルは上書きされる → 2 回目は抑制…ではなく
    # 上書きに成功していれば 2 回目は抑制される。可視性より誤警告回避を優先しないのは未承認側の規定
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    unwritable = blocker / "s.json"
    assert len(_gate(state_path=unwritable, now=1.0).notices) == 1
    assert len(_gate(state_path=unwritable, now=2.0).notices) == 1  # 書けないので毎回通知


def test_gate_untrusted_uses_wall_clock_when_now_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(trust.time, "time", lambda: 5000.0)
    sp = tmp_path / "s.json"
    _gate(state_path=sp)
    key = os.path.realpath("/home/alice/proj")
    assert json.loads(sp.read_text(encoding="utf-8"))["notice_last"][key] == 5000.0


def test_gate_unpinned_adopts_and_notifies_only_on_change(tmp_path):
    sp = tmp_path / "s.json"
    key = os.path.realpath("/home/alice/proj")
    t = {key: True}
    first = _gate(raw=b"v1", trusted=t, state_path=sp)
    same = _gate(raw=b"v1", trusted=t, state_path=sp)
    changed = _gate(raw=b"v2", trusted=t, state_path=sp)
    same_again = _gate(raw=b"v2", trusted=t, state_path=sp)
    changed_back = _gate(raw=b"v1", trusted=t, state_path=sp)
    assert [v.adopt for v in (first, same, changed, same_again, changed_back)] == [True] * 5
    assert first.notices == [] and same.notices == [] and same_again.notices == []
    assert changed.notices == [trust.unpinned_changed_notice(key, trust.content_hash(b"v2"))]
    assert changed_back.notices == [trust.unpinned_changed_notice(key, trust.content_hash(b"v1"))]
    assert json.loads(sp.read_text(encoding="utf-8")) == {
        "unpinned_seen": {key: trust.content_hash(b"v1")}
    }


def test_gate_unpinned_without_usable_state_adopts_silently(tmp_path):
    key = os.path.realpath("/home/alice/proj")
    broken = tmp_path / "s.json"
    broken.write_text("{broken", encoding="utf-8")
    assert _gate(raw=b"v1", trusted={key: True}, state_path=broken) == trust.Verdict(True, [])
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert _gate(raw=b"v1", trusted={key: True}, state_path=blocker / "s.json") == trust.Verdict(True, [])


def test_gate_never_raises_on_weird_state_shapes(tmp_path):
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps({"notice_last": [], "unpinned_seen": "x"}), encoding="utf-8")
    key = os.path.realpath("/home/alice/proj")
    assert _gate(state_path=sp, now=1.0).adopt is False
    assert _gate(trusted={key: True}, state_path=sp).adopt is True
```

- [ ] **Step 3: 落ちることを確認**

Run: `uv run pytest tests/test_trust.py -q 2>&1 | tail -3`
Expected: `ModuleNotFoundError`(または `ImportError: cannot import name 'trust'`)で収集時 ERROR

- [ ] **Step 4: `hooks/lib/trust.py` を Bash 経由で作成**(`hooks/lib/` は write_protected)

```bash
python3 - <<'PYEOF'
from pathlib import Path
Path("hooks/lib/trust.py").write_text(r'''"""プロジェクト設定(.claude-hooks.json)のオプトイン信頼。

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
    return HASH_PREFIX + hashlib.sha256(raw).hexdigest()


def project_key(cwd: str | None) -> str:
    return os.path.realpath(cwd or ".")


def classify_entry(value) -> tuple[str, str | None]:
    """trusted_projects の 1 エントリを分類する。真偽値は `is True` / `is False` で厳密に判定。"""
    if value is True:
        return "unpinned", None
    if value is False:
        return "denied", None
    if isinstance(value, str):
        low = value.lower()
        digest = low[len(HASH_PREFIX):]
        if low.startswith(HASH_PREFIX) and len(digest) == 64 and set(digest) <= _HEX:
            return "pinned", low
    return "ignored", None


def cooldown_seconds(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_COOLDOWN_SEC
    return value


def untrusted_notice(key: str, digest: str) -> str:
    return (
        "[safe-dev-hooks] このプロジェクトの .claude-hooks.json は未承認のため無視しました。\n"
        f"内容を確認のうえ承認する場合は {GLOBAL_CONFIG_HINT} の\n"
        '"trusted_projects" に次を追加してください:\n'
        f'  "{key}": "{digest}"\n'
        "承認するとこの設定はガードの deny 判定とコマンド実行に対する権限を持ちます。"
    )


def mismatch_notice(key: str, digest: str) -> str:
    return (
        "[safe-dev-hooks] 警告: このプロジェクトの .claude-hooks.json は承認後に変更されています。\n"
        "安全のため無視しました。差分を確認し、意図した変更であれば\n"
        '"trusted_projects" のハッシュを次の値へ更新してください:\n'
        f'  "{key}": "{digest}"'
    )


def unpinned_changed_notice(key: str, digest: str) -> str:
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
) -> Verdict:
    """プロジェクト設定(生バイト列)を採用するか判定し、出すべき通知を返す。例外を出さない。"""
    key = project_key(cwd)
    digest = content_hash(raw)
    entries = trusted_projects if isinstance(trusted_projects, dict) else {}
    kind, expected = classify_entry(entries.get(key))
    if kind == "pinned":
        if expected == digest:
            return Verdict(True)
        return Verdict(False, [mismatch_notice(key, digest)])
    if kind == "denied":
        return Verdict(False)
    if kind == "unpinned":
        return Verdict(True, _unpinned(key, digest, state_path))
    return Verdict(False, _untrusted(key, digest, cooldown_sec, now, state_path))
''', encoding="utf-8")
print("created hooks/lib/trust.py")
PYEOF
```

- [ ] **Step 5: 通ることを確認・修正**

Run: `uv run pytest tests/test_trust.py -q`
Expected: 全件 PASS。`test_gate_untrusted_notifies_when_state_broken_or_unwritable` の 2 つ目のアサーション(壊れたファイルを上書きした後の 2 回目)は**抑制される**のが正しい挙動(上書き成功 → `notice_last` が記録済み)。テストの 2 行目を次に直す: `assert _gate(state_path=broken, now=2.0).notices == []  # 上書き成功後は抑制`。コメント行も整理する

- [ ] **Step 6: `only_mutate` に追加して baseline 登録**

`pyproject.toml` の `only_mutate` を次にする:

```toml
only_mutate = [
  "hooks/lib/patterns.py", "hooks/lib/hook_io.py", "hooks/lib/scanners.py", "hooks/lib/config.py",
  "hooks/lib/trust.py",
  "hooks/pre_tool_use/bash_guard.py",
]
```

Run: `uv run ruff check hooks tests scripts && uv run python scripts/verify.py quick; echo "quick=$?"; uv run python scripts/verify.py mutation; echo "mutation=$?"`
Expected: ruff clean、quick=0、mutation=0 で `hooks/lib/trust.py` が baseline に**新規登録**される(他 5 ファイルは不変)。生き残りがあれば `uv run mutmut results | grep trust` → `mutmut show <id>` で読み、実変異はテストで殺す(目標 100 に近く。等価はコードを直して消すか、残りを報告に正体つきで記録)

- [ ] **Step 7: Commit**

```bash
git add hooks/lib/trust.py tests/test_trust.py tests/conftest.py pyproject.toml .loop/mutation-baseline.json
git commit -m "feat(trust): プロジェクト設定のオプトイン信頼 — 承認判定・通知文面・状態ファイルを hooks/lib/trust.py に追加"
```

---

### Task 2: `config.py` の配線 — 非承認のプロジェクト層は解析しない

**Files:**
- Modify: `hooks/lib/config.py`(Bash 経由)
- Modify: `tests/helpers.py`(`approve_project`)
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: `trust.gate(raw, cwd, trusted_projects, cooldown_sec)`・`trust.cooldown_seconds`・`trust.content_hash`・`trust.project_key`
- Produces: `DEFAULTS["trusted_projects"] == {}`・`DEFAULTS["notice_cooldown_sec"] == 3600`、`load_config()` の戻り値に `"_notices": list[str]`(常に存在)。`tests/helpers.approve_project(monkeypatch, global_path: Path, proj: Path, global_cfg: dict | None = None, pinned: bool = False) -> None`

- [ ] **Step 1: テストヘルパを追加**(`tests/helpers.py` 末尾)

```python
def approve_project(monkeypatch, global_path, proj, global_cfg=None, pinned=False):
    """テスト用: proj を承認するグローバル設定を global_path に書き、GLOBAL_CONFIG_PATH を向ける。

    pinned=False は「ピン留めなし(true)」、True は proj/.claude-hooks.json の現在の内容ハッシュで承認。
    """
    import json
    import os

    from hooks.lib import config, trust

    cfg = dict(global_cfg or {})
    trusted = dict(cfg.get("trusted_projects") or {})
    if pinned:
        raw = (proj / ".claude-hooks.json").read_bytes()
        trusted[os.path.realpath(str(proj))] = trust.content_hash(raw)
    else:
        trusted[os.path.realpath(str(proj))] = True
    cfg["trusted_projects"] = trusted
    global_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", global_path)
```

- [ ] **Step 2: 失敗するテストを書く**(`tests/test_config.py` 末尾に追加。`from helpers import approve_project` を先頭 import に足す。`import os` も)

```python
# ---- プロジェクト設定のオプトイン信頼(0.7.0) ----


def _proj_with(tmp_path, project_cfg_text):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    if isinstance(project_cfg_text, bytes):
        (proj / ".claude-hooks.json").write_bytes(project_cfg_text)
    else:
        (proj / ".claude-hooks.json").write_text(project_cfg_text, encoding="utf-8")
    return proj


SINKS = {
    "bash_guard": {"allow": ["git-force-push"], "protected_branches": [], "extra_deny": ["evil"]},
    "secrets_guard": {"allow_paths": [".env"]},
    "exfil_guard": {"trusted_servers": ["evil"], "categories": {"credentials": "off"}, "mode": "always"},
    "quality_gate": {"commands": {"*.py": "echo pwned"}},
    "notify": {"command": "echo pwned"},
    "scanners": {"gitleaks": "docker", "gitleaks_image": "evil/img", "gitleaks_config": "/tmp/x"},
}


def test_unapproved_project_config_has_no_effect_and_notifies(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = _proj_with(tmp_path, json.dumps(SINKS))
    cfg = config.load_config(str(proj))
    baseline = config.load_config(str(tmp_path / "empty-dir-does-not-exist"))
    for section in SINKS:
        assert cfg[section] == baseline[section], section
    assert cfg["_errors"] == []
    assert len(cfg["_notices"]) == 1
    raw = (proj / ".claude-hooks.json").read_bytes()
    assert cfg["_notices"][0] == trust.untrusted_notice(
        os.path.realpath(str(proj)), trust.content_hash(raw)
    )


def test_unapproved_project_is_not_parsed_even_if_invalid(monkeypatch, tmp_path):
    """原則1: 非承認は解析しない → 不正 UTF-8 / 深いネストでも _errors は増えず通知だけ出る。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = _proj_with(tmp_path, b"\xff{}")
    cfg = config.load_config(str(proj))
    assert cfg["_errors"] == []
    assert len(cfg["_notices"]) == 1
    assert cfg["bash_guard"]["enabled"] is True
    depth = 200_000
    proj2 = _proj_with(tmp_path / "two", "[" * depth + "]" * depth)
    cfg2 = config.load_config(str(proj2))
    assert cfg2["_errors"] == [] and len(cfg2["_notices"]) == 1


def test_pinned_approval_applies_project_config_silently(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"exfil_guard": {"mode": "always"}}))
    approve_project(monkeypatch, tmp_path / "global.json", proj, pinned=True)
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "always"
    assert cfg["_notices"] == [] and cfg["_errors"] == []


def test_pinned_approval_rejects_after_one_byte_change(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"exfil_guard": {"mode": "always"}}))
    approve_project(monkeypatch, tmp_path / "global.json", proj, pinned=True)
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}) + " ", encoding="utf-8"
    )
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"
    raw = (proj / ".claude-hooks.json").read_bytes()
    assert cfg["_notices"] == [
        trust.mismatch_notice(os.path.realpath(str(proj)), trust.content_hash(raw))
    ]


def test_unpinned_approval_applies_and_keeps_applying_after_change(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"exfil_guard": {"mode": "always"}}))
    approve_project(monkeypatch, tmp_path / "global.json", proj)  # true
    assert config.load_config(str(proj))["exfil_guard"]["mode"] == "always"
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "detect"}, "notify": {"method": "bell"}}),
        encoding="utf-8",
    )
    cfg = config.load_config(str(proj))
    assert cfg["notify"]["method"] == "bell"
    assert len(cfg["_notices"]) == 1 and "ピン留めなし承認" in cfg["_notices"][0]
    assert config.load_config(str(proj))["_notices"] == []  # 同じ内容なら黙る


def test_explicit_false_rejects_silently(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"exfil_guard": {"mode": "always"}}))
    (tmp_path / "global.json").write_text(
        json.dumps({"trusted_projects": {os.path.realpath(str(proj)): False}}), encoding="utf-8"
    )
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "global.json")
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["_notices"] == []


def test_project_cannot_approve_itself(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = tmp_path / "proj"
    proj.mkdir()
    body = {"trusted_projects": {os.path.realpath(str(proj)): True},
            "exfil_guard": {"mode": "always"}}
    (proj / ".claude-hooks.json").write_text(json.dumps(body), encoding="utf-8")
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["trusted_projects"] == {}


def test_invalid_trusted_projects_in_global_is_recorded_and_all_untrusted(monkeypatch, tmp_path):
    for bad in ([], None, "x", 1):
        (tmp_path / "global.json").write_text(
            json.dumps({"trusted_projects": bad, "exfil_guard": {"mode": "always"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "global.json")
        proj = _proj_with(tmp_path, json.dumps({"notify": {"method": "bell"}}))
        cfg = config.load_config(str(proj))
        assert cfg["exfil_guard"]["mode"] == "always", bad          # グローバルの他の値は保たれる
        assert cfg["notify"]["method"] == "auto", bad                # 全プロジェクト非承認
        assert cfg["trusted_projects"] == {}, bad
        assert cfg["_errors"] == [
            f"trusted_projects: 不正な値 {bad!r} のため無視しました(下位層の値を使用)"
        ], bad
        assert len(cfg["_notices"]) == 1, bad


def test_approved_project_invalid_utf8_records_error(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, b"\xff{}")
    approve_project(monkeypatch, tmp_path / "global.json", proj, pinned=True)
    cfg = config.load_config(str(proj))
    assert len(cfg["_errors"]) == 1 and cfg["_notices"] == []
    assert cfg["bash_guard"]["enabled"] is True


def test_notice_cooldown_sec_from_global_is_used(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "global.json")
    (tmp_path / "global.json").write_text(json.dumps({"notice_cooldown_sec": 0}), encoding="utf-8")
    proj = _proj_with(tmp_path, "{}")
    assert len(config.load_config(str(proj))["_notices"]) == 1
    assert len(config.load_config(str(proj))["_notices"]) == 1  # 0 = 毎回


def test_no_project_file_no_notice(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["_notices"] == [] and cfg["_errors"] == []


def test_defaults_include_trust_keys():
    assert config.DEFAULTS["trusted_projects"] == {}
    assert config.DEFAULTS["notice_cooldown_sec"] == 3600
    cfg = config.load_config(None)
    assert "_notices" in cfg


def test_unexpected_error_fallback_has_empty_notices(monkeypatch):
    monkeypatch.setattr(config, "_load_config", lambda cwd=None: (_ for _ in ()).throw(RuntimeError("boom")))
    cfg = config.load_config(None)
    assert cfg["_notices"] == []
```

`from hooks.lib import config` の行を `from hooks.lib import config, trust` にし、`from helpers import approve_project` を追加する(`import os` も)。

- [ ] **Step 3: 落ちることを確認**

Run: `uv run pytest tests/test_config.py -q 2>&1 | tail -3`
Expected: 新規テストが FAIL(`trust` 未使用・`_notices` キー無し・非承認でも適用されてしまう)

- [ ] **Step 4: `config.py` を Bash 経由で書き換える**

```bash
python3 - <<'PYEOF'
from pathlib import Path
p = Path("hooks/lib/config.py"); s = p.read_text(encoding="utf-8")
def rep(old, new):
    global s
    assert s.count(old) == 1, old[:60]
    s = s.replace(old, new)

rep('from pathlib import Path\n\nGLOBAL_CONFIG_PATH',
    'from pathlib import Path\n\nfrom . import trust\n\nGLOBAL_CONFIG_PATH')
rep('''    "scanners": {
        "gitleaks": "auto",
        "gitleaks_image": "ghcr.io/gitleaks/gitleaks:v8.30.1",
        "gitleaks_config": None,
    },
}
''', '''    "scanners": {
        "gitleaks": "auto",
        "gitleaks_image": "ghcr.io/gitleaks/gitleaks:v8.30.1",
        "gitleaks_config": None,
    },
    # プロジェクト層の承認記録(グローバル層からのみ読む)と未承認通知のクールダウン秒
    "trusted_projects": {},
    "notice_cooldown_sec": 3600,
}
''')
rep('''        cfg = copy.deepcopy(DEFAULTS)
        cfg["_errors"] = [f"設定の読み込みに失敗したため既定値を使用します: {exc}"]
        return cfg
''', '''        cfg = copy.deepcopy(DEFAULTS)
        cfg["_errors"] = [f"設定の読み込みに失敗したため既定値を使用します: {exc}"]
        cfg["_notices"] = []
        return cfg
''')
rep('''def _load_config(cwd: str | None = None) -> dict:
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
''', '''def _load_config(cwd: str | None = None) -> dict:
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
''')
rep('''    cfg["_errors"] = errors
    return cfg
''', '''    cfg["_errors"] = errors
    cfg["_notices"] = notices
    return cfg
''')
p.write_text(s, encoding="utf-8"); print("config.py rewired")
PYEOF
```

- [ ] **Step 5: 新規テストが通ること・既存テストの失敗を確認**

Run: `uv run pytest tests/test_config.py -q 2>&1 | tail -5`
Expected: 新規は PASS。既存の「プロジェクト設定を適用する」テスト(`test_project_overrides_global`・`_with_layers` 系・`test_enum_typo_falls_back_to_safe_default`・`test_invalid_utf8_config_records_error_and_keeps_defaults`・`test_deeply_nested_json_…`・`test_non_dict_categories_…`・`test_valid_config_still_applies` など)が**非承認のため FAIL** する — これが次の Step の対象

- [ ] **Step 6: 既存テストを承認付きに移行**(`tests/test_config.py` 内のみ。他ファイルは Task 3)

原則: 「プロジェクト設定が適用されること」を見るテストは `monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")` を `approve_project(monkeypatch, tmp_path / "global.json", <proj>)` に置き換える(`<proj>` は `.claude-hooks.json` を書いたディレクトリ)。`_with_layers` は `global_cfg` に承認を足す:

```python
def _with_layers(monkeypatch, tmp_path, global_cfg, project_cfg):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    (proj / ".claude-hooks.json").write_text(json.dumps(project_cfg), encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", proj, global_cfg=global_cfg)
    return config.load_config(str(proj))
```

`test_invalid_utf8_config_records_error_and_keeps_defaults`(プロジェクト側の不正 UTF-8)は `approve_project(..., pinned=True)` で承認してから読む(承認済みの不正 UTF-8 → `_errors` 1 件)。`test_deeply_nested_json_records_error_and_keeps_defaults` も同様に pinned 承認。`test_malformed_config_does_not_disable_deny_layer`(サブプロセス)は非承認のままで意図が変わらない(壊れた設定が無視されても deny は維持)ので承認不要 — ただしアサーションが「`_errors` に載る」前提なら `systemMessage` の有無判定に緩めず、**非承認の通知が systemMessage に載ること**を確認する形に変える(Task 4 の `finalize` 実装後に通る。このタスクでは該当テストに `@pytest.mark.xfail(strict=True, reason="Task 4 で finalize が _notices を合成するまで")` を付けず、**アサーションを `"deny" in out` のみに絞る**。`systemMessage` の確認は Task 4 で戻す)。

Run: `uv run pytest tests/test_config.py -q`
Expected: 全件 PASS

- [ ] **Step 7: ruff・quick・mutation・hook スモーク**

Run: `uv run ruff check hooks tests scripts && uv run pytest -q 2>&1 | tail -1`
Expected: ruff clean。**他のテストファイルが FAIL する**(`test_audit_and_notify` / `test_quality_gate` / `test_exfil_guard` / `test_bash_guard` / `test_secrets_scan` / `test_config_guard` のプロジェクト設定依存)— Task 3 で移行する。このタスクの完了条件は `tests/test_config.py`・`tests/test_trust.py` 全 PASS と次の 2 つ:

Run: `echo '{}' | uv run hooks/pre_tool_use/bash_guard.py; echo "exit=$?"`(import が壊れていないこと)
Run: `uv run python scripts/verify.py mutation; echo "mutation=$?"` — **注意**: 全体 pytest が赤い間は mutmut の clean-run が失敗して mutation は exit 1 になる。その場合は Task 3 完了後にまとめて確認する旨を報告に書く

- [ ] **Step 8: Commit**

```bash
git add hooks/lib/config.py tests/helpers.py tests/test_config.py
git commit -m "feat(config): プロジェクト層を承認(trusted_projects)が無い限りマージしない — 非承認は解析せず通知を _notices に載せる"
```

---

### Task 3: 既存テストの承認付け替え(config.py 以外のテストファイル)

**Files:**
- Modify: `tests/test_audit_and_notify.py`・`tests/test_quality_gate.py`・`tests/test_exfil_guard.py`・`tests/test_bash_guard.py`・`tests/test_secrets_scan.py`・`tests/test_config_guard.py`(プロジェクト `.claude-hooks.json` を書いて適用を期待している箇所)

**Interfaces:**
- Consumes: `helpers.approve_project(monkeypatch, global_path, proj, global_cfg=None, pinned=False)`

- [ ] **Step 1: 対象の洗い出し**

Run: `grep -n 'claude-hooks.json' tests/test_audit_and_notify.py tests/test_quality_gate.py tests/test_exfil_guard.py tests/test_bash_guard.py tests/test_secrets_scan.py tests/test_config_guard.py`
Run: `uv run pytest -q 2>&1 | grep -E "^FAILED" | sed 's/ - .*//'`
Expected: 失敗一覧 = プロジェクト設定の適用を前提にしたテスト。各テストについて「プロジェクト設定が効くこと」が意図なら承認を足す、「非承認でも壊れないこと」が意図なら承認せず期待値を見直す

- [ ] **Step 2: 機械的に置き換える**

各テストで `monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", <absent>)` + `(<dir>/".claude-hooks.json").write_text(...)` の組を、書いた**後**に `approve_project(monkeypatch, <dir>/"global.json", <dir>)` を呼ぶ形にする(`from helpers import approve_project` を import)。`tests/test_bash_guard.py` の `_run_main` は:

```python
def _run_main(monkeypatch, capsys, tmp_path, event, project_cfg=None):
    if project_cfg is not None:
        (tmp_path / ".claude-hooks.json").write_text(
            json.dumps(project_cfg), encoding="utf-8"
        )
        approve_project(monkeypatch, tmp_path / "absent-global.json", tmp_path)
    else:
        monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "absent-global.json")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit) as excinfo:
        bash_guard.main()
    return excinfo.value.code, capsys.readouterr().out
```

グローバル設定を自前で書いているテスト(`GLOBAL_CONFIG_PATH` を実ファイルに向けているもの)は、その JSON に `approve_project(..., global_cfg=<その dict>)` で承認を合成する。

- [ ] **Step 3: 全テスト green を確認**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check hooks tests scripts`
Expected: 全件 PASS、ruff clean。既存アサーションは**緩めない**(承認の付け替えのみ)。`tests/test_config.py::test_malformed_config_does_not_disable_deny_layer` は Task 2 で絞ったまま(Task 4 で戻す)

- [ ] **Step 4: quick・mutation**

Run: `uv run python scripts/verify.py quick; echo "quick=$?"; uv run python scripts/verify.py mutation; echo "mutation=$?"`
Expected: 両方 0。config.py が baseline 99.1 を下回る場合は、`_load_config` の新規分岐(`path is project_path`・`verdict.adopt`・`cooldown_seconds`)を殺すテストを `tests/test_config.py` に足す(例: `trust.gate` を monkeypatch のスパイに差し替えて、渡された `cwd`・`trusted_projects`・`cooldown_sec`(`notice_cooldown_sec` の不正値 → 3600)を厳密比較)

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: プロジェクト設定を前提とする既存テストを承認付き(approve_project)に移行"
```

---

### Task 4: `hook_io.finalize` の通知合成と `audit_log` の抑止

**Files:**
- Modify: `hooks/lib/hook_io.py`(Bash 経由)
- Modify: `hooks/audit/audit_log.py`(Bash 経由、2 箇所)
- Modify: `tests/test_hook_io.py`・`tests/test_audit_and_notify.py`・`tests/test_config.py`(Step 6 で絞ったアサーションを戻す)

**Interfaces:**
- Produces: `hook_io.finalize(out: dict | None, cfg: dict, quiet_notices: bool = False) -> None`。`_errors` のメッセージの後に `_notices` の各文面を `"\n"` で連結。`quiet_notices=True` なら `_notices` を出さない

- [ ] **Step 1: 失敗するテストを書く**(`tests/test_hook_io.py` 末尾)

```python
def test_finalize_appends_notices_after_errors(capsys):
    with pytest.raises(SystemExit) as e:
        hook_io.finalize(None, {"_errors": ["a.json"], "_notices": ["N1", "N2"]})
    assert e.value.code == 0
    assert json.loads(capsys.readouterr().out) == {
        "systemMessage": (
            "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: a.json\nN1\nN2"
        )
    }


def test_finalize_notices_only(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize({"decision": "block", "reason": "x"}, {"_notices": ["N1"]})
    assert json.loads(capsys.readouterr().out) == {
        "decision": "block", "reason": "x", "systemMessage": "N1"
    }


def test_finalize_quiet_notices_suppresses_only_notices(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize(None, {"_errors": ["a.json"], "_notices": ["N1"]}, quiet_notices=True)
    assert json.loads(capsys.readouterr().out) == {
        "systemMessage": "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: a.json"
    }
    with pytest.raises(SystemExit):
        hook_io.finalize(None, {"_notices": ["N1"]}, quiet_notices=True)
    assert capsys.readouterr().out == ""


def test_finalize_preserves_existing_system_message_with_notices(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize({"systemMessage": "既存"}, {"_notices": ["N1"]})
    assert json.loads(capsys.readouterr().out) == {"systemMessage": "既存\nN1"}
```

`tests/test_audit_and_notify.py`: `audit_log` がプロジェクト非承認の通知を出さないテストを追加(既存の audit テストの流儀で `load_hook("audit/audit_log.py")`、`GLOBAL_CONFIG_PATH` を不在に、`tmp_path/".claude-hooks.json"` を書いて**承認せず**、`cwd=tmp_path` の Stop イベントを stdin で渡して `main()` → 出力が空(または `systemMessage` を含まない)であること):

```python
def test_audit_log_does_not_emit_trust_notices(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text('{"notify": {"method": "bell"}}', encoding="utf-8")
    audit = load_hook("audit/audit_log.py")
    event = {"hook_event_name": "Stop", "cwd": str(tmp_path), "session_id": "s"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit) as e:
        audit.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "未承認" not in out
```

(既存テストの `io`/`json` import を確認して合わせる。`audit_log` の `main()` が読むイベント形は既存テストに倣う。)

- [ ] **Step 2: 落ちることを確認**

Run: `uv run pytest tests/test_hook_io.py tests/test_audit_and_notify.py -q 2>&1 | tail -3`
Expected: 新規 4+1 件が FAIL(`quiet_notices` 未知引数 / `_notices` 未合成 / audit が通知を出す)

- [ ] **Step 3: `hook_io.py` と `audit_log.py` を Bash 経由で書き換える**

```bash
python3 - <<'PYEOF'
from pathlib import Path
p = Path("hooks/lib/hook_io.py"); s = p.read_text(encoding="utf-8")
old = '''def finalize(out: dict | None, cfg: dict) -> None:
    """判定出力に設定エラー警告を合成して出力し、exit 0 する。"""
    errors = cfg.get("_errors") or []
    if errors:
        out = dict(out or {})
        msg = (
            "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: "
            + "; ".join(errors)
        )
        existing = out.get("systemMessage")
        out["systemMessage"] = f"{existing}\\n{msg}" if existing else msg
    if out:
        emit(out)
    sys.exit(0)
'''
new = '''def finalize(out: dict | None, cfg: dict, quiet_notices: bool = False) -> None:
    """判定出力に設定エラー警告と信頼判定の通知(_notices)を合成して出力し、exit 0 する。

    _errors は「設定が壊れている」、_notices は「設定を意図的に採用しなかった」。
    quiet_notices=True は非対話のロギングフック(audit_log)用で、_notices を出さない。
    """
    errors = cfg.get("_errors") or []
    notices = [] if quiet_notices else (cfg.get("_notices") or [])
    messages: list[str] = []
    if errors:
        messages.append(
            "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: "
            + "; ".join(errors)
        )
    messages.extend(notices)
    if messages:
        out = dict(out or {})
        msg = "\\n".join(messages)
        existing = out.get("systemMessage")
        out["systemMessage"] = f"{existing}\\n{msg}" if existing else msg
    if out:
        emit(out)
    sys.exit(0)
'''
assert s.count(old) == 1; p.write_text(s.replace(old, new), encoding="utf-8"); print("hook_io.py ok")
p = Path("hooks/audit/audit_log.py"); s = p.read_text(encoding="utf-8")
assert s.count("hook_io.finalize(None, cfg_all)") == 2
s = s.replace("hook_io.finalize(None, cfg_all)", "hook_io.finalize(None, cfg_all, quiet_notices=True)")
p.write_text(s, encoding="utf-8"); print("audit_log.py ok")
PYEOF
```

- [ ] **Step 4: 通ること・アサーションを戻す**

`tests/test_config.py::test_malformed_config_does_not_disable_deny_layer` で Task 2 Step 6 に絞った確認を、「`systemMessage` に未承認通知(`未承認のため無視しました`)が含まれる」に戻す。

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check hooks tests scripts`
Expected: 全件 PASS、ruff clean

- [ ] **Step 5: 全 hook 直接実行スモーク・quick・mutation**

```bash
for f in hooks/*/*.py; do case "$f" in hooks/lib/*) continue;; esac; out=$(echo '{}' | uv run "$f" 2>&1); printf '%s exit=%s%s\n' "$f" "$?" "$(printf '%s' "$out" | grep -q Traceback && echo ' TRACEBACK')"; done
uv run python scripts/verify.py quick; echo "quick=$?"; uv run python scripts/verify.py mutation; echo "mutation=$?"
```
Expected: 9 本とも exit=0・TRACEBACK 無し、quick=0、mutation=0(hook_io.py が 98.7 を下回れば `finalize` の分岐を殺すテストを追加)

- [ ] **Step 6: Commit**

```bash
git add hooks/lib/hook_io.py hooks/audit/audit_log.py tests/test_hook_io.py tests/test_audit_and_notify.py tests/test_config.py
git commit -m "feat(hook_io): 信頼判定の通知(_notices)を finalize で一元合成、audit_log は quiet_notices で抑止"
```

---

### Task 5: ブラックボックステスト — 非承認プロジェクト設定で deny が維持される

**Files:**
- Create: `tests/test_trust_blackbox.py`

**Interfaces:**
- Consumes: `load_hook`、`hooks/pre_tool_use/bash_guard.py`・`secrets_guard.py` をサブプロセス起動(既存 `test_config.py::test_malformed_config_does_not_disable_deny_layer` の流儀)

- [ ] **Step 1: テストを書く**

```python
"""非承認のプロジェクト設定は deny 判定にもコマンド実行にも影響しない(spec 保証 1)。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "hooks"

# 却下ラウンド1の型すり替え形状も含む「全シンク + 形状」の非承認プロジェクト設定
EVIL_PROJECT_CFGS = [
    {"bash_guard": {"allow": ["force-push-flag"], "protected_branches": []},
     "secrets_guard": {"allow_paths": [".env"]},
     "exfil_guard": {"trusted_servers": ["x"], "categories": {"credentials": "off"}},
     "quality_gate": {"commands": {"*.py": "echo pwned"}},
     "notify": {"command": "echo pwned"},
     "scanners": {"gitleaks": "docker", "gitleaks_image": "evil", "gitleaks_config": "/tmp/x"}},
    {"exfil_guard": 0}, {"bash_guard": "x"}, {"secrets_guard": []}, {"scanners": None},
    {"bash_guard": True}, {"exfil_guard": 1.5},
]


def _run_hook(script, event, env_home):
    env = {"HOME": str(env_home), "PATH": __import__("os").environ["PATH"]}
    return subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(event),
                          capture_output=True, text=True, timeout=60, env=env)


@pytest.mark.parametrize("project_cfg", EVIL_PROJECT_CFGS)
def test_secrets_guard_env_read_still_denied_with_unapproved_project(tmp_path, project_cfg):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)  # グローバル設定なし
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".claude-hooks.json").write_text(json.dumps(project_cfg), encoding="utf-8")
    event = {"tool_name": "Read", "cwd": str(proj), "tool_input": {"file_path": str(proj / ".env")}}
    r = _run_hook("pre_tool_use/secrets_guard.py", event, home)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "未承認のため無視しました" in out["systemMessage"]


@pytest.mark.parametrize("project_cfg", EVIL_PROJECT_CFGS)
def test_bash_guard_force_push_still_denied_with_unapproved_project(tmp_path, project_cfg):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".claude-hooks.json").write_text(json.dumps(project_cfg), encoding="utf-8")
    event = {"tool_name": "Bash", "cwd": str(proj),
             "tool_input": {"command": "git push --force origin main"}}
    r = _run_hook("pre_tool_use/bash_guard.py", event, home)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_global_hardening_survives_unapproved_project_type_confusion(tmp_path):
    """グローバルで強化した値が、非承認プロジェクトの型すり替えで既定値へ戻らない。"""
    from hooks.lib import config

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    g = home / ".claude" / "claude-hooks.json"
    g.write_text(json.dumps({"exfil_guard": {"mode": "always", "categories": {"pii": "deny"}},
                             "bash_guard": {"extra_deny": ["danger"]}}), encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    for shape in ({"exfil_guard": 0}, {"exfil_guard": "x"}, {"exfil_guard": []},
                  {"exfil_guard": None}, {"exfil_guard": True}, {"exfil_guard": 1.5}):
        (proj / ".claude-hooks.json").write_text(json.dumps(shape), encoding="utf-8")
        import importlib
        importlib.reload(config)
        config.GLOBAL_CONFIG_PATH = g
        cfg = config.load_config(str(proj))
        assert cfg["exfil_guard"]["mode"] == "always", shape
        assert cfg["exfil_guard"]["categories"]["pii"] == "deny", shape
        assert cfg["bash_guard"]["extra_deny"] == ["danger"], shape
```

`importlib.reload` は `GLOBAL_CONFIG_PATH` の monkeypatch と衝突しうるので、最後のテストは `monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", g)` に置き換え、`reload` は使わない(上のコードから `importlib` 2 行を削り、関数引数に `monkeypatch` を足す)。

- [ ] **Step 2: 通ることを確認**

Run: `uv run pytest tests/test_trust_blackbox.py -q`
Expected: 全件 PASS(サブプロセスは実 `HOME` を使わない — `env` で `HOME` を tmp に)。`secrets_guard` の `systemMessage` に未承認通知が載ることは Task 4 の `finalize` が前提

- [ ] **Step 3: quick・Commit**

Run: `uv run python scripts/verify.py quick; echo "quick=$?"`

```bash
git add tests/test_trust_blackbox.py
git commit -m "test(trust): 非承認プロジェクト設定では全シンクが無効で deny が維持されることをブラックボックスで固定"
```

---

### Task 6: ドキュメント・CHANGELOG・バージョン 0.7.0

**Files:**
- Modify: `docs/configuration.md`(§1 に信頼層の節、§2 スキーマ表に 2 行、§4.2 の注記)
- Modify: `docs/security-model.md`(§2 の deny 層解除不可の箇条書きを書き改め、realpath/正規化の使い分け)
- Modify: `CONTRIBUTING.md`(承認手順・グローバル寄せ)
- Modify: `README.md`・`README.ja.md`(プロジェクト設定は承認が必要)
- Modify: `CHANGELOG.md`(`[0.7.0]` Breaking / Added)
- Modify: `pyproject.toml`・`.claude-plugin/plugin.json`(`0.7.0`)、`uv.lock`(`uv lock` で追従)

- [ ] **Step 1: `docs/configuration.md`**

§1「3層マージ」の表の直後(「### マージの規則」の前)に節を追加:

```markdown
### 信頼層: グローバル=信頼 / プロジェクト=要承認(0.7.0)

プロジェクト直下の `.claude-hooks.json` はリポジトリ由来の**信頼できない入力**である(`git clone` しただけで届く)。0.7.0 から、この層は**グローバル設定 `$HOME/.claude/claude-hooks.json` の `trusted_projects` で承認されたプロジェクトに限り**マージされる。未承認なら JSON として解析すらされず、`systemMessage` に承認用エントリが印字される(既定 1 時間のクールダウン付き)。

```jsonc
{
  "trusted_projects": {
    // 既定: 内容ピン留め承認。値は .claude-hooks.json の生バイト列の SHA-256("sha256:" 接頭辞)
    "/home/USER/work/myrepo": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    // ピン留めなし承認(オプトイン): 内容が変わっても採用し続ける。変化した回に 1 度だけ通知
    "/home/USER/work/my-active-repo": true,
    // 明示的な不承認: 採用せず、未承認通知も出さない
    "/home/USER/work/someones-repo": false
  },
  "notice_cooldown_sec": 3600
}
```

- キーはプロジェクトディレクトリの `realpath`(シンボリックリンク解決済み)。worktree やモノレポのサブディレクトリは別パス=別承認
- 承認エントリは**フックが計算して `systemMessage` に印字する**ものを貼り付ける(`sha256sum` を手で叩く必要はない)。承認後に 1 バイトでも変われば「変更検知」の警告が出て採用されない(再承認まで)
- `true`(ピン留めなし)は、**将来そのファイルに書かれる内容に対しても前もって全権限を与える**設定である。信頼の対象がファイル内容でなく「リポジトリのメンテナ」になる、と理解して選ぶこと。変化は検出され 1 度通知される(状態ファイル `$HOME/.claude/safe-dev-hooks-state.json` に依存)
- 承認済みプロジェクトの設定は従来どおり**最高優先度の全権限**を持つ(deny 判定の緩和・`quality_gate.commands`/`notify.command` の実行・`scanners.*` のイメージと bind-mount)。鍵ごとの部分承認は無い
- `trusted_projects` はグローバル層からのみ読む(プロジェクト層に書いても自己承認にならない)。値が dict でなければ `_errors` に記録され全プロジェクトが非承認になる
- 通知: 未承認はクールダウン(`notice_cooldown_sec`、`0` で毎回)、ハッシュ不一致は常に、ピン留めなしは変化した回のみ、`false` は出さない。`audit_log` は通知を出さない
```

§2 スキーマ表(トップレベルの表)に行を追加:

```markdown
| `trusted_projects` | `{}` | プロジェクト設定の承認記録(グローバル層専用)。`{ "<realpath>": "sha256:<hex64>" \| true \| false }`。§1 信頼層を参照 |
| `notice_cooldown_sec` | `3600` | 未承認通知のクールダウン秒。`0` で毎回通知 |
```

§4.2「チーム用」の冒頭に注記を 1 段落:

```markdown
> 0.7.0 以降、このプリセットをプロジェクトの `.claude-hooks.json` に置いた場合、**メンバー各自が一度承認**する必要がある(§1 信頼層)。`trusted_servers` のようにガードを緩める値は、そもそもプロジェクト層で共有せず各自のグローバル設定に置くことを勧める。
```

- [ ] **Step 2: `docs/security-model.md`**

§2 の箇条書き「**設定ファイルからのdeny層解除不可(force-push保護を除く)**: …」を次で置き換える(既存の後半の説明は残す。冒頭に信頼層の前提を足す):

```markdown
- **設定ファイルからのdeny層解除不可(force-push保護を除く)**: 0.7.0 から、プロジェクト直下の `.claude-hooks.json`(リポジトリ由来の信頼できない入力)は、グローバル設定 `trusted_projects` で承認されない限り**一切マージされない**(JSON として解析もしない)。したがって未承認リポジトリを clone して開いただけでは、プロジェクト設定は deny 判定にもコマンド実行(`quality_gate.commands`・`notify.command`・`scanners.*`)にも影響しない。承認済み(内容ハッシュ一致、またはピン留めなし `true`)のプロジェクト設定は従来どおり最高優先度で適用され、`bash_guard.allow` は ask 層の判定のみを解除できる。〔以下、既存の説明をそのまま続ける〕
```

同じ §2 に 1 項目追加:

```markdown
- **プロジェクト層の型不正でグローバル層の強化が消えない**: 設定は層ごとにマージ直後に検証し、不正な値はその層をマージする前の状態(直下の層)へ戻す(0.6.1)。承認済みプロジェクトが `{"exfil_guard": 0}` のような型すり替えを行っても、グローバルで設定した `mode: "always"` や `categories.pii: "deny"` は保たれる。
```

§4 既知の限界の末尾に 1 項目追加:

```markdown
11. **パスの同一性判定と書込先の正規化は別物** — `trusted_projects` のキーは `os.path.realpath(cwd)`(シンボリックリンク解決済みの同一性)で照合する。一方 `secrets_guard` の write_protected は Bash トークン/`file_path` をイベントの `cwd` 基準で絶対化するが `resolve()` しない(利用者が指定した「書こうとしている場所」の表記を見る)。前者は「同じプロジェクトか」、後者は「保護対象に書こうとしているか」という異なる問いに答えており、意図的に揃えていない。
```

- [ ] **Step 3: `CONTRIBUTING.md`**

「### 検証ゲート(loop-hooks)」節の直前に追加:

```markdown
### プロジェクト設定の承認(0.7.0 以降)

このリポジトリの `.claude-hooks.json`(`real-home-path` 規約の `custom_patterns` と、Loop Engineering のゲート設定・baseline の書込保護)は、clone 直後は**未承認**で無視される(プロジェクト設定のオプトイン信頼。`docs/configuration.md` §1 信頼層)。初回のツール呼び出しで `systemMessage` に承認エントリが印字されるので、次のどちらかを行う:

- 印字されたエントリを `$HOME/.claude/claude-hooks.json` の `"trusted_projects"` に貼り付ける(内容ピン留め。`.claude-hooks.json` を変更するたびに再承認)。メンテナは値を `true`(ピン留めなし)にしてもよい
- あるいは `real-home-path` の `custom_patterns` と `secrets_guard.write_protected_paths` を**自分のグローバル設定に入れる**(承認や worktree の有無と無関係に常時有効。コントリビュータにはこちらが堅牢)

未承認のままでも CI のリークチェックとブランチレビューは残るので、作業は止まらない。
```

- [ ] **Step 4: `README.md` / `README.ja.md`**

`README.md` の段落「Put project-shared settings in `.claude-hooks.json` at your repo root; …」の直後に 1 段落:

```markdown
**Since 0.7.0, a repo-level `.claude-hooks.json` is only applied after you approve it** in `~/.claude/claude-hooks.json` (`"trusted_projects"`: content hash, `true`, or `false`). Unapproved project config is ignored (not even parsed) and the hook prints a paste-ready approval entry — so cloning a repository can never weaken your guards or run commands from its config. See [docs/configuration.md](docs/configuration.md) §1.
```

`README.ja.md` の段落「チームで共有する設定はリポジトリ直下の `.claude-hooks.json` に、…」の直後に:

```markdown
**0.7.0 以降、リポジトリ直下の `.claude-hooks.json` は `~/.claude/claude-hooks.json` の `"trusted_projects"` で承認したときだけ適用されます**(内容ハッシュ / `true` / `false`)。未承認のプロジェクト設定は無視され(解析もされず)、フックが貼り付け可能な承認エントリを印字します — clone しただけでガードが緩んだり設定由来のコマンドが走ったりすることはありません。詳細: [docs/configuration.md](docs/configuration.md) §1。
```

- [ ] **Step 5: `CHANGELOG.md`・バージョン**

`## [0.6.1] - 2026-08-23` の前に:

```markdown
## [0.7.0] - <実装日>

### Changed(破壊的変更)
- **プロジェクト直下の `.claude-hooks.json` は承認制になった。** グローバル設定 `$HOME/.claude/claude-hooks.json` の `trusted_projects` に、プロジェクトの `realpath` をキーとして内容ハッシュ(`"sha256:…"`、既定)/ `true`(ピン留めなし)/ `false`(明示的な不承認)を登録したプロジェクトのみマージする。未承認のプロジェクト設定は JSON として解析せず無視し、`systemMessage` に貼り付け可能な承認エントリを印字する(既定 1 時間のクールダウン、`notice_cooldown_sec` で調整)。**既存利用者のプロジェクト設定は承認するまで無効になる。** 背景: 敵対的リポジトリを clone して開くだけで deny 判定の緩和(`allow_paths`・`allow`・`trusted_servers`・`categories: "off"`・`protected_branches: []`)とコマンド実行(`quality_gate.commands`・`notify.command`・`scanners.*`)に到達できた(セキュリティスキャン 12 件の単一根本原因)。denylist 方式は列挙漏れで 2 度却下されたため、列挙そのものを廃止するオプトイン方式を採用。設計: `docs/superpowers/specs/2026-07-26-project-config-trust-optin-design.md`

### Added
- `hooks/lib/trust.py`: 承認判定・通知文面・状態ファイル(`$HOME/.claude/safe-dev-hooks-state.json`: 未承認通知のクールダウンとピン留めなし承認の変化検出)
- `hook_io.finalize` が `_notices`(意図的に採用しなかった設定の通知)を `_errors` と分けて合成。`audit_log` は通知を出さない
```

`<実装日>` は実施日に置き換える。`pyproject.toml` と `.claude-plugin/plugin.json` の `0.6.1` → `0.7.0`、`uv lock` を実行して `uv.lock` を追従させる。

- [ ] **Step 6: 検証・Commit**

Run: `uv run pytest tests/test_packaging.py -q && uv run python scripts/verify.py quick; echo "quick=$?"`

```bash
git add docs/configuration.md docs/security-model.md CONTRIBUTING.md README.md README.ja.md CHANGELOG.md pyproject.toml .claude-plugin/plugin.json uv.lock
git commit -m "docs(release): 0.7.0 — プロジェクト設定のオプトイン信頼を文書化(信頼層・承認手順・破壊的変更)"
```

---

### Task 7: 全体確認・自リポジトリのドッグフーディング・引き渡し

**Files:** なし(確認・報告。ユーザー手動作業の提示)

- [ ] **Step 1: 全体検証**

Run: `uv run python scripts/verify.py all; echo "exit=$?"; cat .loop/mutation-baseline.json; git status --short; git log --oneline main..HEAD`
Expected: exit 0(6 ファイルの baseline: `trust.py` を含む)、working tree clean

- [ ] **Step 2: ライブ確認(この checkout は稼働中プラグイン)**

マージ前でも、このブランチを checkout している間は新挙動が稼働する。このリポジトリ自身の `.claude-hooks.json` は**未承認**なので、次のツール呼び出しで `systemMessage` に未承認通知(承認エントリ付き)が出るはず。出た通知のエントリを報告に含める(パスは `$HOME/...` に置換して記載)。

- [ ] **Step 3: ユーザー手動作業(報告に含める)**

1. `$HOME/.claude/claude-hooks.json`(無ければ作成)に、このリポジトリの承認を追加(メンテナなのでピン留めなし推奨):
   ```json
   { "trusted_projects": { "<このリポジトリの realpath>": true } }
   ```
   利用側(rakuten-optimizer / news-collector)も同様に追加(各リポジトリで最初に出る通知のエントリを貼るか `true`)
2. 既存の `.claude-hooks.json` 書込保護はそのまま(承認後に有効)。`$HOME/.claude/claude-hooks.json` は `write_protected`(`claude-hooks.json`)対象なのでエージェントは書けない — ユーザーの手作業
3. マージ後の `git push origin main` / タグ `v0.7.0` の判断

---

## Self-Review

- **Spec coverage**: #1 スキーマ・分類・厳密真偽値・防御 → Task 1(`classify_entry`/`gate`)+ Task 2(`DEFAULTS`、型不正の縮退)/ #2 判定フロー(read_bytes・非承認は解析しない・同一バイト列を解析・表の 6 行)→ Task 1 `gate` + Task 2 `_load_config` / #3 通知の配線(`_notices`・`finalize`・`audit_log` 抑止)と流量(クールダウン・不一致は常に・ピン留めなしは変化時のみ・`false` は無し・状態ファイルのベストエフォート)→ Task 1 + Task 4 / #4 承認の意味 → Task 6 docs / #5 → 0.6.1 済(Task 5 の型すり替え回帰テストで確認)/ 保証する・しない → Task 5 ブラックボックス + Task 2 テスト / 副作用(ドッグフーディング・worktree・チーム UX)→ Task 6 docs + Task 7 手動 / Loop Engineering との接続 → Task 1 `only_mutate`、各タスクの mutation 確認 / テスト一覧(両方向)→ Task 1・2・4・5 に分配(未承認で全シンク無効・型すり替え 6 形状・ハッシュ不一致・ワイルドカード/未知接頭辞/長さ不正/非 str・truthy 非真偽値・`False`・`trusted_projects` 自体の型不正・自己承認不可・不正 UTF-8 の非承認/承認・承認済みは従来同一・グローバルのみ環境は不変・ファイル無しは無通知・`cwd` None/不在/symlink・realpath・`audit_log` 無通知・クールダウン/不一致は対象外/ピン留めなしの変化・状態ファイル不能・`False` 無通知)/ ドキュメント・リリース → Task 6
- **Placeholder scan**: Task 6 Step 5 の `<実装日>` と Task 7 の `<このリポジトリの realpath>` は実施時に埋める指示つき。他に TBD なし
- **Type consistency**: `trust.gate(raw, cwd, trusted_projects, cooldown_sec, *, now=None, state_path=None) -> Verdict(adopt, notices)`、`trust.cooldown_seconds`、`trust.content_hash`、`trust.project_key`、通知関数 3 種、`load_state`/`save_state`、`hook_io.finalize(out, cfg, quiet_notices=False)`、`helpers.approve_project(monkeypatch, global_path, proj, global_cfg=None, pinned=False)`、`DEFAULTS["trusted_projects"]`/`["notice_cooldown_sec"]`、`cfg["_notices"]` を各タスクで同名使用
