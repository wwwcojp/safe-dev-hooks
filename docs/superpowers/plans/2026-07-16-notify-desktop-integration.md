# notify デスクトップ通知統合 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `examples/notify_wrapper.sh` のプラットフォーム判別ロジックを `notify.py` にPython移植し、ゼロ設定でデスクトップ通知が動く `notify.method: "auto"` を既定にする(0.3.0)。

**Architecture:** `hooks/notification/notify.py` 内に自己完結の小関数群(WSL判定・3バックエンド・チェーン)を追加し、`main()` の優先順位を「enabled → command(互換) → method」に再構成する。設定スキーマに `notify.method`(enum: auto/bell)を追加。wrapperと旧テストは削除。

**Tech Stack:** Python標準ライブラリのみ(os / shutil / subprocess / shlex / pathlib)。テストは pytest + monkeypatch(`tests/helpers.py` の `load_hook` パターン)。

**Spec:** `docs/superpowers/specs/2026-07-16-notify-desktop-integration-design.md`

## Global Constraints

- Python は標準ライブラリのみ。各hookスクリプト冒頭の PEP 723 ブロック(`# /// script` + `requires-python = ">=3.10"`)を維持する(`tests/test_packaging.py` が強制)
- リポジトリ内ファイルに実ホームパスを書かない。プレースホルダーは `$HOME` / `/home/USER` / テストは `/home/alice`(`.claude/rules/no-personal-paths.md`)
- lint: `uv run ruff check .`(line-length 100、E501有効)が常にパスすること
- テスト実行は `uv run pytest`(全件)または `uv run pytest tests/<file>::<test> -v`(個別)
- 通知タイトルは `"Claude Code"` 固定
- コミットメッセージは日本語、末尾に `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- ドキュメント・コード内コメントは日本語(既存スタイルに合わせる)

---

### Task 1: config に notify.method キーを追加

**Files:**
- Modify: `hooks/lib/config.py`(DEFAULTS の notify セクション:30行付近、`_ENUM_KEYS`:33-37行付近)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 既存の `config.load_config(cwd) -> dict`(3層マージ+enum検証)
- Produces: `load_config()["notify"]` が `{"enabled": bool, "method": "auto"|"bell", "command": str|None}` を返す。`method` の既定は `"auto"`、不正値は `"auto"` へフォールバックし `_errors` に1件記録される

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_config.py` の末尾に追加:

```python
def test_notify_method_default_and_typo_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["notify"]["method"] == "auto"
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "toast"}}), encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["notify"]["method"] == "auto"
    assert len(cfg["_errors"]) == 1
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_config.py::test_notify_method_default_and_typo_fallback -v`
Expected: FAIL(`KeyError: 'method'`)

- [ ] **Step 3: 最小実装**

`hooks/lib/config.py` の DEFAULTS 内:

```python
    "notify": {"enabled": True, "method": "auto", "command": None},
```

`_ENUM_KEYS` に1行追加:

```python
_ENUM_KEYS = {
    ("exfil_guard", "mode"): {"detect", "always"},
    ("exfil_output_scan", "action"): {"warn", "redact"},
    ("quality_gate", "mode"): {"block", "warn"},
    ("notify", "method"): {"auto", "bell"},
}
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_config.py -v`
Expected: 全件 PASS(既存の enum テスト含む)

- [ ] **Step 5: コミット**

```bash
git add hooks/lib/config.py tests/test_config.py
git commit -m "feat: notify.method設定キーを追加(auto/bell、既定auto)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: notify.py に _is_wsl() を追加

**Files:**
- Modify: `hooks/notification/notify.py`
- Test: `tests/test_audit_and_notify.py`

**Interfaces:**
- Consumes: なし(純粋なOS判定)
- Produces: `notify._is_wsl() -> bool`、`notify._PROC_VERSION: pathlib.Path`(テストがmonkeypatchで差し替えるモジュール定数)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_audit_and_notify.py` の末尾に追加(`notify = load_hook("notification/notify.py")` はファイル冒頭に既存):

