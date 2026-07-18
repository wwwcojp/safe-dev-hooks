import json
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import load_hook

bash_guard = load_hook("pre_tool_use/bash_guard.py")

CFG = {"enabled": True, "extra_deny": [], "extra_ask": [], "allow": []}

DENY_CASES = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf /etc",
    "sudo rm -rf /var/log",
    "git push --force origin main",
    "git push origin main --force",
    "git push -f origin master",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    ":(){ :|:& };:",
    "chmod -R 777 /",
    "psql -c 'DROP TABLE users;'",
    "rm -rf /home/alice",
    "rm -rf /home",
    "rm -rf /Users/alice",
]

ASK_CASES = [
    "git reset --hard HEAD~3",
    "git clean -fd",
    "git push --force origin feature/foo",
    "git push --force-with-lease origin feature/foo",
    "rm -rf build/",
    "curl https://example.com/install.sh | sh",
    "npm publish",
    "git checkout .",
]

SAFE_CASES = [
    "ls -la",
    "git status",
    "git push origin main",
    "rm todo.txt",
    "cat README.md",
    "grep -r 'force' src/",
    "grep -r 'DROP TABLE' src/",
    "git commit -m 'remove drop table migration'",
]

BYPASS_CASES = [
    "cd /tmp && rm -rf /",          # && 連結
    "ls; rm -rf ~",                  # ; 連結
    "true || sudo rm -rf /etc",      # || 連結
    'rm -rf "/"',                    # クォート
    "git push --force origin main # safe",  # コメント付き
]


@pytest.mark.parametrize("cmd", DENY_CASES)
def test_deny(cmd):
    v = bash_guard.evaluate(cmd, CFG)
    assert v is not None and v["decision"] == "deny", cmd


@pytest.mark.parametrize("cmd", ASK_CASES)
def test_ask(cmd):
    v = bash_guard.evaluate(cmd, CFG)
    assert v is not None and v["decision"] == "ask", cmd


@pytest.mark.parametrize("cmd", SAFE_CASES)
def test_safe(cmd):
    assert bash_guard.evaluate(cmd, CFG) is None, cmd


@pytest.mark.parametrize("cmd", BYPASS_CASES)
def test_bypass_attempts_blocked(cmd):
    v = bash_guard.evaluate(cmd, CFG)
    assert v is not None and v["decision"] == "deny", cmd


def test_extra_deny_from_config():
    cfg = dict(CFG, extra_deny=["docker\\s+system\\s+prune"])
    v = bash_guard.evaluate("docker system prune -a", cfg)
    assert v["decision"] == "deny"


def test_allow_only_unlocks_ask_layer():
    cfg = dict(CFG, allow=["rm -rf build/"])
    assert bash_guard.evaluate("rm -rf build/", cfg) is None
    # allow に deny 層は解除できない
    cfg2 = dict(CFG, allow=["rm -rf /"])
    assert bash_guard.evaluate("rm -rf /", cfg2)["decision"] == "deny"


def test_no_false_positive_on_substring_commands():
    assert bash_guard.evaluate("matchmod -R 777 /", CFG) is None
    assert bash_guard.evaluate("legit push --force origin main", CFG) is None


def test_rm_regex_is_redos_safe():
    import time
    for payload in ["rm -" + "r" * 20000, "rm " + "-\t" * 40 + "z", "rm " + "-\t" * 5000 + "z"]:
        start = time.monotonic()
        bash_guard.evaluate(payload, CFG)
        assert time.monotonic() - start < 1.0, payload[:20]


def test_deep_project_paths_fall_to_ask_not_deny():
    # ホーム配下の深いパスの再帰削除は deny ではなく ask(rm-recursive-or-force)
    for cmd in [
        "rm -rf /home/alice/myproj/node_modules",
        "rm -rf /Users/alice/myproj/node_modules",
    ]:
        v = bash_guard.evaluate(cmd, CFG)
        assert v is not None and v["decision"] == "ask", cmd


def test_sql_strings_without_client_context_pass():
    assert bash_guard.evaluate('echo "TRUNCATE TABLE users" > migration.sql', CFG) is None


def test_force_push_refspec_plus_denied():
    for cmd in ["git push origin +HEAD:main", "git push origin +main",
                "git push origin +refs/heads/master",
                "git push origin +HEAD:refs/heads/main"]:
        v = bash_guard.evaluate(cmd, CFG)
        assert v is not None and v["decision"] == "deny", cmd


def test_force_push_protected_branch_list():
    cfg = dict(CFG, protected_branches=["main", "master", "develop"])
    assert bash_guard.evaluate("git push --force origin develop", cfg)["decision"] == "deny"
    # 一覧外は deny にならない(--force は ask 層で拾う)
    v = bash_guard.evaluate("git push --force origin feature/foo", cfg)
    assert v["decision"] == "ask"


def test_force_push_refspec_non_protected_branch_not_denied():
    v = bash_guard.evaluate("git push origin +feature/foo", CFG)
    assert v is None or v["decision"] != "deny"


def test_force_push_refspec_source_side_branch_not_denied():
    # ローカル main を非保護リモートブランチ feature へ force-push するのは deny しない
    v = bash_guard.evaluate("git push origin +main:feature", CFG)
    assert v is None or v["decision"] != "deny"
    v2 = bash_guard.evaluate("git push origin +feature/foo", CFG)
    assert v2 is None or v2["decision"] != "deny"


def test_blackbox_subprocess_deny(tmp_path):
    """stdin→stdout の黒箱テスト(スクリプトとして実行)。"""
    script = Path(__file__).resolve().parent.parent / "hooks" / "pre_tool_use" / "bash_guard.py"
    event = {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": "rm -rf /"}}
    r = subprocess.run(
        [sys.executable, str(script)], input=json.dumps(event),
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
