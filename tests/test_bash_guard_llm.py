import json

import pytest
from helpers import load_hook

from hooks.lib import llm_judge

bash_guard = load_hook("pre_tool_use/bash_guard.py")

CFG = {"enabled": True, "extra_deny": [], "extra_ask": [], "allow": []}
DENY_CMD = "echo 'rm -rf /'"             # deny: rm-root-or-home + ask: rm-recursive-or-force
ASK_CMD = "git commit -m 'git reset --hard を避ける'"  # ask: git-reset-hard


class FakeCompleted:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


def _fake_llm(monkeypatch, verdicts_by_name=None, stdout=None, record=None):
    """claude CLI があるものとし、指定の判定を返す。verdicts_by_name: {rule: bool}。"""
    monkeypatch.setattr(llm_judge.shutil, "which", lambda name: "/usr/bin/claude")

    def fake_run(cmd, **kwargs):
        if record is not None:
            record.append(kwargs["input"])
        if stdout is not None:
            return FakeCompleted(stdout)
        body = {"verdicts": [
            {"rule": n, "false_positive": fp, "reason": f"{n} の理由"}
            for n, fp in verdicts_by_name.items()
        ]}
        return FakeCompleted(json.dumps(body, ensure_ascii=False))

    monkeypatch.setattr(llm_judge.subprocess, "run", fake_run)


def _review(cmd, cfg=CFG):
    mechanical = bash_guard.evaluate(cmd, cfg)
    return mechanical, bash_guard.llm_review(cmd, cfg, mechanical)


def _names(cmd, cfg=CFG):
    return [h["name"] for h in bash_guard.collect_hits(cmd, cfg)]


# --- 判定表 ---

def test_all_deny_false_positive_downgrades_to_ask(monkeypatch):
    _fake_llm(monkeypatch, {n: True for n in _names(DENY_CMD)})
    mechanical, (verdict, notice) = _review(DENY_CMD)
    assert mechanical["decision"] == "deny"
    assert verdict["decision"] == "ask"
    assert "LLM判定" in verdict["reason"] and "deny から ask に降格" in verdict["reason"]
    assert "rm-root-or-home" in verdict["reason"]
    assert "rm-root-or-home の理由" in verdict["reason"]
    assert notice is None


def test_some_deny_real_keeps_mechanical_deny(monkeypatch):
    cmd = "echo 'rm -rf /' ; sudo rm -rf /var/tmp/x"
    names = _names(cmd)
    assert "sudo-rm" in names
    _fake_llm(monkeypatch, {n: (n != "sudo-rm") for n in names})
    mechanical, (verdict, notice) = _review(cmd)
    assert verdict == mechanical
    assert notice is None


def test_deny_false_positive_with_real_ask_mentions_ask(monkeypatch):
    names = _names(DENY_CMD)
    _fake_llm(monkeypatch, {n: (n != "rm-recursive-or-force") for n in names})
    _, (verdict, _) = _review(DENY_CMD)
    assert verdict["decision"] == "ask"
    assert "rm-recursive-or-force" in verdict["reason"]


def test_all_ask_false_positive_returns_nothing_with_notice(monkeypatch):
    _fake_llm(monkeypatch, {n: True for n in _names(ASK_CMD)})
    mechanical, (verdict, notice) = _review(ASK_CMD)
    assert mechanical["decision"] == "ask"
    assert verdict is None
    assert "git-reset-hard" in notice and "git-reset-hard の理由" in notice
    assert notice.startswith("[safe-dev-hooks] bash_guard:")


def test_some_ask_real_keeps_mechanical_ask(monkeypatch):
    cmd = ASK_CMD + " ; git clean -fd"
    names = _names(cmd)
    _fake_llm(monkeypatch, {n: (n != "git-clean-force") for n in names})
    mechanical, (verdict, notice) = _review(cmd)
    assert verdict == mechanical
    assert notice is None


def test_llm_review_disabled_does_not_call_llm(monkeypatch):
    record = []
    _fake_llm(monkeypatch, {n: True for n in _names(DENY_CMD)}, record=record)
    cfg = dict(CFG, llm_review={"enabled": False, "model": "sonnet"})
    mechanical, (verdict, notice) = _review(DENY_CMD, cfg)
    assert verdict == mechanical and notice is None and record == []


def test_model_setting_is_passed(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm_judge.shutil, "which", lambda name: "/usr/bin/claude")

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return FakeCompleted('{"verdicts": []}')

    monkeypatch.setattr(llm_judge.subprocess, "run", fake_run)
    cfg = dict(CFG, llm_review={"enabled": True, "model": "opus"})
    _review(ASK_CMD, cfg)
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "opus"


