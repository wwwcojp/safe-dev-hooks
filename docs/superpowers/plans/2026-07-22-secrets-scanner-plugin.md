# 秘密検出スキャナのプラグイン化(gitleaks 委譲)実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 秘密検出を成熟OSS `gitleaks` へ任意委譲し、内蔵patterns(floor)にunion加算してカバレッジを広げる(deny保証は不変)。

**Architecture:** 秘密検出の集約点 `hooks/lib/scanners.py` を新設。`scan_secrets()` が内蔵 `patterns.scan_text`(常時floor)に gitleaks 結果を加算する。gitleaks はネイティブ/Docker のいずれかを `shutil.which` ガード下で外部プロセスとして呼び、不在・失敗はすべて fail-open(floorのみ)。3フック(exfil_guard/secrets_scan/exfil_output_scan)は集約点を呼ぶだけに変更する。

**Tech Stack:** Python 3.10+(PEP723 単一ファイルフック)、pytest、gitleaks(外部・任意)、Docker(任意)。

## Global Constraints

- **Python >= 3.10**(各フック先頭の PEP723 `requires-python = ">=3.10"` を踏襲)。
- **PEP723 依存を増やさない。** gitleaks/docker はインライン依存に加えず、`shutil.which` ガードの外部プロセスとしてのみ呼ぶ(不在なら無コスト)。
- **union であって置換ではない。** `secret_patterns.json` の内蔵検出は常に走る floor。gitleaks は上乗せのみ。gitleaks 不在/失敗でも floor は不変。
- **deny 保証を弱めない。** gitleaks 由来 finding も `exfil_guard` の `credentials`(既定deny)に入れる。deny の解除経路は追加しない(`.claude/rules/guard-rule-changes.md` 原則5)。
- **fail-open。** バックエンドの不在・タイムアウト・異常終了・パース失敗はすべて floor のみを返す。
- **テストは両方向(原則4)。** false-negative(union で拾う)と false-positive/退行(floor 不変・正当テキスト誤検出なし)の両方を書く。
- **実 gitleaks/docker に依存しない。** テストは `shutil.which` をモンキーパッチし、実行は stub 実行ファイル/モンキーパッチで注入する。
- **write_protected の回避(ドッグフーディング / `.claude/rules/dogfooding.md`)。** `hooks/` 配下(`config.py`・`scanners.py`・各フック)は Edit/Write も Bash リダイレクトも遮断される。**`.superpowers/patch_<task>.py`(gitignore配下・通常のWriteで作成可)に Python 書込スクリプトを用意し `python3 .superpowers/patch_<task>.py` で適用する**(スクリプトは対象を `read_text()`→`replace()`→`write_text()`、新規ファイルは `write_text()`)。`tests/`・`docs/`・`examples/`・`tests/conftest.py`・ルート設定ファイルは通常の Edit/Write でよい。
- **プレースホルダー規約(`.claude/rules/no-personal-paths.md`)。** 実ホームパス・実ユーザー名を書かない。
- **秘密の字面回避。** テストフィクスチャで内蔵ルールに一致する秘密を書くときは実行時連結(例 `"AKIA" + "Z" * 16`)で組み立て、ソース上に完全一致文字列を残さない(自リポジトリの `secrets_scan` に遮断されないため)。

---

### Task 1: 設定に `scanners` セクションを追加(config.py)

**Files:**
- Modify: `hooks/lib/config.py`(DEFAULTS / _ENUM_KEYS / 検証)※パッチスクリプト経由
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.DEFAULTS["scanners"] = {"gitleaks": "auto", "gitleaks_image": "ghcr.io/gitleaks/gitleaks:v8.30.1", "gitleaks_config": None}`。`load_config()` が `scanners` を検証・フォールバック付きで返す。

- [ ] **Step 1: 失敗するテストを書く**(`tests/test_config.py` に追記、通常の Edit)

```python
def test_scanners_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks"] == "auto"
    assert cfg["scanners"]["gitleaks_image"].startswith("ghcr.io/gitleaks/gitleaks:")
    assert cfg["scanners"]["gitleaks_config"] is None


def test_scanners_gitleaks_enum_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        '{"scanners": {"gitleaks": "bogus"}}', encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks"] == "auto"
    assert any("scanners.gitleaks" in e for e in cfg["_errors"])


def test_scanners_gitleaks_docker_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        '{"scanners": {"gitleaks": "docker"}}', encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks"] == "docker"


