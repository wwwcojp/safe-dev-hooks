# safe-dev-hooks 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Codeで安全に開発するためのHooks集(8 Hooks + 共通lib + ルール定義 + プラグイン配布 + 文書)を構築する。

**Architecture:** 関心事別モジュール(1スクリプト=1関心事)のuv single-file Pythonスクリプト群。判定はJSON出力(`permissionDecision`)で統一し、ルールは `rules/*.json` でデータ駆動。設定は `.claude-hooks.json`(プロジェクト)→ `~/.claude/claude-hooks.json`(グローバル)→ ビルトイン既定の3層マージ。

**Tech Stack:** Python 3.10+(Hook本体は標準ライブラリのみ)、uv、pytest、ruff、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-07-03-safe-dev-hooks-design.md`(このプランの正)

## Global Constraints

- Hookスクリプトは**標準ライブラリのみ**(サードパーティ依存禁止)。先頭に uv script ヘッダ(`requires-python = ">=3.10"`)を付ける
- 判定は必ずJSON出力(`permissionDecision: deny/ask` + 理由)。**exit 2 は使わない**
- deny層は設定で解除不可。`allow` はask層のみ解除できる
- エラー方針: fail-open + `systemMessage` 可視化。ただし bash_guard / secrets_guard の判定中例外のみ fail-close(ask を返す)
- 全Hookスクリプトは `if __name__ == "__main__": main()` ガード必須(テストがimportするため)
- 出力の理由文は日本語。シークレット値そのものを理由文に含めない(先頭4文字+「…」にマスク)
- コミットメッセージは日本語 + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- テスト実行は `uv run pytest`、lint は `uv run ruff check hooks tests`
- 生成物(ログ等)は .gitignore 済み。コミット前に `git status --short` で不要ファイル混入がないこと

---

### Task 1: プロジェクト土台と lib/hook_io.py

**Files:**
- Create: `pyproject.toml`
- Create: `hooks/lib/__init__.py`(空)
- Create: `hooks/lib/hook_io.py`
- Create: `tests/conftest.py`
- Create: `tests/helpers.py`
- Test: `tests/test_hook_io.py`

**Interfaces:**
- Produces: `hook_io.read_event() -> dict` / `hook_io.emit(obj: dict) -> None` / `hook_io.pre_tool_decision(decision: str, reason: str) -> dict` / `hook_io.post_block(reason: str, context: str = "") -> dict` / `hook_io.finalize(out: dict | None, cfg: dict) -> None`(systemMessage合成+emit+exit 0)/ `hook_io.fail_open(hook_name: str, exc: Exception) -> None`
- Produces(テスト用): `helpers.load_hook(relpath: str)`(hooks/配下スクリプトのモジュール読込)

- [ ] **Step 1: pyproject.toml とテスト補助を作成**

`pyproject.toml`:

```toml
[project]
name = "safe-dev-hooks"
version = "0.1.0"
description = "Claude Codeで安全に開発するためのHooks集"
requires-python = ">=3.10"

[tool.uv]
package = false

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.8"]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "W"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`tests/conftest.py`:

```python
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))
```

`tests/helpers.py`:

```python
import importlib.util
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def load_hook(relpath: str):
    """hooks/配下のスクリプトをモジュールとして読み込む(__main__ガード前提)。"""
    path = HOOKS_DIR / relpath
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod
```

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_hook_io.py`:

```python
import io
import json

import pytest

from lib import hook_io


def test_read_event_parses_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"tool_name": "Bash"}'))
    assert hook_io.read_event() == {"tool_name": "Bash"}


def test_read_event_returns_empty_on_broken_json(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("not-json"))
    assert hook_io.read_event() == {}


def test_pre_tool_decision_shape():
    out = hook_io.pre_tool_decision("deny", "理由")
    assert out == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "理由",
        }
    }


def test_post_block_shape():
    out = hook_io.post_block("直してください", context="詳細")
    assert out["decision"] == "block"
    assert out["reason"] == "直してください"
    assert out["hookSpecificOutput"]["additionalContext"] == "詳細"


