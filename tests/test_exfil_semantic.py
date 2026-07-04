import subprocess

from helpers import load_hook

exfil_guard = load_hook("pre_tool_use/exfil_guard.py")

CFG = {
    "categories": {"semantic": "ask"},
    "semantic": {"model": "haiku", "min_payload_chars": 10},
}

LONG_PAYLOAD = "当社の第3四半期の未公開売上見込みは前年比12%減で、田中部長の人事評価は..." * 3


class FakeCompleted:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


def test_semantic_detects_sensitive(monkeypatch):
    monkeypatch.setattr(exfil_guard.shutil, "which", lambda _: "/usr/bin/claude")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env") or {}
        return FakeCompleted('{"sensitive": true, "reason": "未公開の業績情報"}')

    monkeypatch.setattr(exfil_guard.subprocess, "run", fake_run)
    result = exfil_guard.semantic_check(LONG_PAYLOAD, CFG)
    assert result == {"sensitive": True, "reason": "未公開の業績情報"}
    assert captured["cmd"][0] == "claude"
    assert "--model" in captured["cmd"]
    assert captured["env"].get("SAFE_DEV_HOOKS_SEMANTIC") == "1"


def test_semantic_not_sensitive_returns_none(monkeypatch):
    monkeypatch.setattr(exfil_guard.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        exfil_guard.subprocess, "run",
        lambda *a, **k: FakeCompleted('{"sensitive": false, "reason": ""}'),
    )
    assert exfil_guard.semantic_check(LONG_PAYLOAD, CFG) is None


def test_semantic_skips_short_payload(monkeypatch):
    monkeypatch.setattr(exfil_guard.shutil, "which", lambda _: "/usr/bin/claude")

    def boom(*a, **k):
        raise AssertionError("呼ばれてはいけない")

    monkeypatch.setattr(exfil_guard.subprocess, "run", boom)
    assert exfil_guard.semantic_check("短い", CFG) is None


def test_semantic_skips_when_cli_missing(monkeypatch):
    monkeypatch.setattr(exfil_guard.shutil, "which", lambda _: None)
    assert exfil_guard.semantic_check(LONG_PAYLOAD, CFG) is None


def test_semantic_skips_when_recursion_guard_set(monkeypatch):
    monkeypatch.setattr(exfil_guard.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setenv("SAFE_DEV_HOOKS_SEMANTIC", "1")
    assert exfil_guard.semantic_check(LONG_PAYLOAD, CFG) is None


def test_semantic_fail_open_on_error(monkeypatch):
    monkeypatch.setattr(exfil_guard.shutil, "which", lambda _: "/usr/bin/claude")

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=30)

    monkeypatch.setattr(exfil_guard.subprocess, "run", fake_run)
    assert exfil_guard.semantic_check(LONG_PAYLOAD, CFG) is None


def test_semantic_garbage_output_returns_none(monkeypatch):
    monkeypatch.setattr(exfil_guard.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        exfil_guard.subprocess, "run", lambda *a, **k: FakeCompleted("判定できません")
    )
    assert exfil_guard.semantic_check(LONG_PAYLOAD, CFG) is None