def test_scanners_config_type_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        '{"scanners": {"gitleaks_config": 123}}', encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks_config"] is None
    assert any("gitleaks_config" in e for e in cfg["_errors"])
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_config.py -k scanners -q`
Expected: FAIL(`KeyError: 'scanners'` など)

- [ ] **Step 3: config.py を編集(パッチスクリプト経由)**

`.superpowers/patch_task1.py` を Write で作成し実行する。3箇所を置換する。

置換A(DEFAULTS に scanners を追加):
- old:
```python
    "notify": {"enabled": True, "method": "auto", "command": None},
}
```
- new:
```python
    "notify": {"enabled": True, "method": "auto", "command": None},
    "scanners": {
        "gitleaks": "auto",
        "gitleaks_image": "ghcr.io/gitleaks/gitleaks:v8.30.1",
        "gitleaks_config": None,
    },
}
```

置換B(_ENUM_KEYS に gitleaks モードを追加):
- old:
```python
    ("notify", "method"): {"auto", "bell"},
}
```
- new:
```python
    ("notify", "method"): {"auto", "bell"},
    ("scanners", "gitleaks"): {"auto", "off", "docker"},
}
```

置換C(検証を追加。`cfg["_errors"] = errors` の直前に挿入):
- old:
```python
        cfg["secrets_guard"]["write_protected_paths"] = []
    cfg["_errors"] = errors
    return cfg
```
- new:
```python
        cfg["secrets_guard"]["write_protected_paths"] = []
    sc = cfg.get("scanners", {})
    if not isinstance(sc.get("gitleaks_image"), str):
        errors.append("scanners.gitleaks_image: 文字列でないため既定値を使用します")
        cfg["scanners"]["gitleaks_image"] = DEFAULTS["scanners"]["gitleaks_image"]
    gc = sc.get("gitleaks_config")
    if gc is not None and not isinstance(gc, str):
        errors.append("scanners.gitleaks_config: 文字列またはnullでないため既定値を使用します")
        cfg["scanners"]["gitleaks_config"] = None
    cfg["_errors"] = errors
    return cfg
```

パッチスクリプト例(`.superpowers/patch_task1.py`):
```python
from pathlib import Path

p = Path("hooks/lib/config.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    '    "notify": {"enabled": True, "method": "auto", "command": None},\n}',
    '    "notify": {"enabled": True, "method": "auto", "command": None},\n'
    '    "scanners": {\n'
    '        "gitleaks": "auto",\n'
    '        "gitleaks_image": "ghcr.io/gitleaks/gitleaks:v8.30.1",\n'
    '        "gitleaks_config": None,\n'
    '    },\n}',
)
s = s.replace(
    '    ("notify", "method"): {"auto", "bell"},\n}',
    '    ("notify", "method"): {"auto", "bell"},\n'
    '    ("scanners", "gitleaks"): {"auto", "off", "docker"},\n}',
)
s = s.replace(
    '        cfg["secrets_guard"]["write_protected_paths"] = []\n'
    '    cfg["_errors"] = errors\n    return cfg',
    '        cfg["secrets_guard"]["write_protected_paths"] = []\n'
    '    sc = cfg.get("scanners", {})\n'
    '    if not isinstance(sc.get("gitleaks_image"), str):\n'
    '        errors.append("scanners.gitleaks_image: 文字列でないため既定値を使用します")\n'
    '        cfg["scanners"]["gitleaks_image"] = DEFAULTS["scanners"]["gitleaks_image"]\n'
    '    gc = sc.get("gitleaks_config")\n'
    '    if gc is not None and not isinstance(gc, str):\n'
    '        errors.append("scanners.gitleaks_config: 文字列またはnullでないため既定値を使用します")\n'
    '        cfg["scanners"]["gitleaks_config"] = None\n'
    '    cfg["_errors"] = errors\n    return cfg',
)
p.write_text(s, encoding="utf-8")
print("patched config.py")
```
Run: `python3 .superpowers/patch_task1.py`

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS(既存 config テストを含め全通過)

- [ ] **Step 5: コミット**

```bash
git add hooks/lib/config.py tests/test_config.py
git commit -m "feat(config): scanners セクション(gitleaks モード/イメージ/設定パス)を追加"
```

---

### Task 2: `hooks/lib/scanners.py`(argv 組み立て・実行・scan_secrets)

**Files:**
- Create: `hooks/lib/scanners.py` ※パッチスクリプト(新規 write_text)経由
- Modify: `tests/conftest.py`(autouse フィクスチャ、通常の Edit)
- Test: `tests/test_scanners.py`(通常の Write)

**Interfaces:**
- Consumes: `lib.patterns.scan_text` / `lib.patterns.load_rules`(Task なしで既存)。
- Produces:
  - `scanners.scan_secrets(text: str, scanners_cfg: dict | None = None, cwd: str | None = None) -> list[{"rule": str, "match": str}]`
  - `scanners._gitleaks_argv(sc: dict, cwd: str | None) -> list | None`
  - `scanners._run_gitleaks(argv: list, text: str) -> list[{"rule","match"}]`
  - 定数 `scanners.DEFAULT_IMAGE`, `scanners.GITLEAKS_TIMEOUT_SEC`

- [ ] **Step 1: autouse フィクスチャを追加(`tests/conftest.py`、通常の Edit)**

`tests/conftest.py` の末尾に追記:
```python
import shutil