def test_finalize_emits_and_exits(capsys):
    with pytest.raises(SystemExit) as e:
        hook_io.finalize({"decision": "block", "reason": "x"}, {})
    assert e.value.code == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_finalize_appends_config_errors(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize(None, {"_errors": ["broken.json"]})
    out = json.loads(capsys.readouterr().out)
    assert "broken.json" in out["systemMessage"]


def test_finalize_silent_when_nothing(capsys):
    with pytest.raises(SystemExit):
        hook_io.finalize(None, {})
    assert capsys.readouterr().out == ""
```

- [ ] **Step 3: 失敗を確認**

Run: `uv run pytest tests/test_hook_io.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'lib'` など)

- [ ] **Step 4: hooks/lib/hook_io.py を実装**

`hooks/lib/__init__.py` は空ファイル。`hooks/lib/hook_io.py`:

```python
"""Hookの標準入出力処理。stdinイベント読取とJSON判定出力を担う。"""
import json
import sys


def read_event() -> dict:
    try:
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def emit(obj: dict) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def pre_tool_decision(decision: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def post_block(reason: str, context: str = "") -> dict:
    out: dict = {"decision": "block", "reason": reason}
    if context:
        out["hookSpecificOutput"] = {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }
    return out


def finalize(out: dict | None, cfg: dict) -> None:
    """判定出力に設定エラー警告を合成して出力し、exit 0 する。"""
    errors = cfg.get("_errors") or []
    if errors:
        out = dict(out or {})
        out["systemMessage"] = "[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: " + "; ".join(errors)
    if out:
        emit(out)
    sys.exit(0)


def fail_open(hook_name: str, exc: Exception) -> None:
    """Hook自体の異常時: ツール実行は止めないが必ず可視化する(fail-open)。"""
    emit({"systemMessage": f"[safe-dev-hooks] {hook_name} が異常終了したため検査をスキップしました: {exc}"})
    sys.exit(0)
```

- [ ] **Step 5: テストが通ることを確認**

Run: `uv run pytest tests/test_hook_io.py -v`
Expected: 7 passed

- [ ] **Step 6: コミット**

```bash
git add pyproject.toml hooks/lib tests
git commit -m "feat: プロジェクト土台と hook_io(イベント読取・判定出力・fail-open)を追加"
```

---

### Task 2: lib/config.py(3層マージ設定)

**Files:**
- Create: `hooks/lib/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: なし
- Produces: `config.load_config(cwd: str | None = None) -> dict`(DEFAULTS ← グローバル ← プロジェクトのdeepマージ。読込失敗は `_errors: list[str]` に記録)/ `config.DEFAULTS: dict` / `config.GLOBAL_CONFIG_PATH: Path`(テストはこれをmonkeypatchする)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_config.py`:

```python
import json

from lib import config


def test_defaults_when_no_files(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"
    assert cfg["quality_gate"]["mode"] == "block"
    assert cfg.get("_errors", []) == []


def test_project_overrides_global(monkeypatch, tmp_path):
    g = tmp_path / "global.json"
    g.write_text(json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", g)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "detect", "trusted_servers": ["mcp__kb"]}}),
        encoding="utf-8",
    )
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["exfil_guard"]["trusted_servers"] == ["mcp__kb"]
    # 未指定キーは既定値が残る(deepマージ)
    assert cfg["exfil_guard"]["categories"]["pii"] == "ask"


def test_broken_json_records_error_and_keeps_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text("{broken", encoding="utf-8")
    cfg = config.load_config(str(tmp_path))
    assert len(cfg["_errors"]) == 1
    assert cfg["exfil_guard"]["mode"] == "detect"


def test_non_dict_config_records_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text("[1,2]", encoding="utf-8")
    cfg = config.load_config(str(tmp_path))
    assert len(cfg["_errors"]) == 1
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL(`No module named 'lib.config'`)

- [ ] **Step 3: hooks/lib/config.py を実装**

```python
"""3層マージ設定(ビルトイン既定 ← グローバル ← プロジェクト)。"""
import copy
import json
from pathlib import Path

GLOBAL_CONFIG_PATH = Path.home() / ".claude" / "claude-hooks.json"
PROJECT_CONFIG_NAME = ".claude-hooks.json"

DEFAULTS: dict = {
    "bash_guard": {"enabled": True, "extra_deny": [], "extra_ask": [], "allow": []},
    "secrets_guard": {"enabled": True, "protected_paths": [], "allow_paths": []},
    "exfil_guard": {
        "enabled": True,
        "mode": "detect",
        "categories": {
            "credentials": "deny",
            "pii": "ask",
            "confidential_markers": "ask",
            "custom": "ask",
            "semantic": "ask",
        },
        "semantic": {"model": "haiku", "min_payload_chars": 200},
        "custom_patterns": [],
        "trusted_servers": [],
    },
    "exfil_output_scan": {"enabled": True, "action": "warn"},
    "quality_gate": {"enabled": True, "mode": "block", "commands": {}},
    "secrets_scan": {"enabled": True},
    "audit_log": {"enabled": True, "path": ".claude/logs"},
    "notify": {"enabled": True, "command": None},
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(cwd: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    errors: list[str] = []
    paths = [GLOBAL_CONFIG_PATH, Path(cwd or ".") / PROJECT_CONFIG_NAME]
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path}: オブジェクトではありません")
            continue
        cfg = _merge(cfg, data)
    cfg["_errors"] = errors
    return cfg
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 5: コミット**

```bash
git add hooks/lib/config.py tests/test_config.py
git commit -m "feat: 3層マージの設定読込(既定値・エラー記録付き)を追加"
```

---

### Task 3: lib/patterns.py とシークレット/PII/機密マーカールール

**Files:**
- Create: `hooks/lib/patterns.py`
- Create: `rules/secret_patterns.json`
- Create: `rules/pii_patterns.json`
- Create: `rules/confidential_markers.json`
- Test: `tests/test_patterns.py`

**Interfaces:**
- Produces: `patterns.load_rules(name: str) -> dict | list`(rules/からJSON読込)/ `patterns.scan_text(text: str, rules: list[dict]) -> list[dict]`(戻り値要素は `{"rule": str, "match": str}`)/ `patterns.luhn_ok(digits: str) -> bool` / `patterns.mynumber_ok(digits: str) -> bool` / `patterns.RULES_DIR: Path`
- ルール形式: `[{"name": str, "regex": str, "validator": "luhn"|"mynumber"(任意)}]`。confidential_markers.json のみ `{"markers": [str, ...]}`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_patterns.py`:

```python
from lib import patterns


def test_luhn_valid_and_invalid():
    assert patterns.luhn_ok("4111111111111111") is True
    assert patterns.luhn_ok("4111111111111112") is False
    assert patterns.luhn_ok("abc") is False


def test_mynumber_check_digit():
    # 11桁 "12345678901" のチェックデジットは 8(仕様書のアルゴリズム参照)
    assert patterns.mynumber_ok("123456789018") is True
    assert patterns.mynumber_ok("123456789012") is False
    assert patterns.mynumber_ok("12345") is False


def test_scan_detects_aws_key():
    rules = patterns.load_rules("secret_patterns.json")
    hits = patterns.scan_text("key=AKIAIOSFODNN7EXAMPLE ok", rules)
    assert any(h["rule"] == "aws-access-key" for h in hits)


def test_scan_detects_private_key_block():
    rules = patterns.load_rules("secret_patterns.json")
    hits = patterns.scan_text("-----BEGIN RSA PRIVATE KEY-----", rules)
    assert any(h["rule"] == "private-key-block" for h in hits)


def test_scan_generic_credential_assignment():
    rules = patterns.load_rules("secret_patterns.json")
    hits = patterns.scan_text('API_KEY = "supersecretvalue123"', rules)
    assert any(h["rule"] == "generic-credential" for h in hits)


def test_scan_clean_text_no_hits():
    rules = patterns.load_rules("secret_patterns.json")
    assert patterns.scan_text("普通のテキストです", rules) == []


def test_pii_credit_card_requires_luhn():
    rules = patterns.load_rules("pii_patterns.json")
    assert any(h["rule"] == "credit-card" for h in patterns.scan_text("4111 1111 1111 1111", rules))
    assert not any(
        h["rule"] == "credit-card" for h in patterns.scan_text("4111 1111 1111 1112", rules)
    )


def test_pii_mynumber_requires_check_digit():
    rules = patterns.load_rules("pii_patterns.json")
    assert any(h["rule"] == "my-number" for h in patterns.scan_text("番号: 123456789018", rules))
    assert not any(h["rule"] == "my-number" for h in patterns.scan_text("番号: 123456789012", rules))


def test_pii_email_and_phone():
    rules = patterns.load_rules("pii_patterns.json")
    text = "連絡先: taro@example.co.jp / 090-1234-5678"
    got = {h["rule"] for h in patterns.scan_text(text, rules)}
    assert {"email", "jp-phone"} <= got


def test_confidential_markers_file_shape():
    data = patterns.load_rules("confidential_markers.json")
    assert "社外秘" in data["markers"]
    assert "confidential" in data["markers"]
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_patterns.py -v`
Expected: FAIL

- [ ] **Step 3: ルールJSONと patterns.py を実装**

`rules/secret_patterns.json`:

```json
[
  {"name": "aws-access-key", "regex": "\\bAKIA[0-9A-Z]{16}\\b"},
  {"name": "github-token", "regex": "\\bgh[pousr]_[A-Za-z0-9]{36,255}\\b"},
  {"name": "github-fine-grained-token", "regex": "\\bgithub_pat_[A-Za-z0-9_]{22,255}\\b"},
  {"name": "slack-token", "regex": "\\bxox[baprs]-[A-Za-z0-9-]{10,}\\b"},
  {"name": "anthropic-api-key", "regex": "\\bsk-ant-[A-Za-z0-9_-]{20,}\\b"},
  {"name": "private-key-block", "regex": "-----BEGIN [A-Z ]*PRIVATE KEY-----"},
  {"name": "generic-credential", "regex": "(?i)(api[_-]?key|secret|token|passwd|password)\\s*[:=]\\s*['\\\"][^'\\\"]{8,}"}
]
```

`rules/pii_patterns.json`:

```json
[
  {"name": "email", "regex": "[\\w.+-]+@[\\w-]+\\.[\\w.-]+"},
  {"name": "jp-phone", "regex": "\\b0\\d{1,4}-\\d{1,4}-\\d{4}\\b"},
  {"name": "credit-card", "regex": "\\b\\d{4}[ -]?\\d{4}[ -]?\\d{4}[ -]?\\d{4}\\b", "validator": "luhn"},
  {"name": "my-number", "regex": "\\b\\d{12}\\b|\\b\\d{4}-\\d{4}-\\d{4}\\b", "validator": "mynumber"}
]
```

`rules/confidential_markers.json`:

```json
{
  "markers": [
    "社外秘", "部外秘", "極秘", "取扱注意", "マル秘", "㊙",
    "confidential", "internal only", "internal use only",
    "do not distribute", "trade secret"
  ]
}
```

`hooks/lib/patterns.py`:

```python
"""ルールJSONの読込と、テキストへのパターン適用(バリデータ付き)。"""
import json
import re
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent.parent / "rules"


def load_rules(name: str):
    return json.loads((RULES_DIR / name).read_text(encoding="utf-8"))


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 13:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mynumber_ok(digits: str) -> bool:
    """マイナンバーのチェックデジット検証(J-LIS方式)。"""
    if len(digits) != 12 or not digits.isdigit():
        return False
    p = digits[:11][::-1]  # 検査用数字を除く11桁を末尾から
    total = sum(
        int(p[n - 1]) * ((n + 1) if n <= 6 else (n - 5)) for n in range(1, 12)
    )
    rem = total % 11
    check = 0 if rem <= 1 else 11 - rem
    return check == int(digits[11])


_VALIDATORS = {"luhn": luhn_ok, "mynumber": mynumber_ok}


def scan_text(text: str, rules: list) -> list:
    findings = []
    for rule in rules:
        for m in re.finditer(rule["regex"], text):
            validator = _VALIDATORS.get(rule.get("validator", ""))
            if validator is not None:
                digits = re.sub(r"\D", "", m.group())
                if not validator(digits):
                    continue
            findings.append({"rule": rule["name"], "match": m.group()})
    return findings
```

> **実装時変更(D12・ユーザー承認済み)**: 当初案の「1ルール1件で打ち切り(break)」はレビュー指摘により「全マッチ収集(同一match文字列はルール内で重複排除・初出順維持、1ルール上限 `MAX_FINDINGS_PER_RULE = 20` 件)」へ変更した。redactマスキングで2件目以降の異なるシークレットが漏れる実害を防ぐため。

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_patterns.py -v`
Expected: 10 passed

- [ ] **Step 5: コミット**

```bash
git add hooks/lib/patterns.py rules tests/test_patterns.py
git commit -m "feat: パターン走査ライブラリとシークレット/PII/機密マーカールールを追加"
```

---

### Task 4: bash_guard(破壊的コマンドの deny/ask)

**Files:**
- Create: `hooks/pre_tool_use/bash_guard.py`
- Create: `rules/bash_deny.json`
- Create: `rules/bash_ask.json`
- Test: `tests/test_bash_guard.py`

**Interfaces:**
- Consumes: `hook_io.read_event/pre_tool_decision/finalize`、`config.load_config`、`patterns.load_rules`
- Produces: `bash_guard.evaluate(command: str, cfg: dict) -> dict | None`(`{"decision": "deny"|"ask", "reason": str}` または None)。`bash_guard._segments(command) -> list[str]`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_bash_guard.py`:

```python
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
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_bash_guard.py -v`
Expected: FAIL(ファイル不在)

- [ ] **Step 3: ルールJSONと bash_guard.py を実装**

`rules/bash_deny.json`:

```json
[
  {"name": "rm-root-or-home", "regex": "\\brm\\s+(-[^ ]+\\s+)*(/|/\\*|~|~/|\\$HOME)(\\s|$)"},
  {"name": "rm-system-dir", "regex": "\\brm\\s+(-[^ ]+\\s+)*/(etc|usr|var|bin|boot|lib|home|opt|srv)(/|\\s|$)"},
  {"name": "sudo-rm", "regex": "\\bsudo\\s+rm\\b"},
  {"name": "force-push-protected", "regex": "git\\s+push\\s+[^;|&]*(--force\\b|-f\\b)[^;|&]*\\b(main|master)\\b"},
  {"name": "force-push-protected-order", "regex": "git\\s+push\\s+[^;|&]*\\b(main|master)\\b[^;|&]*(--force\\b|-f\\b)"},
  {"name": "mkfs", "regex": "\\bmkfs(\\.|\\s)"},
  {"name": "dd-to-device", "regex": "\\bdd\\s+[^;|&]*of=/dev/"},
  {"name": "fork-bomb", "regex": ":\\(\\)\\s*\\{[^}]*\\}\\s*;?\\s*:"},
  {"name": "chmod-777-root", "regex": "chmod\\s+(-R\\s+)?777\\s+/(\\s|$)"},
  {"name": "redirect-to-device", "regex": ">\\s*/dev/sd[a-z]"},
  {"name": "sql-drop", "regex": "(?i)\\bDROP\\s+(TABLE|DATABASE)\\b"},
  {"name": "sql-truncate", "regex": "(?i)\\bTRUNCATE\\s+(TABLE\\s+)?\\w"}
]
```

`rules/bash_ask.json`:

```json
[
  {"name": "git-reset-hard", "regex": "git\\s+reset\\s+[^;|&]*--hard"},
  {"name": "git-clean-force", "regex": "git\\s+clean\\s+-[a-zA-Z]*f"},
  {"name": "git-force-push", "regex": "git\\s+push\\s+[^;|&]*(--force(-with-lease)?\\b|-f\\b)"},
  {"name": "rm-recursive-or-force", "regex": "\\brm\\s+(-[^ ]*[rRf][^ ]*\\s+)+"},
  {"name": "pipe-to-shell", "regex": "(curl|wget)[^;&]*\\|\\s*(sudo\\s+)?(ba|z|da)?sh\\b"},
  {"name": "npm-publish", "regex": "\\bnpm\\s+publish\\b"},
  {"name": "git-discard-worktree", "regex": "git\\s+(checkout|restore)\\s+(--\\s+)?\\.(\\s|$)"}
]
```

`hooks/pre_tool_use/bash_guard.py`:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Bashコマンドの破壊的操作を deny/ask の2段階でガードする。"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402


def _segments(command: str) -> list[str]:
    parts = re.split(r"(?:&&|\|\||;|\n)", command)
    return [p.strip() for p in parts if p.strip()]


def _normalize(text: str) -> str:
    # クォートによるすり抜け対策(過剰検知側に倒す)
    return text.replace('"', "").replace("'", "")


def evaluate(command: str, cfg: dict) -> dict | None:
    deny_rules = list(patterns.load_rules("bash_deny.json")) + [
        {"name": f"extra_deny:{p}", "regex": p} for p in cfg.get("extra_deny", [])
    ]
    ask_rules = list(patterns.load_rules("bash_ask.json")) + [
        {"name": f"extra_ask:{p}", "regex": p} for p in cfg.get("extra_ask", [])
    ]
    allow = cfg.get("allow", [])
    targets = [_normalize(s) for s in _segments(command)] + [_normalize(command)]
    for rule in deny_rules:
        if any(re.search(rule["regex"], t) for t in targets):
            return {
                "decision": "deny",
                "reason": f"破壊的コマンドを検出: {rule['name']}(deny層は設定で解除できません)",
            }
    for rule in ask_rules:
        for t in targets:
            if re.search(rule["regex"], t) and not any(re.search(a, t) for a in allow):
                return {
                    "decision": "ask",
                    "reason": f"注意が必要なコマンドを検出: {rule['name']}。実行してよいか確認してください",
                }
    return None


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") != "Bash":
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("bash_guard", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    command = (event.get("tool_input") or {}).get("command", "")
    try:
        verdict = evaluate(command, cfg)
    except Exception as exc:  # deny層の判定不能は安全側に倒す(fail-close)
        hook_io.finalize(
            hook_io.pre_tool_decision("ask", f"bash_guard の判定に失敗したため確認してください: {exc}"),
            cfg_all,
        )
        return
    out = hook_io.pre_tool_decision(verdict["decision"], verdict["reason"]) if verdict else None
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_bash_guard.py -v`
Expected: 全passed(DENY 12 + ASK 8 + SAFE 6 + BYPASS 5 + 個別3)

- [ ] **Step 5: コミット**

```bash
git add hooks/pre_tool_use/bash_guard.py rules/bash_deny.json rules/bash_ask.json tests/test_bash_guard.py
git commit -m "feat: bash_guard(破壊的コマンドの2段階ガード、連結・クォートバイパス対策付き)を追加"
```

---

### Task 5: secrets_guard(機密ファイルアクセス遮断)

**Files:**
- Create: `hooks/pre_tool_use/secrets_guard.py`
- Create: `rules/sensitive_paths.json`
- Test: `tests/test_secrets_guard.py`

**Interfaces:**
- Consumes: Task 1-3 の lib
- Produces: `secrets_guard.check_path(path_str: str, cfg: dict) -> str | None`(保護に該当したパターン名、なければ None)/ `secrets_guard.evaluate(event: dict, cfg: dict) -> dict | None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_secrets_guard.py`:

```python
import pytest

from helpers import load_hook

secrets_guard = load_hook("pre_tool_use/secrets_guard.py")

CFG = {"enabled": True, "protected_paths": [], "allow_paths": []}


def _event(tool, **tool_input):
    return {"tool_name": tool, "tool_input": tool_input}


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
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_secrets_guard.py -v`
Expected: FAIL

- [ ] **Step 3: ルールJSONと secrets_guard.py を実装**

`rules/sensitive_paths.json`:

```json
{
  "protected": [
    ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "*.p12", "*.pfx", "*.keystore", ".credentials.json", "credentials",
    ".netrc", ".npmrc", ".pypirc", "secrets.*"
  ],
  "protected_dirs": ["~/.ssh", "~/.aws", "~/.gnupg"],
  "allow": [".env.example", ".env.sample", ".env.template", "*.pub"]
}
```

`hooks/pre_tool_use/secrets_guard.py`:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""機密ファイル(.env・秘密鍵・認証情報)への読取・編集・catを遮断する。"""
import fnmatch
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402

FILE_TOOLS = ("Read", "Edit", "Write")


def check_path(path_str: str, cfg: dict) -> str | None:
    rules = patterns.load_rules("sensitive_paths.json")
    protected = rules["protected"] + cfg.get("protected_paths", [])
    allow = rules["allow"] + cfg.get("allow_paths", [])
    p = os.path.expanduser(path_str)
    name = os.path.basename(p.rstrip("/"))
    for pat in allow:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(path_str, pat):
            return None
    for pat in protected:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(path_str, pat):
            return pat
    for d in rules["protected_dirs"]:
        d_exp = os.path.expanduser(d)
        if p == d_exp or p.startswith(d_exp + os.sep):
            return d
    return None


def evaluate(event: dict, cfg: dict) -> dict | None:
    tool = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    hit = None
    target = ""
    if tool in FILE_TOOLS:
        target = tool_input.get("file_path", "")
        hit = check_path(target, cfg) if target else None
    elif tool == "Bash":
        command = tool_input.get("command", "")
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        for tok in tokens:
            hit = check_path(tok, cfg)
            if hit:
                target = tok
                break
    if hit:
        return {
            "decision": "deny",
            "reason": f"機密ファイルへのアクセスを遮断: {target}(該当ルール: {hit})",
        }
    return None
```

> **実装時変更(D13・ユーザー承認済み)**: 上記の「全トークン検査」はレビュー指摘により「パス形式トークンのみ検査」へ変更した(`_looks_like_path`: globメタ文字含みは除外、`/`含み・`.`/`~`始まり・`.`含みのみ対象)。`grep credentials` 等の検索コマンドの過剰denyを防ぐため。

```python


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") not in FILE_TOOLS + ("Bash",):
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("secrets_guard", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    try:
        verdict = evaluate(event, cfg)
    except Exception as exc:  # fail-close(ask)
        hook_io.finalize(
            hook_io.pre_tool_decision("ask", f"secrets_guard の判定に失敗したため確認してください: {exc}"),
            cfg_all,
        )
        return
    out = hook_io.pre_tool_decision(verdict["decision"], verdict["reason"]) if verdict else None
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_secrets_guard.py -v`
Expected: 全passed

- [ ] **Step 5: コミット**

```bash
git add hooks/pre_tool_use/secrets_guard.py rules/sensitive_paths.json tests/test_secrets_guard.py
git commit -m "feat: secrets_guard(機密ファイルアクセス遮断)を追加"
```

---

### Task 6: exfil_guard(正規表現カテゴリ・モード・trusted_servers)

**Files:**
- Create: `hooks/pre_tool_use/exfil_guard.py`
- Test: `tests/test_exfil_guard.py`

**Interfaces:**
- Consumes: Task 1-3 の lib
- Produces: `exfil_guard.is_target(tool_name: str) -> bool` / `exfil_guard.server_prefix(tool_name: str) -> str`(例 `mcp__kb__search` → `mcp__kb`)/ `exfil_guard.evaluate(payload_text: str, cfg: dict) -> dict | None` / `exfil_guard.main()`。semantic判定は Task 7 で追加(このタスクでは `semantic_check(payload_text, cfg) -> dict | None` を「常に None を返すスタブ」として定義しておく)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_exfil_guard.py`:

```python
import io
import json

import pytest

from helpers import load_hook
from lib import config

exfil_guard = load_hook("pre_tool_use/exfil_guard.py")


def _cfg(**over):
    import copy
    cfg = copy.deepcopy(config.DEFAULTS["exfil_guard"])
    for k, v in over.items():
        if isinstance(v, dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    return cfg


def test_is_target():
    assert exfil_guard.is_target("mcp__github__create_issue")
    assert exfil_guard.is_target("WebFetch")
    assert exfil_guard.is_target("WebSearch")
    assert not exfil_guard.is_target("Bash")
    assert not exfil_guard.is_target("Edit")


def test_server_prefix():
    assert exfil_guard.server_prefix("mcp__internal-kb__search") == "mcp__internal-kb"


def test_credentials_default_deny():
    v = exfil_guard.evaluate("query with AKIAIOSFODNN7EXAMPLE", _cfg())
    assert v["decision"] == "deny"
    assert "AKIAIOSFODNN7EXAMPLE" not in v["reason"]  # 値そのものは理由に出さない


def test_pii_default_ask():
    v = exfil_guard.evaluate("連絡先は taro@example.co.jp です", _cfg())
    assert v["decision"] == "ask"


def test_confidential_marker_ask():
    v = exfil_guard.evaluate("この資料は社外秘です", _cfg())
    assert v["decision"] == "ask"


def test_custom_pattern():
    cfg = _cfg(custom_patterns=[{"name": "internal-domain", "regex": "[\\w.-]+\\.corp\\.example\\.jp"}])
    v = exfil_guard.evaluate("http://wiki.corp.example.jp/page", cfg)
    assert v["decision"] == "ask"
    assert "internal-domain" in v["reason"]


def test_category_off_disables():
    cfg = _cfg(categories={"pii": "off"})
    assert exfil_guard.evaluate("taro@example.co.jp", cfg) is None


def test_clean_payload_passes():
    assert exfil_guard.evaluate("普通の検索クエリ python asyncio", _cfg()) is None


def _run_main(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        exfil_guard.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_main_always_mode_asks_everything(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8"
    )
    event = {
        "tool_name": "mcp__foo__bar",
        "cwd": str(tmp_path),
        "tool_input": {"q": "安全な内容"},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_main_trusted_server_skipped_even_in_always(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always", "trusted_servers": ["mcp__foo"]}}),
        encoding="utf-8",
    )
    event = {
        "tool_name": "mcp__foo__bar",
        "cwd": str(tmp_path),
        "tool_input": {"q": "AKIAIOSFODNN7EXAMPLE"},
    }
    assert _run_main(monkeypatch, event, capsys) is None
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_exfil_guard.py -v`
Expected: FAIL

- [ ] **Step 3: exfil_guard.py を実装**

`hooks/pre_tool_use/exfil_guard.py`:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""外部送信ガード: MCP/WebFetch/WebSearch への引数に含まれる機微情報を検査する。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402

BUILTIN_TARGETS = ("WebFetch", "WebSearch")


def is_target(tool_name: str) -> bool:
    return tool_name.startswith("mcp__") or tool_name in BUILTIN_TARGETS


def server_prefix(tool_name: str) -> str:
    parts = tool_name.split("__")
    return "__".join(parts[:2]) if len(parts) >= 2 else tool_name


def _mask(value: str) -> str:
    return value[:4] + "…"


def evaluate(payload_text: str, cfg: dict) -> dict | None:
    cats = cfg.get("categories", {})
    findings: list[dict] = []

    def add(category: str, items: list) -> None:
        action = cats.get(category, "ask")
        if action == "off":
            return
        for f in items:
            findings.append({"category": category, "action": action, "rule": f["rule"]})

    add("credentials", patterns.scan_text(payload_text, patterns.load_rules("secret_patterns.json")))
    add("pii", patterns.scan_text(payload_text, patterns.load_rules("pii_patterns.json")))
    lowered = payload_text.lower()
    markers = [
        m for m in patterns.load_rules("confidential_markers.json")["markers"]
        if m.lower() in lowered
    ]
    add("confidential_markers", [{"rule": m, "match": m} for m in markers])
    add("custom", patterns.scan_text(
        payload_text,
        [{"name": p["name"], "regex": p["regex"]} for p in cfg.get("custom_patterns", [])],
    ))
    if not findings:
        return None
    decision = "deny" if any(f["action"] == "deny" for f in findings) else "ask"
    detail = ", ".join(f"{f['category']}:{f['rule']}" for f in findings[:5])
    return {
        "decision": decision,
        "reason": f"外部送信ペイロードに機微情報の可能性を検出: {detail}",
    }


def semantic_check(payload_text: str, cfg: dict) -> dict | None:
    """LLMによる意味的判定(Task 7 で実装)。"""
    return None


def main() -> None:
    event = hook_io.read_event()
    tool = event.get("tool_name", "")
    if not is_target(tool):
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("exfil_guard", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    if tool.startswith("mcp__") and server_prefix(tool) in cfg.get("trusted_servers", []):
        hook_io.finalize(None, cfg_all)
    payload_text = json.dumps(event.get("tool_input") or {}, ensure_ascii=False)
    try:
        if cfg.get("mode") == "always":
            verdict = {
                "decision": "ask",
                "reason": f"外部送信({tool})を検出しました(mode=always)。送信してよいか確認してください",
            }
        else:
            verdict = evaluate(payload_text, cfg)
            if verdict is None and cfg.get("categories", {}).get("semantic", "ask") != "off":
                s = semantic_check(payload_text, cfg)
                if s:
                    verdict = {
                        "decision": "ask",
                        "reason": f"LLM判定: 機微情報を含む可能性 — {s.get('reason', '(理由なし)')}",
                    }
    except Exception as exc:  # 外部送信ガードは fail-open + 可視化
        hook_io.fail_open("exfil_guard", exc)
        return
    out = hook_io.pre_tool_decision(verdict["decision"], verdict["reason"]) if verdict else None
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_exfil_guard.py -v`
Expected: 11 passed

- [ ] **Step 5: コミット**

```bash
git add hooks/pre_tool_use/exfil_guard.py tests/test_exfil_guard.py
git commit -m "feat: exfil_guard(外部送信の正規表現DLP検査、detect/alwaysモード)を追加"
```

---

### Task 7: exfil_guard の semantic 判定(ヘッドレスClaude)

**Files:**
- Modify: `hooks/pre_tool_use/exfil_guard.py`(`semantic_check` のスタブを実装に置換)
- Create: `rules/semantic_prompt.md`
- Test: `tests/test_exfil_semantic.py`

**Interfaces:**
- Consumes: Task 6 の `exfil_guard`
- Produces: `exfil_guard.semantic_check(payload_text: str, cfg: dict) -> dict | None`(判定 `{"sensitive": bool, "reason": str}` の sensitive 時のみ dict を返す)。再帰防止の環境変数名は `SAFE_DEV_HOOKS_SEMANTIC`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_exfil_semantic.py`:

```python
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
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_exfil_semantic.py -v`
Expected: FAIL(スタブは常にNone、`exfil_guard.shutil` 属性なし)

- [ ] **Step 3: semantic_check を実装**

`rules/semantic_prompt.md`:

```markdown
あなたはDLP(情報漏洩防止)の判定器です。以下のペイロードが外部サービスへ送信されようとしています。
企業の機微情報(未公開の事業・財務・人事・給与情報、顧客情報、内部システム構成)や、
機密マーカーが付いていなくても機微と考えられる個人情報が含まれる可能性を判定してください。

必ず次の1行のJSONのみを出力してください。他の文章は出力しないでください:
{"sensitive": true または false, "reason": "判定理由を日本語で簡潔に"}

--- ペイロード ---
{payload}
```

`hooks/pre_tool_use/exfil_guard.py` の変更: import に `os`, `re`, `shutil`, `subprocess` を追加し、`semantic_check` スタブを以下に置換:

```python
SEMANTIC_ENV_GUARD = "SAFE_DEV_HOOKS_SEMANTIC"
SEMANTIC_TIMEOUT_SEC = 30
SEMANTIC_MAX_PAYLOAD = 4000


def semantic_check(payload_text: str, cfg: dict) -> dict | None:
    """ヘッドレスClaudeで機微情報の可能性を判定する。判定不能時はNone(fail-open)。"""
    sem = cfg.get("semantic", {})
    if len(payload_text) < sem.get("min_payload_chars", 200):
        return None
    if os.environ.get(SEMANTIC_ENV_GUARD) == "1":  # 再帰防止
        return None
    if shutil.which("claude") is None:
        return None
    template = (patterns.RULES_DIR / "semantic_prompt.md").read_text(encoding="utf-8")
    prompt = template.replace("{payload}", payload_text[:SEMANTIC_MAX_PAYLOAD])
    env = dict(os.environ, **{SEMANTIC_ENV_GUARD: "1"})
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", sem.get("model", "haiku")],
            capture_output=True, text=True, timeout=SEMANTIC_TIMEOUT_SEC, env=env,
        )
        if r.returncode != 0:
            return None
        m = re.search(r"\{.*\}", r.stdout, re.DOTALL)
        data = json.loads(m.group()) if m else {}
    except Exception:
        return None
    return data if data.get("sensitive") else None
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_exfil_semantic.py tests/test_exfil_guard.py -v`
Expected: 全passed(既存テストも回帰なし)

- [ ] **Step 5: コミット**

```bash
git add hooks/pre_tool_use/exfil_guard.py rules/semantic_prompt.md tests/test_exfil_semantic.py
git commit -m "feat: exfil_guard にヘッドレスClaudeによる semantic 判定を追加"
```

---

### Task 8: exfil_output_scan(MCP/Web応答の出力検査)

**Files:**
- Create: `hooks/post_tool_use/exfil_output_scan.py`
- Test: `tests/test_exfil_output_scan.py`

**Interfaces:**
- Consumes: Task 1-3 の lib、`exfil_guard` と同じ対象判定ロジック(is_target 相当は自前実装)
- Produces: `exfil_output_scan.evaluate(output_text: str, cfg: dict) -> list[dict]`(findings)/ `exfil_output_scan.build_output(findings, raw_output, cfg) -> dict | None`(warn: additionalContext / redact: updatedToolOutput)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_exfil_output_scan.py`:

```python
from helpers import load_hook

scan = load_hook("post_tool_use/exfil_output_scan.py")

CFG_WARN = {"enabled": True, "action": "warn"}
CFG_REDACT = {"enabled": True, "action": "redact"}


def test_detects_secret_in_output():
    findings = scan.evaluate("結果: AKIAIOSFODNN7EXAMPLE", CFG_WARN)
    assert any(f["rule"] == "aws-access-key" for f in findings)


def test_warn_builds_additional_context():
    findings = scan.evaluate("AKIAIOSFODNN7EXAMPLE", CFG_WARN)
    out = scan.build_output(findings, "AKIAIOSFODNN7EXAMPLE", CFG_WARN)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "aws-access-key" in ctx
    assert "AKIAIOSFODNN7EXAMPLE" not in ctx  # 値は再掲しない


def test_redact_masks_value():
    raw = "key=AKIAIOSFODNN7EXAMPLE end"
    findings = scan.evaluate(raw, CFG_REDACT)
    out = scan.build_output(findings, raw, CFG_REDACT)
    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    assert "AKIAIOSFODNN7EXAMPLE" not in updated
    assert "[REDACTED:aws-access-key]" in updated


def test_redact_falls_back_to_warn_for_non_string(capsys):
    findings = [{"rule": "aws-access-key", "match": "AKIAIOSFODNN7EXAMPLE"}]
    out = scan.build_output(findings, {"nested": "value"}, CFG_REDACT)
    assert "additionalContext" in out["hookSpecificOutput"]


def test_clean_output_returns_no_findings():
    assert scan.evaluate("普通の応答です", CFG_WARN) == []
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_exfil_output_scan.py -v`
Expected: FAIL

- [ ] **Step 3: exfil_output_scan.py を実装**

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""MCP/WebFetch/WebSearch の応答に含まれるシークレット・PIIを検出し、警告またはマスキングする。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402

BUILTIN_TARGETS = ("WebFetch", "WebSearch")


def is_target(tool_name: str) -> bool:
    return tool_name.startswith("mcp__") or tool_name in BUILTIN_TARGETS


def evaluate(output_text: str, cfg: dict) -> list:
    rules = list(patterns.load_rules("secret_patterns.json")) + list(
        patterns.load_rules("pii_patterns.json")
    )
    return patterns.scan_text(output_text, rules)


def build_output(findings: list, raw_output, cfg: dict) -> dict | None:
    if not findings:
        return None
    names = ", ".join(sorted({f["rule"] for f in findings}))
    if cfg.get("action") == "redact" and isinstance(raw_output, str):
        updated = raw_output
        for f in findings:
            updated = updated.replace(f["match"], f"[REDACTED:{f['rule']}]")
        return {
            "systemMessage": f"[safe-dev-hooks] 外部応答内の機微情報をマスキングしました: {names}",
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": updated,
            },
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"[safe-dev-hooks] この外部応答には機微情報の可能性があります({names})。"
                "この値をファイル・コミット・別の外部ツールへ転記しないでください。"
            ),
        }
    }


def main() -> None:
    event = hook_io.read_event()
    if not is_target(event.get("tool_name", "")):
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("exfil_output_scan", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    raw = event.get("tool_output", event.get("tool_response", ""))
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    try:
        findings = evaluate(text, cfg)
        out = build_output(findings, raw, cfg)
    except Exception as exc:
        hook_io.fail_open("exfil_output_scan", exc)
        return
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_exfil_output_scan.py -v`
Expected: 5 passed

- [ ] **Step 5: コミット**

```bash
git add hooks/post_tool_use/exfil_output_scan.py tests/test_exfil_output_scan.py
git commit -m "feat: exfil_output_scan(外部応答のシークレット/PII検出、warn/redact)を追加"
```

---

### Task 9: secrets_scan(書き込み内容のシークレット検出)

**Files:**
- Create: `hooks/post_tool_use/secrets_scan.py`
- Test: `tests/test_secrets_scan.py`

**Interfaces:**
- Consumes: Task 1-3 の lib
- Produces: `secrets_scan.extract_written_text(tool_input: dict) -> str`(Write: content / Edit: new_string / NotebookEdit: new_source を連結)/ `secrets_scan.main()`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_secrets_scan.py`:

```python
import io
import json

import pytest

from helpers import load_hook
from lib import config

scan = load_hook("post_tool_use/secrets_scan.py")


def test_extract_from_write():
    assert scan.extract_written_text({"content": "abc"}) == "abc"


def test_extract_from_edit():
    assert scan.extract_written_text({"old_string": "x", "new_string": "y"}) == "y"


def _run_main(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        scan.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_blocks_secret_write(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.py", "content": 'KEY = "AKIAIOSFODNN7EXAMPLE"'},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"
    assert "aws-access-key" in out["reason"]
    assert "AKIAIOSFODNN7EXAMPLE" not in out["reason"]


def test_clean_write_passes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.py", "content": "print('hello')"},
    }
    assert _run_main(monkeypatch, event, capsys) is None
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_secrets_scan.py -v`
Expected: FAIL

- [ ] **Step 3: secrets_scan.py を実装**

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Edit/Write で書き込まれた内容のシークレットを検出し、除去を促す(block)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, patterns  # noqa: E402

WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
CONTENT_KEYS = ("content", "new_string", "new_source")


def extract_written_text(tool_input: dict) -> str:
    return "\n".join(str(tool_input[k]) for k in CONTENT_KEYS if tool_input.get(k))


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") not in WRITE_TOOLS:
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("secrets_scan", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    text = extract_written_text(event.get("tool_input") or {})
    try:
        findings = patterns.scan_text(text, patterns.load_rules("secret_patterns.json"))
    except Exception as exc:
        hook_io.fail_open("secrets_scan", exc)
        return
    out = None
    if findings:
        names = ", ".join(sorted({f["rule"] for f in findings}))
        file_path = (event.get("tool_input") or {}).get("file_path", "(不明)")
        out = hook_io.post_block(
            f"書き込み内容にシークレットの可能性を検出しました({names})。"
            f"{file_path} から該当箇所を除去し、環境変数など安全な方法に置き換えてください"
        )
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_secrets_scan.py -v`
Expected: 4 passed

- [ ] **Step 5: コミット**

```bash
git add hooks/post_tool_use/secrets_scan.py tests/test_secrets_scan.py
git commit -m "feat: secrets_scan(書き込み内容のシークレット検出)を追加"
```

---

### Task 10: quality_gate(編集後の lint/format)

**Files:**
- Create: `hooks/post_tool_use/quality_gate.py`
- Test: `tests/test_quality_gate.py`

**Interfaces:**
- Consumes: Task 1-2 の lib
- Produces: `quality_gate.resolve_commands(file_path: str, cfg: dict, cwd: str) -> list[str]`(`{file}` 置換済みコマンド列)/ `quality_gate.run_checks(commands: list[str], cwd: str) -> list[str]`(失敗コマンドの出力末尾。空なら全て成功)/ `quality_gate.main()`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_quality_gate.py`:

```python
import io
import json

import pytest

from helpers import load_hook
from lib import config

qg = load_hook("post_tool_use/quality_gate.py")


def test_resolve_commands_from_config(tmp_path):
    cfg = {"commands": {"*.py": ["mylint {file}"]}}
    got = qg.resolve_commands(str(tmp_path / "app.py"), cfg, str(tmp_path))
    assert got == [f"mylint {tmp_path / 'app.py'}"]


def test_resolve_commands_no_match(tmp_path):
    cfg = {"commands": {"*.py": ["mylint {file}"]}}
    assert qg.resolve_commands(str(tmp_path / "app.md"), cfg, str(tmp_path)) == []


def test_run_checks_collects_failures(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    failures = qg.run_checks([f"python3 -m py_compile {bad}"], str(tmp_path))
    assert len(failures) == 1


def test_run_checks_passes(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1\n", encoding="utf-8")
    assert qg.run_checks([f"python3 -m py_compile {ok}"], str(tmp_path)) == []


def _run_main(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        qg.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_main_block_mode(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {"commands": {"*.py": ["python3 -m py_compile {file}"]}}}),
        encoding="utf-8",
    )
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(bad)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"


def test_main_warn_mode(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {
            "mode": "warn",
            "commands": {"*.py": ["python3 -m py_compile {file}"]},
        }}),
        encoding="utf-8",
    )
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(bad)}}
    out = _run_main(monkeypatch, event, capsys)
    assert "decision" not in out
    assert "additionalContext" in out["hookSpecificOutput"]


def test_main_skips_missing_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "tool_name": "Write", "cwd": str(tmp_path),
        "tool_input": {"file_path": str(tmp_path / "gone.py")},
    }
    assert _run_main(monkeypatch, event, capsys) is None
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_quality_gate.py -v`
Expected: FAIL

- [ ] **Step 3: quality_gate.py を実装**

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""編集されたファイルへ lint/format チェックを実行し、失敗をClaudeへフィードバックする。"""
import fnmatch
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io  # noqa: E402

WRITE_TOOLS = ("Edit", "Write")
COMMAND_TIMEOUT_SEC = 45
OUTPUT_TAIL_CHARS = 1500

# 自動検出: (globパターン, 必要な実行ファイル, 前提設定ファイル(いずれか必須), コマンド)
AUTO_DETECT = [
    ("*.py", "ruff", ("pyproject.toml", "ruff.toml", ".ruff.toml"), "ruff check {file}"),
    ("*.rs", "rustfmt", ("Cargo.toml",), "rustfmt --check {file}"),
    ("*.js|*.jsx|*.ts|*.tsx", "npx", ("package.json",), "npx --no-install eslint {file}"),
]


def resolve_commands(file_path: str, cfg: dict, cwd: str) -> list:
    name = Path(file_path).name
    commands = []
    for pattern, cmds in (cfg.get("commands") or {}).items():
        if fnmatch.fnmatch(name, pattern):
            commands += [c.replace("{file}", file_path) for c in cmds]
    if commands:
        return commands
    for patterns_str, exe, marker, cmd in AUTO_DETECT:
        if not any(fnmatch.fnmatch(name, p) for p in patterns_str.split("|")):
            continue
        if shutil.which(exe) is None:
            continue
        if marker and not (Path(cwd) / marker).is_file():
            continue
        commands.append(cmd.replace("{file}", file_path))
    return commands


def run_checks(commands: list, cwd: str) -> list:
    failures = []
    for cmd in commands:
        try:
            r = subprocess.run(
                shlex.split(cmd), cwd=cwd, capture_output=True, text=True,
                timeout=COMMAND_TIMEOUT_SEC,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            failures.append(f"$ {cmd}\n実行できませんでした: {exc}")
            continue
        if r.returncode != 0:
            tail = (r.stdout + r.stderr)[-OUTPUT_TAIL_CHARS:]
            failures.append(f"$ {cmd}\n{tail}")
    return failures


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") not in WRITE_TOOLS:
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("quality_gate", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    file_path = (event.get("tool_input") or {}).get("file_path", "")
    cwd = event.get("cwd") or "."
    if not file_path or not Path(file_path).is_file():
        hook_io.finalize(None, cfg_all)
    try:
        commands = resolve_commands(file_path, cfg, cwd)
        failures = run_checks(commands, cwd) if commands else []
    except Exception as exc:
        hook_io.fail_open("quality_gate", exc)
        return
    out = None
    if failures:
        detail = "\n\n".join(failures)
        if cfg.get("mode", "block") == "block":
            out = hook_io.post_block(
                f"品質チェックが失敗しました。修正してください:\n{detail}"
            )
        else:
            out = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": f"[safe-dev-hooks] 品質チェック警告:\n{detail}",
                }
            }
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_quality_gate.py -v`
Expected: 8 passed

- [ ] **Step 5: コミット**

```bash
git add hooks/post_tool_use/quality_gate.py tests/test_quality_gate.py
git commit -m "feat: quality_gate(編集後lint、設定+自動検出、block/warn)を追加"
```

---

### Task 11: audit_log と notify

**Files:**
- Create: `hooks/audit/audit_log.py`
- Create: `hooks/notification/notify.py`
- Test: `tests/test_audit_and_notify.py`

**Interfaces:**
- Consumes: Task 1-2 の lib
- Produces: `audit_log.main()`(`<path>/audit-YYYYMMDD.jsonl` に1行追記。書込失敗は黙って exit 0)/ `notify.main()`(設定 `command` があれば `{message}` を置換して実行、なければ `{"terminalSequence": "\u0007"}` を出力)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_audit_and_notify.py`:

```python
import io
import json

import pytest

from helpers import load_hook
from lib import config

audit = load_hook("audit/audit_log.py")
notify = load_hook("notification/notify.py")


def _run(mod, monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        mod.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_audit_appends_jsonl(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Bash",
        "cwd": str(tmp_path),
        "tool_input": {"command": "ls"},
    }
    _run(audit, monkeypatch, event, capsys)
    files = list((tmp_path / ".claude" / "logs").glob("audit-*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["tool_name"] == "Bash"
    assert record["event"] == "PreToolUse"
    assert "ts" in record


def test_audit_truncates_large_input(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"content": "x" * 5000},
    }
    _run(audit, monkeypatch, event, capsys)
    files = list((tmp_path / ".claude" / "logs").glob("audit-*.jsonl"))
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert len(record["tool_summary"]) <= 500


def test_audit_never_crashes_on_unwritable_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"audit_log": {"path": "/proc/forbidden"}}), encoding="utf-8"
    )
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(tmp_path)}
    _run(audit, monkeypatch, event, capsys)  # SystemExit(0) すれば成功


def test_notify_default_bell(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "hook_event_name": "Notification",
        "cwd": str(tmp_path),
        "notification_type": "permission_prompt",
        "message": "許可待ち",
    }
    out = _run(notify, monkeypatch, event, capsys)
    assert out["terminalSequence"] == "\u0007"


def test_notify_custom_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    marker = tmp_path / "notified.txt"
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"command": f"touch {marker}"}}), encoding="utf-8"
    )
    event = {
        "hook_event_name": "Notification",
        "cwd": str(tmp_path),
        "message": "done",
    }
    _run(notify, monkeypatch, event, capsys)
    assert marker.exists()
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_audit_and_notify.py -v`
Expected: FAIL

- [ ] **Step 3: audit_log.py と notify.py を実装**

`hooks/audit/audit_log.py`:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""全ツール実行・セッション境界をJSONLで監査記録する。失敗しても開発を止めない。"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io  # noqa: E402

SUMMARY_MAX_CHARS = 500


def main() -> None:
    event = hook_io.read_event()
    cfg = config.load_config(event.get("cwd")).get("audit_log", {})
    if not cfg.get("enabled", True):
        sys.exit(0)
    try:
        log_dir = Path(cfg.get("path", ".claude/logs"))
        if not log_dir.is_absolute():
            log_dir = Path(event.get("cwd") or ".") / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.timezone.utc)
        record = {
            "ts": now.isoformat(),
            "session_id": event.get("session_id", ""),
            "event": event.get("hook_event_name", ""),
            "tool_name": event.get("tool_name", ""),
            "tool_summary": json.dumps(
                event.get("tool_input") or {}, ensure_ascii=False
            )[:SUMMARY_MAX_CHARS],
        }
        log_file = log_dir / f"audit-{now.strftime('%Y%m%d')}.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 監査ログの失敗は開発を止めない(spec セクション8)
    sys.exit(0)


if __name__ == "__main__":
    main()
```

`hooks/notification/notify.py`:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""通知イベントをターミナルベルまたは任意コマンドでユーザーへ伝える。"""
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io  # noqa: E402


def main() -> None:
    event = hook_io.read_event()
    cfg = config.load_config(event.get("cwd")).get("notify", {})
    if not cfg.get("enabled", True):
        sys.exit(0)
    command = cfg.get("command")
    if command:
        message = event.get("message", "")
        try:
            subprocess.run(
                shlex.split(command.replace("{message}", shlex.quote(message))),
                timeout=10, capture_output=True,
            )
        except Exception:
            pass
        sys.exit(0)
    hook_io.emit({"terminalSequence": "\u0007"})
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_audit_and_notify.py -v`
Expected: 5 passed

- [ ] **Step 5: コミット**

```bash
git add hooks/audit/audit_log.py hooks/notification/notify.py tests/test_audit_and_notify.py
git commit -m "feat: audit_log(JSONL監査記録)と notify(通知)を追加"
```

---

### Task 12: プラグインパッケージングと導入スニペット

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `hooks/hooks.json`
- Create: `marketplace.json`
- Create: `examples/settings.full.json`
- Create: `examples/settings.minimal.json`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: Task 4-11 の全Hookスクリプト(パス参照)
- Produces: プラグインとして `/plugin install` 可能な構成。examples はパスの `${CLAUDE_PLUGIN_ROOT}` を `~/claude-code-hooks` に読み替えた手動導入版

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_packaging.py`:

```python
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(rel):
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def test_plugin_manifest():
    m = _load(".claude-plugin/plugin.json")
    assert m["name"] == "safe-dev-hooks"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["version"])


def test_hooks_json_references_existing_scripts():
    h = _load("hooks/hooks.json")
    for event, entries in h["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["type"] == "command"
                m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/(\S+?\.py)", hook["command"])
                assert m, hook["command"]
                assert (REPO / m.group(1)).is_file(), m.group(1)


def test_hooks_json_wires_all_events():
    h = _load("hooks/hooks.json")
    assert set(h["hooks"]) == {
        "PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "Stop", "Notification",
    }


def test_marketplace_manifest():
    m = _load("marketplace.json")
    assert m["plugins"][0]["name"] == "safe-dev-hooks"


def test_examples_are_valid_json():
    full = _load("examples/settings.full.json")
    minimal = _load("examples/settings.minimal.json")
    assert "hooks" in full and "hooks" in minimal
    # minimal は bash_guard / secrets_guard のみ
    assert set(minimal["hooks"]) == {"PreToolUse"}


def test_all_rules_json_parse_and_regex_compile():
    for path in (REPO / "rules").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else []
        for item in items:
            assert "name" in item and "regex" in item, path.name
            re.compile(item["regex"])
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_packaging.py -v`
Expected: FAIL

- [ ] **Step 3: マニフェスト類を作成**

`.claude-plugin/plugin.json`:

```json
{
  "name": "safe-dev-hooks",
  "version": "0.1.0",
  "description": "Claude Codeで安全に開発するためのHooks集(破壊的コマンド遮断・機密保護・DLP・品質ゲート・監査)",
  "author": {"name": "wwwcojp"}
}
```

`hooks/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/pre_tool_use/bash_guard.py\"", "timeout": 10}
        ]
      },
      {
        "matcher": "Read|Edit|Write|Bash",
        "hooks": [
          {"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/pre_tool_use/secrets_guard.py\"", "timeout": 10}
        ]
      },
      {
        "matcher": "mcp__.*|WebFetch|WebSearch",
        "hooks": [
          {"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/pre_tool_use/exfil_guard.py\"", "timeout": 60, "statusMessage": "外部送信ペイロードを検査中"}
        ]
      },
      {
        "matcher": "*",
        "hooks": [
          {"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/audit/audit_log.py\"", "timeout": 10, "async": true}
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/post_tool_use/quality_gate.py\"", "timeout": 90},
          {"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/post_tool_use/secrets_scan.py\"", "timeout": 10}
        ]
      },
      {
        "matcher": "mcp__.*|WebFetch|WebSearch",
        "hooks": [
          {"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/post_tool_use/exfil_output_scan.py\"", "timeout": 15}
        ]
      },
      {
        "matcher": "*",
        "hooks": [
          {"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/audit/audit_log.py\"", "timeout": 10, "async": true}
        ]
      }
    ],
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/audit/audit_log.py\"", "timeout": 10, "async": true}]}
    ],
    "SessionEnd": [
      {"hooks": [{"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/audit/audit_log.py\"", "timeout": 10, "async": true}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/audit/audit_log.py\"", "timeout": 10, "async": true}]}
    ],
    "Notification": [
      {"hooks": [{"type": "command", "command": "uv run \"${CLAUDE_PLUGIN_ROOT}/hooks/notification/notify.py\"", "timeout": 10}]}
    ]
  }
}
```

`marketplace.json`:

```json
{
  "name": "claude-code-hooks",
  "owner": {"name": "wwwcojp"},
  "plugins": [
    {
      "name": "safe-dev-hooks",
      "source": "./",
      "description": "Claude Codeで安全に開発するためのHooks集"
    }
  ]
}
```

`examples/settings.full.json`: hooks.json と同一構成で、`uv run "${CLAUDE_PLUGIN_ROOT}/...` を `uv run "$HOME/claude-code-hooks/...` に置換したもの(機械的に全エントリを変換して作る)。

`examples/settings.minimal.json`(bash_guard + secrets_guard のみ):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "uv run \"$HOME/claude-code-hooks/hooks/pre_tool_use/bash_guard.py\"", "timeout": 10}
        ]
      },
      {
        "matcher": "Read|Edit|Write|Bash",
        "hooks": [
          {"type": "command", "command": "uv run \"$HOME/claude-code-hooks/hooks/pre_tool_use/secrets_guard.py\"", "timeout": 10}
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_packaging.py -v`
Expected: 6 passed

- [ ] **Step 5: 全テスト回帰確認 + コミット**

Run: `uv run pytest -q`
Expected: 全passed

```bash
git add .claude-plugin hooks/hooks.json marketplace.json examples tests/test_packaging.py
git commit -m "feat: プラグインマニフェスト・Hook配線・手動導入スニペットを追加"
```

---

### Task 13: CI(GitHub Actions)と lint 整備

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: (ruff指摘があれば各ファイル)

- [ ] **Step 1: ワークフローを作成**

`.github/workflows/ci.yml`:

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Lint
        run: uv run ruff check hooks tests
      - name: Test
        run: uv run pytest -q
```

- [ ] **Step 2: ローカルで lint 実行と修正**

Run: `uv run ruff check hooks tests`
Expected: エラー0(指摘があれば修正。import順は `uv run ruff check --fix` で自動修正可)

- [ ] **Step 3: 全テスト実行**

Run: `uv run pytest -q`
Expected: 全passed

- [ ] **Step 4: コミット**

```bash
git add .github pyproject.toml hooks tests
git commit -m "ci: GitHub Actions(ruff + pytest)を追加"
```

---

### Task 14: ドキュメント一式

**Files:**
- Create: `README.md`(英語)
- Create: `README.ja.md`(日本語)
- Create: `docs/hooks/bash_guard.md`, `docs/hooks/secrets_guard.md`, `docs/hooks/exfil_guard.md`, `docs/hooks/exfil_output_scan.md`, `docs/hooks/quality_gate.md`, `docs/hooks/secrets_scan.md`, `docs/hooks/audit_log.md`, `docs/hooks/notify.md`
- Create: `docs/configuration.md`
- Create: `docs/security-model.md`
- Create: `docs/best-practices.md`
- Create: `CHANGELOG.md`
- Create: `CONTRIBUTING.md`

内容はすべて **スペック(`docs/superpowers/specs/2026-07-03-safe-dev-hooks-design.md`)の対応セクションを正**として書く。プレースホルダ(TBD等)禁止。

- [ ] **Step 1: README.ja.md を書く**

必須構成(各節は実装済みの実物と一致させること):

1. 概要 — 何を防ぐか(スペック セクション1の5項目)
2. クイックスタート
   - プラグイン: `/plugin marketplace add wwwcojp/claude-code-hooks` → `/plugin install safe-dev-hooks`
   - 手動: `git clone` → `examples/settings.full.json`(または minimal)の内容を `~/.claude/settings.json` にマージ
   - 前提: uv 必須。semantic判定は `claude` CLI があるときのみ動作
3. Hook一覧表 — スペック セクション3.1 の表を転記し、各行から `docs/hooks/*.md` へリンク
4. 設定 — `.claude-hooks.json` の最小例と `docs/configuration.md` へのリンク
5. 動作確認方法 — `echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | uv run hooks/pre_tool_use/bash_guard.py` で deny が返ること
6. 保証範囲 — `docs/security-model.md` へのリンクと1段落の要約(「Hooksは `disableAllHooks` で無効化できるため、悪意ある利用者への防御ではなく事故防止の仕組み」)
7. License / Contributing へのリンク

- [ ] **Step 2: README.md(英語)を書く**

README.ja.md と同一構成の英訳。冒頭に `日本語版: README.ja.md` のリンクを置く。

- [ ] **Step 3: docs/hooks/*.md を書く(8ファイル)**

各ファイル共通フォーマット:

```markdown
# <hook名>

## 目的
(1-2文)

## 対象イベント / matcher
(hooks.json の実際の値)

## 判定基準
(deny になる条件 / ask になる条件 / 何もしない条件。ルールJSONの実際のルール名を列挙)

## 設定キー
(.claude-hooks.json の該当セクションの全キーと既定値の表)

## 既知の限界
(例: bash_guard はクォート除去で過剰検知側に倒すため `echo 'rm -rf /'` も deny される、など実装上の実際の限界)
```

- [ ] **Step 4: docs/configuration.md を書く**

- 3層マージの説明(スペック 4.1)
- 全スキーマ(スペック 4.2 のJSONCを実装と突き合わせて転記)
- プリセット3種の完全な設定例: 個人用(既定のまま)/ チーム用(custom_patterns + trusted_servers 追加)/ 高セキュリティ(exfil_guard.mode=always, exfil_output_scan.action=redact)

- [ ] **Step 5: docs/security-model.md を書く**

必須内容(スペック 3.2「既知の限界」・セクション8):

- 脅威モデル: 対象は「エージェントの事故・暴走の防止」。悪意あるユーザー・悪意あるプラグインへの防御ではない
- 保証すること: deny層パターンの決定論的ブロック(permission modeに依らない)、設定でdeny層を解除できないこと
- 保証しないこと: `disableAllHooks` やHook設定削除による無効化、正規表現の網羅性、semantic判定の確率性(検出漏れあり)、文脈依存PII(人名等)の完全検出
- fail-open / fail-close の方針(スペック セクション8の転記)
- 監査ログに tool_input の先頭500文字が残ること(ログ自体に機微情報が入り得るため .gitignore 済みであること)

- [ ] **Step 6: docs/best-practices.md を書く**

調査結果のまとめ(出典リンク必須):

- 公式ドキュメント(https://code.claude.com/docs/en/hooks): permissionDecision の使い分け、exit 2 よりJSON出力、matcherで範囲を絞る、`ask` でグレーゾーンをユーザーに委ねる
- disler/claude-code-hooks-mastery: uv single-file scripts、全イベント監査ログ
- karanb192/claude-code-hooks ほか: 1 Hook = 1関心事、コピペ可能な構成
- 本リポジトリが採用した設計原則: 安全側の既定・データ駆動ルール・deny層の設定不可侵

- [ ] **Step 7: CHANGELOG.md と CONTRIBUTING.md を書く**

`CHANGELOG.md`: Keep a Changelog 形式、`## [0.1.0] - <実装完了日>` に全Hook・設定・配布の初期リリース内容を列挙。

`CONTRIBUTING.md`: ルール追加の手順(rules/*.json への追記 → tests/ に危険系・安全系のケース追加 → `uv run pytest`)、PRの前提(CI green、README/docsの該当箇所更新)。

- [ ] **Step 8: 文書と実装の突き合わせ確認**

- README のHook一覧・設定例が実物(hooks.json / config.DEFAULTS)と一致しているか
- 動作確認コマンド(README手順5)を実際に実行して deny が返ることを確認

Run: `echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | uv run hooks/pre_tool_use/bash_guard.py`
Expected: `permissionDecision: "deny"` を含むJSON

- [ ] **Step 9: コミット**

```bash
git add README.md README.ja.md docs CHANGELOG.md CONTRIBUTING.md
git commit -m "docs: README(日英)・Hookリファレンス・設定/セキュリティモデル/ベストプラクティス文書を追加"
```

---

## スペックカバレッジ(セルフレビュー用対応表)

| スペック要件 | タスク |
|--------------|--------|
| D2 uv single-file Python | 全Hookタスク(スクリプトヘッダ) |
| D3 プラグイン+スニペット両対応 | Task 12 |
| D4 設定+自動検出 | Task 2, 10 |
| D5 deny/ask 段階制御 | Task 4, 5 |
| D6 関心事別モジュール | 全体構成 |
| D7 JSON出力統一 | Task 1(hook_io) |
| D8 全MCP+Web入出力検査 | Task 6, 8 |
| D9 detect/always モード | Task 6 |
| D10 .gitignore 衛生 | 済(設計時)+ 各コミットで git status 確認 |
| D11 semantic判定(ヘッドレスClaude) | Task 7 |
| 3.1 bash_guard/secrets_guard/quality_gate/secrets_scan/audit_log/notify | Task 4, 5, 10, 9, 11 |
| 4.x 設定スキーマ・3層マージ・スキーマ検証 | Task 2 |
| 5 配布 | Task 12 |
| 6 文書化 | Task 14 |
| 7 テスト戦略・CI(バイパス試行含む) | 各タスクのテスト + Task 13 |
| 8 エラーハンドリング(fail-open/close) | Task 1, 4, 5(fail-close)、6-11(fail-open) |
