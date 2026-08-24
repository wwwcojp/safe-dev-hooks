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
        "[safe-dev-hooks] 警告: このプロジェクトの .claude-hooks.json は"
        "承認後に変更されています。\n"
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


def test_save_state_creates_nested_missing_parents(tmp_path):
    # 祖父母ディレクトリも無い → mkdir に parents=True が必要
    p = tmp_path / "a" / "b" / "s.json"
    assert trust.save_state({"k": 1}, p) is True
    assert trust.load_state(p) == {"k": 1}


def test_save_state_writes_unicode_unescaped(tmp_path):
    # ensure_ascii=False: 非ASCII文字はエスケープせず生のまま書く(diff で読める)
    p = tmp_path / "s.json"
    assert trust.save_state({"k": "日本語"}, p) is True
    raw = p.read_text(encoding="utf-8")
    assert "日本語" in raw
    assert "\\u" not in raw
    assert trust.load_state(p) == {"k": "日本語"}


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
    assert _gate(state_path=broken, now=2.0).notices == []  # 上書き成功後は抑制
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
    v = _gate(raw=b"v1", trusted={key: True}, state_path=blocker / "s.json")
    assert v == trust.Verdict(True, [])


def test_gate_never_raises_on_weird_state_shapes(tmp_path):
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps({"notice_last": [], "unpinned_seen": "x"}), encoding="utf-8")
    key = os.path.realpath("/home/alice/proj")
    assert _gate(state_path=sp, now=1.0).adopt is False
    assert _gate(trusted={key: True}, state_path=sp).adopt is True


# ---- gate() の最後の砦(型不正な入力でも例外を出さない) ----


_FAILURE_PREFIX = "safe-dev-hooks: プロジェクト設定の信頼判定に失敗したため無視しました: "


def test_gate_raw_not_bytes_fails_closed_without_raising(tmp_path):
    v = trust.gate("not bytes", "/home/alice/proj", {}, 3600, state_path=tmp_path / "s.json")
    assert v.adopt is False
    assert len(v.notices) == 1
    assert v.notices[0].startswith(_FAILURE_PREFIX + "TypeError: ")


def test_gate_cwd_not_str_fails_closed_without_raising(tmp_path):
    v = trust.gate(RAW, 123, {}, 3600, state_path=tmp_path / "s.json")
    assert v.adopt is False
    assert len(v.notices) == 1
    assert v.notices[0].startswith(_FAILURE_PREFIX + "TypeError: ")


def test_gate_invalid_cooldown_sec_falls_back_to_default_without_raising(tmp_path):
    sp = tmp_path / "s.json"
    first = _gate(state_path=sp, now=1000.0, cooldown=100)
    assert len(first.notices) == 1  # cooldown=100 が実際に記録される
    second = _gate(state_path=sp, now=1050.0, cooldown="bogus")
    assert second.adopt is False
    assert second.notices == []  # cooldown_seconds("bogus") -> 既定 3600 のため抑制