import pytest


@pytest.fixture(autouse=True)
def _hide_external_secret_scanners(monkeypatch):
    """実 gitleaks/docker へ出ずテストを決定論化する。gitleaks を検証するテストは
    shutil.which / scanners._run_gitleaks を再度上書きしてスタブを指す。"""
    real_which = shutil.which

    def fake_which(name, *args, **kwargs):
        if name in ("gitleaks", "docker"):
            return None
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", fake_which)
```

- [ ] **Step 2: 失敗するテストを書く(`tests/test_scanners.py` を Write)**

```python
import json

from lib import scanners


# --- _gitleaks_argv(純関数) ---

def test_argv_off_returns_none():
    assert scanners._gitleaks_argv({"gitleaks": "off"}, None) is None


def test_argv_auto_present(monkeypatch):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/usr/bin/gitleaks" if n == "gitleaks" else None)
    argv = scanners._gitleaks_argv({"gitleaks": "auto"}, None)
    assert argv[0] == "gitleaks"
    assert "stdin" in argv and "--report-format" in argv


def test_argv_auto_absent(monkeypatch):
    monkeypatch.setattr(scanners.shutil, "which", lambda n, *a, **k: None)
    assert scanners._gitleaks_argv({"gitleaks": "auto"}, None) is None


def test_argv_docker_present(monkeypatch):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/usr/bin/docker" if n == "docker" else None)
    argv = scanners._gitleaks_argv({"gitleaks": "docker", "gitleaks_image": "img:1"}, None)
    assert argv[:4] == ["docker", "run", "--rm", "-i"]
    assert "img:1" in argv


def test_argv_docker_absent(monkeypatch):
    monkeypatch.setattr(scanners.shutil, "which", lambda n, *a, **k: None)
    assert scanners._gitleaks_argv({"gitleaks": "docker"}, None) is None


def test_argv_explicit_config(monkeypatch, tmp_path):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/x" if n == "gitleaks" else None)
    p = tmp_path / "gl.toml"
    p.write_text("", encoding="utf-8")
    argv = scanners._gitleaks_argv({"gitleaks": "auto", "gitleaks_config": str(p)}, None)
    assert "-c" in argv and str(p) in argv


def test_argv_autodetect_project_config(monkeypatch, tmp_path):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/x" if n == "gitleaks" else None)
    (tmp_path / ".gitleaks.toml").write_text("", encoding="utf-8")
    argv = scanners._gitleaks_argv({"gitleaks": "auto"}, str(tmp_path))
    assert "-c" in argv


def test_argv_no_config_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/x" if n == "gitleaks" else None)
    argv = scanners._gitleaks_argv({"gitleaks": "auto"}, str(tmp_path))
    assert "-c" not in argv


def test_argv_docker_config_mount(monkeypatch, tmp_path):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/x" if n == "docker" else None)
    p = tmp_path / "gl.toml"
    p.write_text("", encoding="utf-8")
    argv = scanners._gitleaks_argv({"gitleaks": "docker", "gitleaks_config": str(p)}, None)
    assert "-v" in argv
    assert any(a.endswith(":/tmp/gl.toml:ro") for a in argv)
    assert "-c" in argv and "/tmp/gl.toml" in argv


# --- _run_gitleaks(stub 実行ファイル) ---

def _make_stub(tmp_path, stdout, code):
    stub = tmp_path / "stub_gitleaks.py"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.exit({code})\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return str(stub)


def test_run_gitleaks_parses_findings(tmp_path):
    payload = json.dumps([{"RuleID": "generic-api-key", "Secret": "STUB-LEAK-VALUE"}])
    stub = _make_stub(tmp_path, payload, 1)
    out = scanners._run_gitleaks([stub], "irrelevant")
    assert out == [{"rule": "gitleaks:generic-api-key", "match": "STUB-LEAK-VALUE"}]


def test_run_gitleaks_zero_exit_no_findings(tmp_path):
    stub = _make_stub(tmp_path, "[]", 0)
    assert scanners._run_gitleaks([stub], "x") == []


def test_run_gitleaks_error_exit_fail_open(tmp_path):
    stub = _make_stub(tmp_path, "garbage", 2)
    assert scanners._run_gitleaks([stub], "x") == []


def test_run_gitleaks_bad_json_fail_open(tmp_path):
    stub = _make_stub(tmp_path, "not json", 1)
    assert scanners._run_gitleaks([stub], "x") == []


# --- scan_secrets(union / floor 不変 / dedup) ---

def test_scan_secrets_off_floor_only():
    akia = "AKIA" + "Z" * 16
    out = scanners.scan_secrets(f"key={akia}", {"gitleaks": "off"}, None)
    assert any(f["rule"] == "aws-access-key" for f in out)
    assert all(not f["rule"].startswith("gitleaks:") for f in out)