```python
def test_is_wsl_by_env(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert notify._is_wsl() is True


def test_is_wsl_by_proc_version(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    fake = tmp_path / "version"
    fake.write_text("Linux version 6.6.0-Microsoft-standard", encoding="utf-8")
    monkeypatch.setattr(notify, "_PROC_VERSION", fake)
    assert notify._is_wsl() is True


def test_is_wsl_false_on_plain_linux(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    fake = tmp_path / "version"
    fake.write_text("Linux version 6.6.0-generic", encoding="utf-8")
    monkeypatch.setattr(notify, "_PROC_VERSION", fake)
    assert notify._is_wsl() is False
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_audit_and_notify.py -v -k is_wsl`
Expected: FAIL(`AttributeError: module 'notify' has no attribute '_is_wsl'`)

- [ ] **Step 3: 最小実装**

`hooks/notification/notify.py` に `import os` を追加し(import群はアルファベット順を維持)、`main()` の前に:

```python
_PROC_VERSION = Path("/proc/version")


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in _PROC_VERSION.read_text(encoding="utf-8").lower()
    except OSError:
        return False
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_audit_and_notify.py -v -k is_wsl`
Expected: 3件 PASS

- [ ] **Step 5: コミット**

```bash
git add hooks/notification/notify.py tests/test_audit_and_notify.py
git commit -m "feat: notifyにWSL判定(_is_wsl)を追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 通知バックエンド3種と _notify_desktop チェーン

**Files:**
- Modify: `hooks/notification/notify.py`
- Test: `tests/test_audit_and_notify.py`

**Interfaces:**
- Consumes: Task 2 の `_is_wsl()`
- Produces:
  - `notify.TITLE: str = "Claude Code"`
  - `notify._notify_windows_toast(title: str, message: str) -> bool`
  - `notify._notify_notify_send(title: str, message: str) -> bool`
  - `notify._notify_osascript(title: str, message: str) -> bool`
  - `notify._notify_desktop(message: str) -> bool` — チェーン試行、1つでも成功したら True

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_audit_and_notify.py` の末尾に追加:

```python
def test_windows_toast_passes_message_via_env(monkeypatch):
    captured = {}

    def fake_run(argv, env=None, capture_output=None, timeout=None):
        captured["argv"] = argv
        captured["env"] = env

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    injected = 'x"; Remove-Item -Recurse $HOME; "'
    assert notify._notify_windows_toast("Claude Code", injected) is True
    assert captured["argv"][0] == "powershell.exe"
    # メッセージは環境変数で渡り、コマンド文字列には埋め込まれない
    assert captured["env"]["NOTIFY_MSG"] == injected
    assert captured["env"]["NOTIFY_TITLE"] == "Claude Code"
    assert captured["env"]["WSLENV"].endswith("NOTIFY_TITLE:NOTIFY_MSG")
    assert "Remove-Item" not in " ".join(captured["argv"])


def test_desktop_chain_order_and_fallthrough(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "_is_wsl", lambda: True)
    monkeypatch.setattr(notify.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        notify, "_notify_windows_toast", lambda t, m: calls.append("toast") or False
    )
    monkeypatch.setattr(
        notify, "_notify_notify_send", lambda t, m: calls.append("notify-send") or True
    )
    monkeypatch.setattr(
        notify, "_notify_osascript", lambda t, m: calls.append("osascript") or True
    )
    assert notify._notify_desktop("m") is True
    # toast失敗後にnotify-sendへ進み、成功したらosascriptは呼ばない
    assert calls == ["toast", "notify-send"]


def test_desktop_chain_all_unavailable(monkeypatch):
    monkeypatch.setattr(notify, "_is_wsl", lambda: False)
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    assert notify._notify_desktop("m") is False
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_audit_and_notify.py -v -k "toast or desktop_chain"`
Expected: FAIL(`AttributeError: module 'notify' has no attribute '_notify_windows_toast'` 等)