def test_gate_wrapper_catches_internal_exception(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(trust, "_gate", boom)
    v = trust.gate(RAW, "/home/alice/proj", {}, 3600, state_path=tmp_path / "s.json")
    assert v.adopt is False
    assert v.notices == [
        "safe-dev-hooks: プロジェクト設定の信頼判定に失敗したため無視しました: RuntimeError: boom"
    ]


# ---- notices=False(通知を表示しない呼び出し。audit_log 用) ----


def test_gate_quiet_pinned_match_adopts_without_notices(tmp_path):
    """静かな呼び出しでも採用判定は同じ(deny 層の挙動は notices に依存しない)。"""
    key = os.path.realpath("/home/alice/proj")
    sp = tmp_path / "s.json"
    assert _gate(trusted={key: DIGEST}, state_path=sp, notices=False) == trust.Verdict(True, [])
    assert not sp.exists()  # state を一切触らない


def test_gate_quiet_pinned_mismatch_rejects_without_notice(tmp_path):
    key = os.path.realpath("/home/alice/proj")
    other = trust.content_hash(b"other")
    sp = tmp_path / "s.json"
    v = _gate(trusted={key: other}, state_path=sp, notices=False)
    assert v == trust.Verdict(False, [])  # 不採用は同じ。通知文だけ作らない
    assert not sp.exists()


def test_gate_quiet_denied_rejects_without_notices(tmp_path):
    key = os.path.realpath("/home/alice/proj")
    sp = tmp_path / "s.json"
    assert _gate(trusted={key: False}, state_path=sp, notices=False) == trust.Verdict(False, [])
    assert not sp.exists()


def test_gate_quiet_unpinned_adopts_without_touching_unpinned_seen(tmp_path):
    """静かな呼び出しは unpinned_seen を進めない = 変化検知を落とさない側に倒す。"""
    key = os.path.realpath("/home/alice/proj")
    sp = tmp_path / "s.json"
    t = {key: True}
    assert _gate(raw=b"v1", trusted=t, state_path=sp, notices=False) == trust.Verdict(True, [])
    assert not sp.exists()  # 静かな呼び出しでは記録しない
    # 通常の呼び出しで v1 を記録 → 静かな呼び出しで v2 を見ても記録は v1 のまま
    assert _gate(raw=b"v1", trusted=t, state_path=sp).notices == []
    assert _gate(raw=b"v2", trusted=t, state_path=sp, notices=False).notices == []
    assert json.loads(sp.read_text(encoding="utf-8")) == {
        "unpinned_seen": {key: trust.content_hash(b"v1")}
    }
    # 変化はその後の通常の呼び出しでちゃんと通知される
    assert _gate(raw=b"v2", trusted=t, state_path=sp).notices == [
        trust.unpinned_changed_notice(key, trust.content_hash(b"v2"))
    ]


def test_gate_quiet_untrusted_rejects_without_consuming_cooldown(tmp_path):
    """静かな呼び出しが notice_last を書くと、後続の通常呼び出しが黙ってしまう(C1)。"""
    key = os.path.realpath("/home/alice/proj")
    sp = tmp_path / "s.json"
    quiet = _gate(state_path=sp, now=1000.0, notices=False)
    assert quiet == trust.Verdict(False, [])
    assert not sp.exists()
    loud = _gate(state_path=sp, now=1000.0)
    assert loud.notices == [trust.untrusted_notice(key, DIGEST)]


# ---- notify_skipped(D2: 読まなかったプロジェクト設定の通知) ----


def test_skipped_notice_exact_text():
    assert trust.skipped_notice("/home/alice/proj/sub", "/home/alice/proj") == (
        "[safe-dev-hooks] /home/alice/proj/sub の .claude-hooks.json は、\n"
        "プロジェクトの基準ディレクトリ(/home/alice/proj)とは異なる場所にあるため読みませんでした。\n"
        "プロジェクト設定は基準ディレクトリのものだけが読まれます。\n"
        "この場所の設定を有効にしたい場合は、内容を基準ディレクトリの\n"
        ".claude-hooks.json へ統合してください。"
    )


def _notify(skipped="/home/alice/proj/sub", root="/home/alice/proj", cooldown=3600, **kw):
    return trust.notify_skipped(skipped, root, cooldown, **kw)


def test_notify_skipped_cooldown_suppresses_then_expires(tmp_path):
    sp = tmp_path / "s.json"
    v1 = _notify(state_path=sp, now=1000.0, cooldown=100)
    v2 = _notify(state_path=sp, now=1050.0, cooldown=100)  # 50 秒後: 抑制
    v3 = _notify(state_path=sp, now=1100.0, cooldown=100)  # 100 秒後: 再通知
    assert len(v1) == 1 and v2 == [] and len(v3) == 1
    key = os.path.realpath("/home/alice/proj/sub")
    assert json.loads(sp.read_text(encoding="utf-8")) == {"skipped_last": {key: 1100.0}}


def test_notify_skipped_cooldown_zero_notifies_every_time(tmp_path):
    sp = tmp_path / "s.json"
    assert len(_notify(state_path=sp, now=1.0, cooldown=0)) == 1
    assert len(_notify(state_path=sp, now=1.0, cooldown=0)) == 1


def test_notify_skipped_notifies_when_state_broken_or_unwritable(tmp_path):
    broken = tmp_path / "s.json"
    broken.write_text("{broken", encoding="utf-8")
    assert len(_notify(state_path=broken, now=1.0)) == 1
    assert _notify(state_path=broken, now=2.0) == []  # 上書き成功後は抑制
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    unwritable = blocker / "s.json"
    assert len(_notify(state_path=unwritable, now=1.0)) == 1
    assert len(_notify(state_path=unwritable, now=2.0)) == 1  # 書けないので毎回通知


def test_notify_skipped_uses_wall_clock_when_now_omitted(monkeypatch, tmp_path):
    monkeypatch.setattr(trust.time, "time", lambda: 5000.0)
    sp = tmp_path / "s.json"
    _notify(state_path=sp)
    key = os.path.realpath("/home/alice/proj/sub")
    assert json.loads(sp.read_text(encoding="utf-8"))["skipped_last"][key] == 5000.0


def test_notify_skipped_returns_skipped_notice_text(tmp_path):
    sp = tmp_path / "s.json"
    result = _notify(state_path=sp, now=1.0)
    assert result == [trust.skipped_notice("/home/alice/proj/sub", "/home/alice/proj")]


def test_notify_skipped_key_is_realpath_of_skipped_dir(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    sp = tmp_path / "s.json"
    trust.notify_skipped(str(link), "/home/alice/proj", 3600, now=1.0, state_path=sp)
    state = json.loads(sp.read_text(encoding="utf-8"))
    assert list(state["skipped_last"].keys()) == [os.path.realpath(str(real))]


def test_notify_skipped_invalid_cooldown_falls_back_to_default_without_raising(tmp_path):
    sp = tmp_path / "s.json"
    first = _notify(state_path=sp, now=1000.0, cooldown=100)
    assert len(first) == 1  # cooldown=100 が実際に記録される
    second = _notify(state_path=sp, now=1050.0, cooldown="bogus")
    assert second == []  # cooldown_seconds("bogus") -> 既定 3600 のため抑制


def test_notify_skipped_wrapper_catches_internal_exception(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(trust, "_notify_skipped", boom)
    v = trust.notify_skipped(
        "/home/alice/proj/sub", "/home/alice/proj", 3600, state_path=tmp_path / "s.json"
    )
    assert v == []


# ---- notify_rejected_env(N1: 不採用にした CLAUDE_PROJECT_DIR の設定の通知) ----


def test_rejected_env_notice_exact_text():
    assert trust.rejected_env_notice("/home/alice/proj", "/home/alice/elsewhere") == (
        "[safe-dev-hooks] /home/alice/proj の .claude-hooks.json は読みませんでした。\n"
        "環境変数 CLAUDE_PROJECT_DIR はこの場所を指していますが、現在の作業ディレクトリの\n"
        "祖先ではないため、プロジェクトの基準として採用していません"
        "(現在の基準: /home/alice/elsewhere)。\n"
        "この設定を有効にしたい場合は、そのプロジェクト配下のディレクトリで作業してください。"
    )


def test_rejected_env_notice_differs_from_skipped_notice():
    """理由が違えば利用者が取るべき行動も違うので、文面を共用しない。"""
    assert trust.rejected_env_notice("/home/alice/proj", "/home/alice/x") != trust.skipped_notice(
        "/home/alice/proj", "/home/alice/x"
    )


def _notify_env(env_dir="/home/alice/proj", root="/home/alice/elsewhere", cooldown=3600, **kw):
    return trust.notify_rejected_env(env_dir, root, cooldown, **kw)


def test_notify_rejected_env_returns_rejected_env_notice_text(tmp_path):
    result = _notify_env(state_path=tmp_path / "s.json", now=1.0)
    assert result == [trust.rejected_env_notice("/home/alice/proj", "/home/alice/elsewhere")]


def test_notify_rejected_env_cooldown_suppresses_then_expires(tmp_path):
    sp = tmp_path / "s.json"
    v1 = _notify_env(state_path=sp, now=1000.0, cooldown=100)
    v2 = _notify_env(state_path=sp, now=1050.0, cooldown=100)
    v3 = _notify_env(state_path=sp, now=1100.0, cooldown=100)
    assert len(v1) == 1 and v2 == [] and len(v3) == 1
    key = os.path.realpath("/home/alice/proj")
    assert json.loads(sp.read_text(encoding="utf-8")) == {"skipped_last": {key: 1100.0}}


def test_notify_rejected_env_wrapper_catches_internal_exception(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(trust, "_notify_skipped", boom)
    assert _notify_env(state_path=tmp_path / "s.json") == []