def test_scan_secrets_union_with_gitleaks(monkeypatch):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/x" if n == "gitleaks" else None)
    monkeypatch.setattr(scanners, "_run_gitleaks",
                        lambda argv, text: [{"rule": "gitleaks:generic", "match": "STUB"}])
    akia = "AKIA" + "Z" * 16
    out = scanners.scan_secrets(f"key={akia}", {"gitleaks": "auto"}, None)
    rules = {f["rule"] for f in out}
    assert "aws-access-key" in rules
    assert "gitleaks:generic" in rules


def test_scan_secrets_floor_invariant_when_gitleaks_absent(monkeypatch):
    monkeypatch.setattr(scanners.shutil, "which", lambda n, *a, **k: None)
    akia = "AKIA" + "Z" * 16
    out = scanners.scan_secrets(f"key={akia}", {"gitleaks": "auto"}, None)
    assert [f["rule"] for f in out] == ["aws-access-key"]


def test_scan_secrets_dedup(monkeypatch):
    akia = "AKIA" + "Z" * 16
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/x" if n == "gitleaks" else None)
    monkeypatch.setattr(scanners, "_run_gitleaks",
                        lambda argv, text: [{"rule": "aws-access-key", "match": akia}])
    out = scanners.scan_secrets(f"key={akia}", {"gitleaks": "auto"}, None)
    assert sum(1 for f in out if f["match"] == akia) == 1
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `uv run pytest tests/test_scanners.py -q`
Expected: FAIL(`ModuleNotFoundError: No module named 'lib.scanners'` 等)

- [ ] **Step 4: scanners.py を作成(パッチスクリプト経由)**

`.superpowers/patch_task2.py` を Write で作成し実行する。内容は次の全文を `hooks/lib/scanners.py` へ `write_text`:
```python
"""秘密検出バックエンドの集約。内蔵patterns(floor)にgitleaksをunion加算する。

gitleaks は「あれば使う」任意バックエンド。内蔵検出は常に走る floor であり、
gitleaks の結果は上に加算されるのみ(不在・失敗時も floor は不変=deny 保証を弱めない)。
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import patterns

GITLEAKS_TIMEOUT_SEC = 15
DEFAULT_IMAGE = "ghcr.io/gitleaks/gitleaks:v8.30.1"
_COMMON_FLAGS = [
    "stdin", "--report-format", "json", "--report-path", "-",
    "--no-banner", "-l", "error",
]


def _resolve_config_path(sc: dict, cwd: str | None) -> str | None:
    explicit = sc.get("gitleaks_config")
    if explicit:
        return explicit
    if cwd:
        candidate = Path(cwd) / ".gitleaks.toml"
        if candidate.is_file():
            return str(candidate)
    return None


def _gitleaks_argv(sc: dict, cwd: str | None) -> list | None:
    mode = sc.get("gitleaks", "auto")
    if mode == "off":
        return None
    cfg_path = _resolve_config_path(sc, cwd)
    if mode == "auto":
        if shutil.which("gitleaks") is None:
            return None
        argv = ["gitleaks", *_COMMON_FLAGS]
        if cfg_path:
            argv += ["-c", cfg_path]
        return argv
    if mode == "docker":
        if shutil.which("docker") is None:
            return None
        image = sc.get("gitleaks_image") or DEFAULT_IMAGE
        argv = ["docker", "run", "--rm", "-i"]
        tail: list = []
        if cfg_path:
            argv += ["-v", f"{os.path.abspath(cfg_path)}:/tmp/gl.toml:ro"]
            tail = ["-c", "/tmp/gl.toml"]
        argv += [image, *_COMMON_FLAGS, *tail]
        return argv
    return None


def _run_gitleaks(argv: list, text: str) -> list:
    try:
        r = subprocess.run(
            argv, input=text, capture_output=True, text=True,
            timeout=GITLEAKS_TIMEOUT_SEC,
        )
    except Exception:
        return []
    if r.returncode not in (0, 1):
        return []
    if r.returncode == 0:
        return []
    try:
        data = json.loads(r.stdout)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for f in data:
        if not isinstance(f, dict):
            continue
        rule_id = f.get("RuleID")
        secret = f.get("Secret")
        if rule_id and secret:
            out.append({"rule": f"gitleaks:{rule_id}", "match": secret})
    return out


def scan_secrets(text: str, scanners_cfg: dict | None = None,
                 cwd: str | None = None) -> list:
    # 内蔵 floor は常に走る(例外は呼び出し側の fail_open に委ねるため try で囲まない)
    findings = patterns.scan_text(text, patterns.load_rules("secret_patterns.json"))
    sc = scanners_cfg or {}
    argv = _gitleaks_argv(sc, cwd)
    if argv is not None:
        findings = findings + _run_gitleaks(argv, text)
    seen = set()
    deduped = []
    for f in findings:
        key = (f["rule"], f["match"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return deduped
```
Run: `python3 .superpowers/patch_task2.py`

- [ ] **Step 5: テストが通ることを確認**