- [ ] **Step 3: 最小実装**

`hooks/notification/notify.py` に `import shutil` を追加し、`_is_wsl` の後へ:

```python
TITLE = "Claude Code"
_BACKEND_TIMEOUT = 5

# メッセージはインジェクション回避のため環境変数で渡す(WSLENVで境界を越える)
_TOAST_PS_SCRIPT = (
    '$ErrorActionPreference = "Stop"\n'
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType = WindowsRuntime] | Out-Null\n"
    "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
    '$texts = $template.GetElementsByTagName("text")\n'
    "$texts.Item(0).AppendChild($template.CreateTextNode($env:NOTIFY_TITLE)) | Out-Null\n"
    "$texts.Item(1).AppendChild($template.CreateTextNode($env:NOTIFY_MSG)) | Out-Null\n"
    '$appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell'
    '\\v1.0\\powershell.exe"\n'
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show("
    "[Windows.UI.Notifications.ToastNotification]::new($template))\n"
)


def _run_backend(argv: list[str], env: dict | None = None) -> bool:
    try:
        proc = subprocess.run(
            argv, env=env, capture_output=True, timeout=_BACKEND_TIMEOUT
        )
        return proc.returncode == 0
    except Exception:
        return False


def _notify_windows_toast(title: str, message: str) -> bool:
    env = dict(os.environ)
    env["NOTIFY_TITLE"] = title
    env["NOTIFY_MSG"] = message
    wslenv = env.get("WSLENV", "")
    env["WSLENV"] = (wslenv + ":" if wslenv else "") + "NOTIFY_TITLE:NOTIFY_MSG"
    return _run_backend(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS_SCRIPT],
        env=env,
    )


def _notify_notify_send(title: str, message: str) -> bool:
    return _run_backend(["notify-send", title, message])


def _notify_osascript(title: str, message: str) -> bool:
    # AppleScriptへの文字列埋め込みを避け、argv経由で渡す
    return _run_backend(
        [
            "osascript",
            "-e", "on run argv",
            "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e", "end run",
            title,
            message,
        ]
    )


def _notify_desktop(message: str) -> bool:
    if _is_wsl() and shutil.which("powershell.exe"):
        if _notify_windows_toast(TITLE, message):
            return True
    if shutil.which("notify-send"):
        if _notify_notify_send(TITLE, message):
            return True
    if shutil.which("osascript"):
        if _notify_osascript(TITLE, message):
            return True
    return False
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_audit_and_notify.py -v -k "toast or desktop_chain"`
Expected: 3件 PASS

- [ ] **Step 5: lint確認とコミット**

Run: `uv run ruff check hooks/notification/notify.py`
Expected: `All checks passed!`

