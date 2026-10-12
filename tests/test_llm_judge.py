import subprocess

import pytest

from hooks.lib import llm_judge


class FakeCompleted:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


@pytest.fixture
def cli_present(monkeypatch):
    monkeypatch.setattr(llm_judge.shutil, "which", lambda name: "/usr/bin/claude")


def test_run_json_invokes_claude_with_tools_disabled_and_prompt_on_stdin(
    monkeypatch, cli_present
):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeCompleted('{"ok": true}')

    monkeypatch.setattr(llm_judge.subprocess, "run", fake_run)
    assert llm_judge.run_json("PROMPT", "sonnet", 45) == {"ok": True}
    assert captured["cmd"] == ["claude", "-p", "--model", "sonnet", "--tools="]
    assert captured["kwargs"]["input"] == "PROMPT"
    assert captured["kwargs"]["timeout"] == 45
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["env"]["SAFE_DEV_HOOKS_SEMANTIC"] == "1"


def test_run_json_extracts_json_surrounded_by_prose(monkeypatch, cli_present):
    monkeypatch.setattr(
        llm_judge.subprocess, "run",
        lambda *a, **k: FakeCompleted('判定結果です:\n{"a": 1}\n以上'),
    )
    assert llm_judge.run_json("p", "m", 1) == {"a": 1}


def test_run_json_unavailable_when_cli_missing(monkeypatch):
    with pytest.raises(llm_judge.Unavailable):
        llm_judge.run_json("p", "m", 1)


def test_run_json_unavailable_when_recursion_guard_set(monkeypatch, cli_present):
    monkeypatch.setenv("SAFE_DEV_HOOKS_SEMANTIC", "1")
    called = []
    monkeypatch.setattr(llm_judge.subprocess, "run", lambda *a, **k: called.append(1))
    with pytest.raises(llm_judge.Unavailable):
        llm_judge.run_json("p", "m", 1)
    assert called == []


def test_run_json_timeout_is_judge_error(monkeypatch, cli_present):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=45)

    monkeypatch.setattr(llm_judge.subprocess, "run", fake_run)
    with pytest.raises(llm_judge.JudgeError, match="タイムアウト"):
        llm_judge.run_json("p", "m", 45)


def test_run_json_oserror_is_judge_error(monkeypatch, cli_present):
    def fake_run(*a, **k):
        raise OSError("E2BIG")

    monkeypatch.setattr(llm_judge.subprocess, "run", fake_run)
    with pytest.raises(llm_judge.JudgeError, match="起動"):
        llm_judge.run_json("p", "m", 1)


def test_run_json_nonzero_exit_is_judge_error(monkeypatch, cli_present):
    monkeypatch.setattr(
        llm_judge.subprocess, "run", lambda *a, **k: FakeCompleted('{"a": 1}', returncode=1)
    )
    with pytest.raises(llm_judge.JudgeError, match="終了コード 1"):
        llm_judge.run_json("p", "m", 1)


@pytest.mark.parametrize("stdout", ["判定できません", "", '{"a": 1} {"b": 2}', "{broken"])
def test_run_json_unparseable_output_is_judge_error(monkeypatch, cli_present, stdout):
    monkeypatch.setattr(llm_judge.subprocess, "run", lambda *a, **k: FakeCompleted(stdout))
    with pytest.raises(llm_judge.JudgeError):
        llm_judge.run_json("p", "m", 1)


def test_run_json_non_object_json_is_judge_error(monkeypatch, cli_present):
    monkeypatch.setattr(llm_judge.subprocess, "run", lambda *a, **k: FakeCompleted("[{}]"))
    with pytest.raises(llm_judge.JudgeError):
        llm_judge.run_json("p", "m", 1)


def test_tests_never_call_real_claude():
    """conftest が claude を隠しているので、モックしない限り Unavailable になる。"""
    with pytest.raises(llm_judge.Unavailable):
        llm_judge.run_json("p", "m", 1)