Run: `uv run pytest tests/test_scanners.py -q`
Expected: PASS(全 scanner テスト通過)

- [ ] **Step 6: 全体スモーク(既存テストの決定論を確認)**

Run: `uv run pytest -q`
Expected: PASS(autouse フィクスチャにより既存フックテストは実 gitleaks を呼ばず不変)

- [ ] **Step 7: コミット**

```bash
git add hooks/lib/scanners.py tests/test_scanners.py tests/conftest.py
git commit -m "feat(scanners): gitleaks委譲バックエンド(union/fail-open)を追加"
```

---

### Task 3: exfil_guard を scanners へ配線(deny 保証)

**Files:**
- Modify: `hooks/pre_tool_use/exfil_guard.py` ※パッチスクリプト経由
- Test: `tests/test_exfil_guard.py`(通常の Edit)

**Interfaces:**
- Consumes: `scanners.scan_secrets(text, scanners_cfg, cwd)`(Task 2)。
- Produces: `evaluate(payload_text, cfg, scanners_cfg=None, cwd=None)`。gitleaks 由来 finding は `credentials`(既定 deny)へ入る。

- [ ] **Step 1: 失敗するテストを書く(`tests/test_exfil_guard.py` に追記)**

```python
def test_gitleaks_finding_denies(monkeypatch):
    monkeypatch.setattr(
        exfil_guard.scanners, "scan_secrets",
        lambda text, sc, cwd: [{"rule": "gitleaks:x", "match": "STUB"}],
    )
    v = exfil_guard.evaluate("clean text", _cfg(), {"gitleaks": "auto"}, None)
    assert v["decision"] == "deny"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_exfil_guard.py::test_gitleaks_finding_denies -q`
Expected: FAIL(`AttributeError: module ... has no attribute 'scanners'` または引数不一致)

- [ ] **Step 3: exfil_guard.py を編集(パッチスクリプト経由 `.superpowers/patch_task3.py`)**

置換A(import):
- old: `from lib import config, hook_io, patterns  # noqa: E402`
- new: `from lib import config, hook_io, patterns, scanners  # noqa: E402`

置換B(evaluate シグネチャ):
- old: `def evaluate(payload_text: str, cfg: dict) -> dict | None:`
- new: `def evaluate(payload_text: str, cfg: dict, scanners_cfg: dict | None = None, cwd: str | None = None) -> dict | None:`

置換C(credentials スキャンを scan_secrets へ):
- old:
```python
    add(
        "credentials",
        patterns.scan_text(payload_text, patterns.load_rules("secret_patterns.json")),
    )
```
- new:
```python
    add(
        "credentials",
        scanners.scan_secrets(payload_text, scanners_cfg, cwd),
    )
```

置換D(main の呼び出し2箇所、`replace` は全置換):
- old: `verdict = evaluate(payload_text, cfg)`
- new: `verdict = evaluate(payload_text, cfg, cfg_all.get("scanners"), event.get("cwd"))`

パッチスクリプト例:
```python
from pathlib import Path

p = Path("hooks/pre_tool_use/exfil_guard.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    "from lib import config, hook_io, patterns  # noqa: E402",
    "from lib import config, hook_io, patterns, scanners  # noqa: E402",
)
s = s.replace(
    "def evaluate(payload_text: str, cfg: dict) -> dict | None:",
    "def evaluate(payload_text: str, cfg: dict, scanners_cfg: dict | None = None, "
    "cwd: str | None = None) -> dict | None:",
)
s = s.replace(
    '    add(\n        "credentials",\n'
    '        patterns.scan_text(payload_text, patterns.load_rules("secret_patterns.json")),\n'
    '    )',
    '    add(\n        "credentials",\n'
    '        scanners.scan_secrets(payload_text, scanners_cfg, cwd),\n'
    '    )',
)
s = s.replace(
    "verdict = evaluate(payload_text, cfg)",
    "verdict = evaluate(payload_text, cfg, cfg_all.get(\"scanners\"), event.get(\"cwd\"))",
)
p.write_text(s, encoding="utf-8")
print("patched exfil_guard.py")
```
Run: `python3 .superpowers/patch_task3.py`

- [ ] **Step 4: テストが通ることを確認(新規 + 既存の exfil 一式)**

Run: `uv run pytest tests/test_exfil_guard.py tests/test_exfil_semantic.py -q`
Expected: PASS(既存の `test_credentials_default_deny` 等は内蔵 floor で不変)

- [ ] **Step 5: コミット**

```bash
git add hooks/pre_tool_use/exfil_guard.py tests/test_exfil_guard.py
git commit -m "feat(exfil_guard): credentials検出をscan_secrets(union)へ配線"
```

---

### Task 4: secrets_scan を scanners へ配線(custom と union)

**Files:**
- Modify: `hooks/post_tool_use/secrets_scan.py` ※パッチスクリプト経由
- Test: `tests/test_secrets_scan.py`(通常の Edit)