```bash
git add hooks/notification/notify.py tests/test_audit_and_notify.py
git commit -m "feat: notifyにデスクトップ通知バックエンド(WSLトースト/notify-send/osascript)を実装

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: main() の優先順位配線と既存テスト更新

**Files:**
- Modify: `hooks/notification/notify.py`(`main()` を置き換え)
- Modify: `tests/test_audit_and_notify.py`(`test_notify_default_bell` を置き換え、新テスト追加)

**Interfaces:**
- Consumes: Task 1 の `notify.method` 設定、Task 3 の `_notify_desktop(message) -> bool`
- Produces: 優先順位 enabled → command → method(bell/auto) → ベルフォールバック の `main()`。auto成功時は出力なし(exit 0)、それ以外のベル時は `{"terminalSequence": "\u0007"}` を出力

- [ ] **Step 1: 既存テストを新仕様に置き換え、失敗するテストを書く**

`tests/test_audit_and_notify.py` の `test_notify_default_bell` を**削除**し、以下に置き換え・追加する(`test_notify_custom_command` は無変更で残す):

```python
def test_notify_default_auto_falls_back_to_bell(monkeypatch, tmp_path, capsys):
    """既定(auto)でデスクトップ通知が全滅した場合はベルへフォールバックする。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(notify, "_notify_desktop", lambda msg: False)
    event = {
        "hook_event_name": "Notification",
        "cwd": str(tmp_path),
        "notification_type": "permission_prompt",
        "message": "許可待ち",
    }
    out = _run(notify, monkeypatch, event, capsys)
    assert out["terminalSequence"] == "\u0007"


def test_notify_auto_desktop_success_outputs_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    received = []
    monkeypatch.setattr(notify, "_notify_desktop", lambda msg: received.append(msg) or True)
    event = {"hook_event_name": "Notification", "cwd": str(tmp_path), "message": "許可待ち"}
    out = _run(notify, monkeypatch, event, capsys)
    assert out is None
    assert received == ["許可待ち"]


def test_notify_method_bell_skips_desktop(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "bell"}}), encoding="utf-8"
    )

    def _boom(msg):
        raise AssertionError("method=bellではデスクトップチェーンを呼ばない")

    monkeypatch.setattr(notify, "_notify_desktop", _boom)
    event = {"hook_event_name": "Notification", "cwd": str(tmp_path), "message": "m"}
    out = _run(notify, monkeypatch, event, capsys)
    assert out["terminalSequence"] == "\u0007"


def test_notify_command_skips_desktop(monkeypatch, tmp_path, capsys):
    """notify.command設定時はmethodに関わらずコマンドが最優先(互換性)。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    marker = tmp_path / "notified.txt"
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"command": f"touch {marker}"}}), encoding="utf-8"
    )

    def _boom(msg):
        raise AssertionError("command設定時はデスクトップチェーンを呼ばない")

    monkeypatch.setattr(notify, "_notify_desktop", _boom)
    event = {"hook_event_name": "Notification", "cwd": str(tmp_path), "message": "done"}
    out = _run(notify, monkeypatch, event, capsys)
    assert marker.exists()
    assert out is None