def test_enabled_false_sends_only_deny_hits(monkeypatch):
    record = []
    cfg = dict(CFG, enabled=False)
    _fake_llm(monkeypatch, {n: True for n in _names(DENY_CMD, cfg)}, record=record)
    _review(DENY_CMD, cfg)
    assert "rm-root-or-home" in record[0]
    assert "rm-recursive-or-force" not in record[0]


def test_allowed_ask_hit_is_not_sent(monkeypatch):
    record = []
    cfg = dict(CFG, allow=[r"rm -rf"])
    _fake_llm(monkeypatch, {n: True for n in _names(DENY_CMD, cfg)}, record=record)
    _review(DENY_CMD, cfg)
    assert "rm-recursive-or-force" not in record[0]


# --- 判定不能 → 機械判定そのまま ---

@pytest.mark.parametrize("cmd", [DENY_CMD, ASK_CMD])
def test_cli_missing_keeps_mechanical_without_notice(cmd):
    mechanical, (verdict, notice) = _review(cmd)  # conftest が claude を隠している
    assert verdict == mechanical and notice is None


def test_recursion_guard_keeps_mechanical_without_notice(monkeypatch):
    _fake_llm(monkeypatch, {n: True for n in _names(DENY_CMD)})
    monkeypatch.setenv("SAFE_DEV_HOOKS_SEMANTIC", "1")
    mechanical, (verdict, notice) = _review(DENY_CMD)
    assert verdict == mechanical and notice is None


@pytest.mark.parametrize(
    "stdout",
    [
        "判定できません",
        '{"verdicts": "x"}',
        '{"verdicts": [{"rule": "rm-root-or-home", "false_positive": "true"}]}',
        '{"verdicts": [{"rule": "rm-root-or-home", "false_positive": 1}]}',
        '{"verdicts": [{"rule": "no-such-rule", "false_positive": true}]}',
        '{"verdicts": [{"rule": ["x"], "false_positive": true}]}',
        '{"verdicts": ["x"]}',
        '{"verdicts": [{"rule": "rm-root-or-home", "false_positive": true}]}',  # 欠落
    ],
)
def test_invalid_output_keeps_mechanical_with_notice(monkeypatch, stdout):
    _fake_llm(monkeypatch, stdout=stdout)
    mechanical, (verdict, notice) = _review(DENY_CMD)
    assert verdict == mechanical
    prefix = "[safe-dev-hooks] bash_guard: LLM判定に失敗したため機械判定を採用しました"
    assert notice.startswith(prefix)


def test_timeout_keeps_mechanical_with_notice(monkeypatch):
    import subprocess

    monkeypatch.setattr(llm_judge.shutil, "which", lambda name: "/usr/bin/claude")

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=45)

    monkeypatch.setattr(llm_judge.subprocess, "run", fake_run)
    mechanical, (verdict, notice) = _review(DENY_CMD)
    assert verdict == mechanical and "タイムアウト" in notice


def test_unexpected_exception_keeps_mechanical_with_notice(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bash_guard, "collect_hits", boom)
    mechanical = {"decision": "deny", "reason": "x"}
    verdict, notice = bash_guard.llm_review(DENY_CMD, CFG, mechanical)
    assert verdict == mechanical and "boom" in notice


def test_no_hits_reproduced_keeps_mechanical(monkeypatch):
    """機械判定はヒットしたのに collect_hits が空 → 全件偽陽性と誤認しない。"""
    _fake_llm(monkeypatch, stdout='{"verdicts": []}')
    monkeypatch.setattr(bash_guard, "collect_hits", lambda c, cfg: [])
    mechanical = {"decision": "ask", "reason": "x"}
    verdict, notice = bash_guard.llm_review(ASK_CMD, CFG, mechanical)
    assert verdict == mechanical and notice


def test_delimiter_collision_is_undecidable(monkeypatch):
    record = []
    _fake_llm(monkeypatch, {n: True for n in _names(DENY_CMD)}, record=record)
    monkeypatch.setattr(bash_guard.secrets, "token_hex", lambda n: "abc")
    cmd = DENY_CMD + " # CMD-abc"
    mechanical, (verdict, notice) = _review(cmd)
    assert verdict == mechanical and notice and record == []