**Interfaces:**
- Consumes: `scanners.scan_secrets(text, scanners_cfg, cwd)`。
- Produces: 書込内容の秘密検出が `scan_secrets`(内蔵∪gitleaks)+ `custom_patterns` の union に基づく。

- [ ] **Step 1: 失敗するテストを書く(`tests/test_secrets_scan.py` に追記)**

```python
def test_gitleaks_finding_in_block(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(
        scan.scanners, "scan_secrets",
        lambda text, sc, cwd: [{"rule": "gitleaks:generic", "match": "STUB"}],
    )
    event = {
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "a.py", "content": "hello world"},
    }
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"
    assert "gitleaks:generic" in out["reason"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_secrets_scan.py::test_gitleaks_finding_in_block -q`
Expected: FAIL(`AttributeError: ... 'scanners'`)

- [ ] **Step 3: secrets_scan.py を編集(パッチスクリプト経由 `.superpowers/patch_task4.py`)**

置換A(import):
- old: `from lib import config, hook_io, patterns  # noqa: E402`
- new: `from lib import config, hook_io, patterns, scanners  # noqa: E402`

置換B(スキャン本体):
- old:
```python
    try:
        rules = list(patterns.load_rules("secret_patterns.json")) + [
            {"name": p["name"], "regex": p["regex"]}
            for p in cfg.get("custom_patterns", [])
        ]
        findings = patterns.scan_text(text, rules)
    except Exception as exc:
        hook_io.fail_open("secrets_scan", exc)
        return
```
- new:
```python
    try:
        custom_rules = [
            {"name": p["name"], "regex": p["regex"]}
            for p in cfg.get("custom_patterns", [])
        ]
        findings = scanners.scan_secrets(
            text, cfg_all.get("scanners"), event.get("cwd")
        ) + patterns.scan_text(text, custom_rules)
    except Exception as exc:
        hook_io.fail_open("secrets_scan", exc)
        return
```

パッチスクリプト例:
```python
from pathlib import Path

p = Path("hooks/post_tool_use/secrets_scan.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    "from lib import config, hook_io, patterns  # noqa: E402",
    "from lib import config, hook_io, patterns, scanners  # noqa: E402",
)
s = s.replace(
    '    try:\n'
    '        rules = list(patterns.load_rules("secret_patterns.json")) + [\n'
    '            {"name": p["name"], "regex": p["regex"]}\n'
    '            for p in cfg.get("custom_patterns", [])\n'
    '        ]\n'
    '        findings = patterns.scan_text(text, rules)\n'
    '    except Exception as exc:\n'
    '        hook_io.fail_open("secrets_scan", exc)\n'
    '        return',
    '    try:\n'
    '        custom_rules = [\n'
    '            {"name": p["name"], "regex": p["regex"]}\n'
    '            for p in cfg.get("custom_patterns", [])\n'
    '        ]\n'
    '        findings = scanners.scan_secrets(\n'
    '            text, cfg_all.get("scanners"), event.get("cwd")\n'
    '        ) + patterns.scan_text(text, custom_rules)\n'
    '    except Exception as exc:\n'
    '        hook_io.fail_open("secrets_scan", exc)\n'
    '        return',
)
p.write_text(s, encoding="utf-8")
print("patched secrets_scan.py")
```
Run: `python3 .superpowers/patch_task4.py`

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_secrets_scan.py -q`
Expected: PASS(既存 `test_blocks_secret_write`/`test_clean_write_passes` は内蔵 floor で不変)

- [ ] **Step 5: コミット**

```bash
git add hooks/post_tool_use/secrets_scan.py tests/test_secrets_scan.py
git commit -m "feat(secrets_scan): 書込検出をscan_secrets(union)へ配線"
```

---

### Task 5: exfil_output_scan を scanners へ配線(redact 両立)

**Files:**
- Modify: `hooks/post_tool_use/exfil_output_scan.py` ※パッチスクリプト経由
- Test: `tests/test_exfil_output_scan.py`(通常の Edit)

**Interfaces:**
- Consumes: `scanners.scan_secrets(text, scanners_cfg, cwd)`。
- Produces: `evaluate(output_text, cfg, scanners_cfg=None, cwd=None)`。secret は scan_secrets(内蔵∪gitleaks)、pii は内蔵据え置き。`match` は `Secret` を含むため `action:"redact"` と両立。

- [ ] **Step 1: 失敗するテストを書く(`tests/test_exfil_output_scan.py` に追記)**

```python
def test_gitleaks_finding_redacted(monkeypatch):
    monkeypatch.setattr(
        scan.scanners, "scan_secrets",
        lambda text, sc, cwd: [{"rule": "gitleaks:x", "match": "STUBSECRET"}],
    )
    raw = "leak=STUBSECRET end"
    findings = scan.evaluate(raw, CFG_REDACT, {"gitleaks": "auto"}, None)
    out = scan.build_output(findings, raw, CFG_REDACT)
    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    assert "STUBSECRET" not in updated
    assert "[REDACTED:gitleaks:x]" in updated
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_exfil_output_scan.py::test_gitleaks_finding_redacted -q`
Expected: FAIL(`AttributeError: ... 'scanners'` または引数不一致)

- [ ] **Step 3: exfil_output_scan.py を編集(パッチスクリプト経由 `.superpowers/patch_task5.py`)**

置換A(import):
- old: `from lib import config, hook_io, patterns  # noqa: E402`
- new: `from lib import config, hook_io, patterns, scanners  # noqa: E402`