def test_notify_disabled_outputs_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"enabled": False}}), encoding="utf-8"
    )

    def _boom(msg):
        raise AssertionError("enabled=falseでは何も実行しない")

    monkeypatch.setattr(notify, "_notify_desktop", _boom)
    event = {"hook_event_name": "Notification", "cwd": str(tmp_path), "message": "m"}
    out = _run(notify, monkeypatch, event, capsys)
    assert out is None
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_audit_and_notify.py -v -k notify`
Expected: `test_notify_auto_desktop_success_outputs_nothing` と `test_notify_method_bell_skips_desktop` が FAIL(現行 `main()` は method を見ずに常にベルを返すため)。他は PASS でもよい

- [ ] **Step 3: main() を置き換え**

`hooks/notification/notify.py` の `main()` 全体とモジュールdocstringを置き換え:

```python
"""通知イベントをデスクトップ通知・ターミナルベル・任意コマンドでユーザーへ伝える。"""
```

```python
def main() -> None:
    event = hook_io.read_event()
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("notify", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    message = event.get("message", "")
    command = cfg.get("command")
    if command:
        try:
            subprocess.run(
                shlex.split(command.replace("{message}", shlex.quote(message))),
                timeout=10, capture_output=True,
            )
        except Exception:
            pass
        hook_io.finalize(None, cfg_all)
    if cfg.get("method", "auto") == "auto" and _notify_desktop(message):
        hook_io.finalize(None, cfg_all)
    hook_io.finalize({"terminalSequence": "\u0007"}, cfg_all)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_audit_and_notify.py -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add hooks/notification/notify.py tests/test_audit_and_notify.py
git commit -m "feat: notifyの既定をデスクトップ通知自動判別(method: auto)へ変更

優先順位は enabled → command(従来互換・最優先) → method(auto/bell)。
autoはデスクトップ通知全滅時にターミナルベルへフォールバックする。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: wrapper と旧テストの削除

**Files:**
- Delete: `examples/notify_wrapper.sh`
- Delete: `tests/test_notify_wrapper.py`

**Interfaces:**
- Consumes: Task 4 完了(同等機能がHook本体に存在すること)
- Produces: なし(削除のみ)

- [ ] **Step 1: 削除**

```bash
git rm examples/notify_wrapper.sh tests/test_notify_wrapper.py
```

- [ ] **Step 2: 全テストが通ることを確認**

Run: `uv run pytest -q`
Expected: 全件 PASS(wrapper系4件が消え、notify系の新テストが加わった状態)

- [ ] **Step 3: 参照残りが無いことを確認**

Run: `grep -rn "notify_wrapper" --include="*.py" --include="*.json" --include="*.sh" hooks/ tests/ examples/ .claude* 2>/dev/null || echo "参照なし"`
Expected: `参照なし`(ドキュメント内の参照は Task 6 で更新する)

- [ ] **Step 4: コミット**

```bash
git commit -m "refactor: notify_wrapper.shと旧テストを削除(機能はnotify.py本体へ統合済み)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: ドキュメント更新

**Files:**
- Modify: `docs/hooks/notify.md`(全面改稿)
- Modify: `docs/configuration.md`(スキーマの notify ブロック)
- Modify: `README.ja.md`(Hook一覧の notify 行)
- Modify: `README.md`(Hook一覧の notify 行)

**Interfaces:**
- Consumes: Task 1〜5 の最終仕様
- Produces: 新仕様と一致したドキュメント一式

- [ ] **Step 1: docs/hooks/notify.md を以下の内容で全面置き換え**

```markdown
# notify

## 目的

許可待ちやアイドル状態などの `Notification` イベントをユーザーに知らせる。既定(`method: "auto"`)で実行環境を自動判別してデスクトップ通知(WSL→Windowsトースト / Linuxデスクトップ→notify-send / macOS→osascript)を出し、使えない環境ではターミナルベルにフォールバックする。任意コマンドへの差し替え(`notify.command`)も可能。

## 対象イベント / matcher

- イベント: `Notification`(ツールmatcherは無く、Notificationイベント全般が対象)
- timeout: 10秒(`hooks/hooks.json`)

## 判定基準

このHookは `deny`/`ask`/`block` を返さない通知専用Hookである。優先順位:

1. `notify.enabled: false` → 何もしない
2. `notify.command` 設定あり → コマンド文字列中の `{message}` を通知メッセージ(シェルエスケープ済み)で置換し実行(タイムアウト10秒)。結果やエラーは無視され、ベルも鳴らさない
3. `notify.method: "bell"` → `{"terminalSequence": "\u0007"}`(ベル文字)を返す
4. `notify.method: "auto"`(既定)→ 下表のデスクトップ通知チェーンを順に試行し、最初に成功した時点で終了。全滅ならベルへフォールバック

| 順 | 環境判定 | 通知手段 |
|---|---|---|
| 1 | `WSL_DISTRO_NAME` があるか `/proc/version` に microsoft を含み、かつ `powershell.exe` が `PATH` にある | WinRT APIによるWindowsトースト |
| 2 | `notify-send` が `PATH` にある | Linuxデスクトップ通知 |
| 3 | `osascript` が `PATH` にある | macOS通知センター |

各バックエンドはタイムアウト5秒で実行され、失敗(非ゼロ終了・例外・タイムアウト)時は次へ進む。通知タイトルは `Claude Code` 固定。メッセージはPowerShellへは環境変数(`NOTIFY_TITLE`/`NOTIFY_MSG`、`WSLENV` でWSL境界を越える)、notify-send/osascriptへはargvで渡し、シェルやスクリプトへの文字列埋め込みは行わない。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `notify.enabled` | `true` | falseで本Hookを無効化 |
| `notify.method` | `"auto"` | `"auto"`=デスクトップ通知の自動判別(不可ならベル) / `"bell"`=常にターミナルベル |
| `notify.command` | `null` | 設定時は `method` より優先。`{message}` プレースホルダーが通知メッセージに置換される |

## 設定ファイルの置き場所

本Hookが読む設定ファイルは `~/.claude/claude-hooks.json`(個人・グローバル)とプロジェクト直下の `.claude-hooks.json` の2つ([設定リファレンス](../configuration.md)の3層マージを参照)。既定の `auto` はゼロ設定で動くため、通常は設定不要。`notify.command` を使う場合はマシン固有のパスを含みやすいためグローバル側を推奨する。

**注意**: Claude Code本体の `settings.json` / `settings.local.json` は本Hookの読み込み対象ではない。そこに `notify` キーを書いても無視される。

## 設定例

ターミナルベルに固定する:

```json
{
  "notify": {"method": "bell"}
}
```

独自コマンドへ差し替える(`notify.command` はシェルを介さず実行されるため、`$HOME` 等の展開が必要な場合は `bash -c` で包む):

```json
{
  "notify": {
    "command": "bash -c 'exec \"$HOME/bin/my-notify.sh\" \"$1\"' _ {message}"
  }
}
```

## 動作確認

```bash
# リポジトリ直下で実行。自環境のデスクトップ通知が表示されれば成功
printf '{"cwd": "%s", "message": "notify動作確認"}' "$PWD" | uv run hooks/notification/notify.py
```

出力が**空**であればデスクトップ通知が成功している。`{"terminalSequence": "\u0007"}` が出力された場合はデスクトップ通知が使えない環境で、ベルへフォールバックしている。

なお `Notification` イベントは `audit_log` の記録対象外のため、監査ログから本Hookの発火有無は確認できない([audit_log の既知の限界](audit_log.md)を参照)。

## 既知の限界

- カスタム `notify.command` の実行が失敗しても例外は握りつぶされ、フォールバックのベルも鳴らないため、通知が完全に無音になり得る(`method` 系は全滅時にベルへフォールバックする)。
- 通知の抑制・重複排除(デデュープ)・レート制限は無く、短時間に多数の `Notification` が発生した場合は通知もその都度実行される。
- バックエンドのタイムアウトは5秒固定、`command` のタイムアウトは10秒固定で設定不可。
- デスクトップ通知の成否は各コマンドの終了コードで判定するため、通知デーモン側で表示が抑制されるケース(集中モード等)は成功扱いになる。
```

- [ ] **Step 2: docs/configuration.md のスキーマを更新**

`## 2. 全スキーマ` 内の notify ブロックを置き換え:

```jsonc
  "notify": {
    "enabled": true,
    "method": "auto",                        // "auto"=デスクトップ通知の自動判別(不可ならベル) / "bell"=常にベル
    "command": null                          // 設定時はmethodより優先。{message} 置換で実行
  }
```

- [ ] **Step 3: README のHook一覧を更新**

`README.ja.md` の notify 行を置き換え:

```markdown
| [notify](docs/hooks/notify.md) | Notification | 許可待ち・アイドル時の通知(既定はデスクトップ通知の自動判別、不可ならベル。bell固定・コマンド差し替えも可) |
```

`README.md` の notify 行を置き換え:

```markdown
| [notify](docs/hooks/notify.md) | Notification | Notifies on permission-wait/idle (default: auto-detected desktop notification with bell fallback; bell-only or a custom command also available) |
```

- [ ] **Step 4: wrapper参照とリークの最終確認**

Run: `grep -rn "notify_wrapper" README.md README.ja.md docs/ --include="*.md" | grep -v superpowers || echo "参照なし"`
Expected: `参照なし`(specs/plans内の履歴的言及は残ってよい)

Run: `grep -rnE '/(home|Users)/[A-Za-z_][A-Za-z0-9._-]*' docs/hooks/notify.md docs/configuration.md README.md README.ja.md | grep -vE '/(home|Users)/(USER|alice|user)\b' || echo "リークなし"`
Expected: `リークなし`

- [ ] **Step 5: コミット**

```bash
git add docs/hooks/notify.md docs/configuration.md README.md README.ja.md
git commit -m "docs: notifyのデスクトップ通知統合(method: auto)に合わせてドキュメントを更新

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: CHANGELOG と plugin.json(0.3.0)

**Files:**
- Modify: `CHANGELOG.md`(先頭に0.3.0エントリ追加)
- Modify: `.claude-plugin/plugin.json`(version)

**Interfaces:**
- Consumes: Task 1〜6 の変更内容
- Produces: 0.3.0 リリース情報

- [ ] **Step 1: CHANGELOG.md の `## [0.2.0]` の直前に追加**

```markdown
## [0.3.0] - 2026-07-16

### Changed

- **破壊的変更** `notify`: デスクトップ通知(WSL→Windowsトースト / Linuxデスクトップ→notify-send / macOS→osascript)をHook本体へ統合し、既定動作をターミナルベルから自動判別(`notify.method: "auto"`)へ変更。デスクトップ通知が使えない環境では従来どおりベルへフォールバックする。ベルに固定したい場合は `notify.method: "bell"` を設定する。`notify.command` は従来どおり最優先で動作する(完全互換)。

### Removed

- **破壊的変更** `examples/notify_wrapper.sh`: 同等機能がHook本体へ統合されたため削除。`notify.command` に本スクリプトを絶対パスで指定していた場合、リポジトリ/プラグイン更新でスクリプトが消えるため、設定から `notify.command` を削除して既定の `auto` へ移行すること(同等以上の動作をする)。
```

- [ ] **Step 2: .claude-plugin/plugin.json の version を更新**

```json
"version": "0.3.0",
```

- [ ] **Step 3: パッケージングテストが通ることを確認**

Run: `uv run pytest tests/test_packaging.py -v`
Expected: 全件 PASS

- [ ] **Step 4: コミット**

```bash
git add CHANGELOG.md .claude-plugin/plugin.json
git commit -m "release: 0.3.0(notifyデスクトップ通知統合)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 最終検証(実機E2E含む)

**Files:**
- なし(検証のみ)

**Interfaces:**
- Consumes: Task 1〜7 の全成果物
- Produces: 検証済みのブランチ(マージ判断へ)

- [ ] **Step 1: 全テストとlint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 全件 PASS / `All checks passed!`

- [ ] **Step 2: リポジトリ全体のリークチェック**

Run: `grep -rnE '/(home|Users)/[A-Za-z_][A-Za-z0-9._-]*' --include="*.py" --include="*.md" --include="*.json" hooks/ tests/ docs/ examples/ README.md README.ja.md CHANGELOG.md | grep -vE '/(home|Users)/(USER|alice|user)\b' || echo "リークなし"`
Expected: `リークなし`

- [ ] **Step 3: 実機E2Eスモーク(このマシンはWSL)**

Run: `printf '{"cwd": "%s", "message": "0.3.0 E2Eスモーク"}' "$PWD" | uv run hooks/notification/notify.py`
Expected: 出力が空(= デスクトップ通知チェーン成功)で、Windowsトーストが実際に表示される。表示は人間の目視確認が必要なので、実行後にユーザーへ確認を求めること

- [ ] **Step 4: グローバル設定の後始末(このマシン固有・リポジトリ外)**

`~/.claude/claude-hooks.json` に旧wrapperを指す `notify.command` が設定されている場合、`auto` で不要になるため削除する(`command` が残っていると削除済みwrapperのパスを実行しようとして通知が無音になる)。削除後にStep 3を再実行して確認する。

- [ ] **Step 5: 完了報告**

superpowers:finishing-a-development-branch スキルに従い、マージ・PR等の統合方法をユーザーに確認する。