def test_llm_review_too_long_command_is_undecidable(monkeypatch):
    record = []
    cmd = DENY_CMD + " " + "a" * bash_guard.LLM_MAX_COMMAND
    _fake_llm(monkeypatch, {n: True for n in _names(cmd)}, record=record)
    mechanical, (verdict, notice) = _review(cmd)
    assert verdict == mechanical and notice and record == []


# --- parse_verdicts ---

HITS = [{"name": "a", "decision": "deny"}, {"name": "b", "decision": "ask"}]


def test_parse_verdicts_duplicate_same_value_is_accepted():
    data = {"verdicts": [
        {"rule": "a", "false_positive": True, "reason": "r1"},
        {"rule": "a", "false_positive": True, "reason": "r2"},
        {"rule": "b", "false_positive": False},
    ]}
    assert bash_guard.parse_verdicts(data, HITS) == {"a": (True, "r1"), "b": (False, "")}


def test_parse_verdicts_duplicate_conflict_is_rejected():
    data = {"verdicts": [
        {"rule": "a", "false_positive": True},
        {"rule": "a", "false_positive": False},
        {"rule": "b", "false_positive": False},
    ]}
    with pytest.raises(ValueError):
        bash_guard.parse_verdicts(data, HITS)


def test_parse_verdicts_truncates_reason():
    data = {"verdicts": [
        {"rule": "a", "false_positive": True, "reason": "x" * 1000},
        {"rule": "b", "false_positive": True, "reason": 5},
    ]}
    result = bash_guard.parse_verdicts(data, HITS)
    assert result["a"] == (True, "x" * bash_guard.LLM_REASON_MAX)
    assert result["b"] == (True, "")


# --- build_prompt ---

def test_build_prompt_wraps_command_in_delimiter():
    hits = bash_guard.collect_hits(DENY_CMD, CFG)
    prompt = bash_guard.build_prompt(DENY_CMD, hits, "CMD-xyz")
    start = prompt.index("<<<CMD-xyz>>>")
    end = prompt.index("<<<END CMD-xyz>>>")
    assert start < prompt.index(DENY_CMD) < end
    assert "rm-root-or-home" in prompt
    assert hits[0]["description"] in prompt


def test_build_prompt_command_braces_are_not_substituted():
    cmd = "find / -exec rm {} + ; echo {rules} {command} {delimiter} ${HOME}"
    hits = bash_guard.collect_hits(cmd, CFG)
    prompt = bash_guard.build_prompt(cmd, hits, "CMD-xyz")
    assert cmd in prompt
    assert prompt.count("<<<CMD-xyz>>>") == 1  # コマンド内の {delimiter} は展開されない
    rules_part = prompt[prompt.index("## ヒットしたルール"):prompt.index("## 検査対象のコマンド")]
    assert "echo {rules}" not in rules_part  # コマンドがルール欄に入り込まない


def test_delimiter_differs_per_call(monkeypatch):
    record = []
    _fake_llm(monkeypatch, {n: True for n in _names(DENY_CMD)}, record=record)
    _review(DENY_CMD)
    _review(DENY_CMD)
    assert len(record) == 2 and record[0] != record[1]


# --- main ---

def _run_main(monkeypatch, capsys, tmp_path, command):
    import io

    from hooks.lib import config

    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "absent-global.json")
    event = {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": command}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        bash_guard.main()
    return capsys.readouterr().out


def test_main_downgrades_deny_to_ask(monkeypatch, capsys, tmp_path):
    _fake_llm(monkeypatch, {n: True for n in _names(DENY_CMD)})
    out = json.loads(_run_main(monkeypatch, capsys, tmp_path, DENY_CMD))
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "deny から ask に降格" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_ask_false_positive_emits_only_system_message(monkeypatch, capsys, tmp_path):
    _fake_llm(monkeypatch, {n: True for n in _names(ASK_CMD)})
    out = json.loads(_run_main(monkeypatch, capsys, tmp_path, ASK_CMD))
    assert "hookSpecificOutput" not in out
    assert "git-reset-hard" in out["systemMessage"]


def test_main_llm_failure_keeps_deny_and_notifies(monkeypatch, capsys, tmp_path):
    _fake_llm(monkeypatch, stdout="壊れた出力")
    out = json.loads(_run_main(monkeypatch, capsys, tmp_path, DENY_CMD))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "LLM判定に失敗" in out["systemMessage"]


def test_main_safe_command_does_not_call_llm(monkeypatch, capsys, tmp_path):
    record = []
    _fake_llm(monkeypatch, {}, record=record)
    assert _run_main(monkeypatch, capsys, tmp_path, "ls -la") == ""
    assert record == []