置換B(evaluate シグネチャと本体):
- old:
```python
def evaluate(output_text: str, cfg: dict) -> list:
    rules = list(patterns.load_rules("secret_patterns.json")) + list(
        patterns.load_rules("pii_patterns.json")
    )
    return patterns.scan_text(output_text, rules)
```
- new:
```python
def evaluate(output_text: str, cfg: dict, scanners_cfg: dict | None = None,
             cwd: str | None = None) -> list:
    secrets = scanners.scan_secrets(output_text, scanners_cfg, cwd)
    pii = patterns.scan_text(output_text, patterns.load_rules("pii_patterns.json"))
    return secrets + pii
```

置換C(main の呼び出し):
- old: `        findings = evaluate(text, cfg)`
- new: `        findings = evaluate(text, cfg, cfg_all.get("scanners"), event.get("cwd"))`

パッチスクリプト例:
```python
from pathlib import Path

p = Path("hooks/post_tool_use/exfil_output_scan.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    "from lib import config, hook_io, patterns  # noqa: E402",
    "from lib import config, hook_io, patterns, scanners  # noqa: E402",
)
s = s.replace(
    'def evaluate(output_text: str, cfg: dict) -> list:\n'
    '    rules = list(patterns.load_rules("secret_patterns.json")) + list(\n'
    '        patterns.load_rules("pii_patterns.json")\n'
    '    )\n'
    '    return patterns.scan_text(output_text, rules)',
    'def evaluate(output_text: str, cfg: dict, scanners_cfg: dict | None = None,\n'
    '             cwd: str | None = None) -> list:\n'
    '    secrets = scanners.scan_secrets(output_text, scanners_cfg, cwd)\n'
    '    pii = patterns.scan_text(output_text, patterns.load_rules("pii_patterns.json"))\n'
    '    return secrets + pii',
)
s = s.replace(
    "        findings = evaluate(text, cfg)",
    "        findings = evaluate(text, cfg, cfg_all.get(\"scanners\"), event.get(\"cwd\"))",
)
p.write_text(s, encoding="utf-8")
print("patched exfil_output_scan.py")
```
Run: `python3 .superpowers/patch_task5.py`

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_exfil_output_scan.py -q`
Expected: PASS(既存 `test_detects_secret_in_output`/`test_redact_masks_value` は内蔵 floor で不変)

- [ ] **Step 5: 全体テスト + Lint**

Run: `uv run pytest -q && uv run ruff check hooks tests`
Expected: PASS(全通過・Lint クリーン)

- [ ] **Step 6: コミット**

```bash
git add hooks/post_tool_use/exfil_output_scan.py tests/test_exfil_output_scan.py
git commit -m "feat(exfil_output_scan): secret検出をscan_secrets(union)へ配線"
```

---

### Task 6: ドキュメント・設定例

**Files:**
- Modify: `docs/hooks/secrets_scan.md` / `docs/hooks/exfil_guard.md` / `docs/hooks/exfil_output_scan.md`
- Modify: `docs/configuration.md`
- Modify: `docs/security-model.md`
- Modify: `examples/settings.full.json`
- (すべて通常の Edit/Write)

**Interfaces:** ドキュメントのみ。コード変更なし。

- [ ] **Step 1: `docs/configuration.md` に `scanners` セクションを追記**

トップレベル設定として次を記載する:
```json
"scanners": {
  "gitleaks": "auto",
  "gitleaks_image": "ghcr.io/gitleaks/gitleaks:v8.30.1",
  "gitleaks_config": null
}
```
- `gitleaks`: `"auto"`(PATH にあれば加算/無ければ無コスト)| `"off"`(内蔵のみ)| `"docker"`(`docker run` 経由・明示 opt-in)。
- `gitleaks_image`: `docker` モードのイメージ(既定は固定タグ)。
- `gitleaks_config`: `-c` に渡す `.gitleaks.toml` パス。未指定時は `<cwd>/.gitleaks.toml` を自動採用、無ければ gitleaks 既定。
- Docker モードは `secrets_scan`/`exfil_output_scan` の短タイムアウトでは予算超過しうるため実質 `exfil_guard` 向き、かつ `DOCKER_HOST` がリモートだと payload を外部送信し得るためローカルデーモン前提、と明記。

- [ ] **Step 2: 各フックドキュメントに追記**

`docs/hooks/secrets_scan.md`・`exfil_guard.md`・`exfil_output_scan.md` に「秘密検出は内蔵 patterns(floor)に gitleaks を union 加算する。gitleaks は任意・既定 `auto`・不在/失敗は fail-open で floor 不変」を各1段落追記。`exfil_output_scan.md` には「gitleaks の `Secret` により `action:"redact"` も両立」を追記。

- [ ] **Step 3: `docs/security-model.md` に保証範囲を追記**

- 「gitleaks は加算(union)であり内蔵 floor(`credentials=deny`)を置換しない。gitleaks 不在・失敗・`.gitleaks.toml` の広い allowlist があっても deny 保証は不変」。
- 「Docker モードは `DOCKER_HOST` がリモートだと payload を外部送信し得る。ローカルデーモン前提」。

- [ ] **Step 4: `examples/settings.full.json` に scanners を追加**

既存 JSON に上記 `scanners` ブロックを追加(既定値のまま。コメント可能なら用途を1行)。

- [ ] **Step 5: 検証**

Run: `uv run pytest tests/test_packaging.py -q`
Expected: PASS(設定例・パッケージ整合性テストが通る)

- [ ] **Step 6: コミット**

```bash
git add docs/ examples/settings.full.json
git commit -m "docs: gitleaks委譲(scanners設定/union/Docker/redact)を反映"
```

---

### Task 7: リリース(CHANGELOG 0.6.0・バージョン更新)

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`（`version`）
- Modify: `.claude-plugin/plugin.json`（`version`）
- (通常の Edit。もし write_protected で遮断された場合のみパッチスクリプト経由)

