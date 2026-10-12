import subprocess
from pathlib import Path

import pytest

from hooks.lib import llm_judge


class FakeCompleted:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


EXPECTED_ARGV = [
    "claude", "-p", "--model", "sonnet", "--tools=",
    "--setting-sources=", "--strict-mcp-config", "--no-session-persistence",
]


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
    assert captured["cmd"] == EXPECTED_ARGV
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


def test_run_json_isolates_child_in_fresh_empty_cwd(monkeypatch, cli_present, tmp_path):
    """子の claude をプロジェクト外の空ディレクトリで動かし、終了後に片付ける。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/home/alice/project")
    captured = {}

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs["cwd"])
        captured["cwd"] = cwd
        captured["existed_empty"] = cwd.is_dir() and not any(cwd.iterdir())
        captured["env"] = kwargs["env"]
        return FakeCompleted('{"ok": true}')

    monkeypatch.setattr(llm_judge.subprocess, "run", fake_run)
    llm_judge.run_json("p", "sonnet", 1)
    cwd = captured["cwd"]
    assert captured["existed_empty"] is True
    assert cwd.resolve() != tmp_path.resolve()
    assert cwd.resolve() != Path(__file__).resolve().parent.parent
    assert not cwd.exists()
    assert "CLAUDE_PROJECT_DIR" not in captured["env"]
    assert captured["env"]["SAFE_DEV_HOOKS_SEMANTIC"] == "1"


def test_run_json_removes_cwd_even_on_failure(monkeypatch, cli_present):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cwd"] = Path(kwargs["cwd"])
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(llm_judge.subprocess, "run", fake_run)
    with pytest.raises(llm_judge.JudgeError):
        llm_judge.run_json("p", "m", 1)
    assert not captured["cwd"].exists()


def test_run_json_nonzero_exit_includes_stderr_tail(monkeypatch, cli_present):
    done = FakeCompleted("", returncode=2)
    done.stderr = "x" * 500 + "\nError: not logged in\n"
    monkeypatch.setattr(llm_judge.subprocess, "run", lambda *a, **k: done)
    with pytest.raises(llm_judge.JudgeError) as info:
        llm_judge.run_json("p", "m", 1)
    message = str(info.value)
    assert "終了コード 2" in message
    assert "Error: not logged in" in message
    assert "x" * 201 not in message
    tail = message.split(": ", 1)[1]
    assert len(tail) <= 200


def test_run_json_nonzero_exit_without_stderr_has_no_tail(monkeypatch, cli_present):
    monkeypatch.setattr(
        llm_judge.subprocess, "run", lambda *a, **k: FakeCompleted("", returncode=3)
    )
    with pytest.raises(llm_judge.JudgeError) as info:
        llm_judge.run_json("p", "m", 1)
    assert str(info.value) == "claude CLI が終了コード 3 で終了しました"