**Interfaces:** リリースメタデータのみ。

- [ ] **Step 1: バージョンを 0.6.0 へ更新**

`pyproject.toml` と `.claude-plugin/plugin.json` の `version` を `0.5.0` → `0.6.0`。

- [ ] **Step 2: CHANGELOG に 0.6.0 セクションを追加**

```markdown
## [0.6.0] - 2026-07-22

### Added
- 秘密検出の任意バックエンドとして gitleaks 委譲を追加(`scanners.gitleaks`: `auto`/`off`/`docker`)。内蔵 patterns を floor として残す union(加算)方式で、deny 保証を弱めずカバレッジを拡張。
- `scanners.gitleaks_image`(Docker イメージ)・`scanners.gitleaks_config`(`.gitleaks.toml` 指定、未指定時は `<cwd>/.gitleaks.toml` 自動)を追加。

### Changed
- `exfil_guard`/`secrets_scan`/`exfil_output_scan` の秘密検出を共有集約点 `scanners.scan_secrets` 経由に変更(内蔵挙動は不変・gitleaks 不在時は従来同等)。
```

- [ ] **Step 3: 最終検証**

Run: `uv run pytest -q && uv run ruff check hooks tests`
Expected: PASS(全通過・Lint クリーン)

- [ ] **Step 4: 実ホームパスのリークチェック(CI 同等)**

Run: `git grep -nP '/(home|Users)/(?!USER\b|alice\b|user\b)[A-Za-z_][A-Za-z0-9._-]*' -- || echo clean`
Expected: `clean`

- [ ] **Step 5: コミット**

```bash
git add CHANGELOG.md pyproject.toml .claude-plugin/plugin.json
git commit -m "chore(release): 0.6.0"
```

- [ ] **Step 6: `.superpowers/` の一時パッチスクリプトを後片付け**

```bash
rm -f .superpowers/patch_task1.py .superpowers/patch_task2.py .superpowers/patch_task3.py .superpowers/patch_task4.py .superpowers/patch_task5.py
```
(`.superpowers/` は gitignore 配下のため履歴には影響しない)

---

## Self-Review（プラン↔スペック突き合わせ）

- **スペック網羅**: `scanners.py`(T2)/ config スキーマ(T1)/ 3フック配線(T3–T5)/ Docker モード・設定ファイル(T1・T2・T6)/ union・fail-open・deny 保証(T2–T5 のテスト)/ redact 両立(T5)/ ドキュメント(T6)/ バージョン(T7)。スペックの各節に対応タスクあり。
- **型・名称整合**: `scan_secrets(text, scanners_cfg=None, cwd=None)` / `_gitleaks_argv(sc, cwd)` / `_run_gitleaks(argv, text)` を全タスクで一貫使用。フック呼び出しは全て `cfg_all.get("scanners")` と `event.get("cwd")` を渡す。
- **プレースホルダー無し**: 各コード手順は実コードを提示。秘密フィクスチャは実行時連結(`"AKIA" + "Z" * 16`)で字面回避。
- **決定論**: autouse フィクスチャ(T2)で実 gitleaks/docker を隠し、既存テストは floor のみで不変。gitleaks 検証テストは stub 実行ファイル/モンキーパッチで注入。
