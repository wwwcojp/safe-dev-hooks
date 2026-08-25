# quality_gate の自動検出を承認制にする(0.8.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `quality_gate` の `AUTO_DETECT`(`ruff` / `rustfmt` / `npx eslint`)を、`trusted_projects` で承認済みのプロジェクトでのみ実行するようにし、スキップしたことを通知する。

**Architecture:** 承認判定はライブラリ側に一本化する — `config.load_config` が返す設定に `cfg["_project_trusted"]`(bool)を必ず載せ、`quality_gate` はそれを読むだけにする。判定そのものは `trust.is_trusted(root, trusted_projects)` に置き、既存の `classify_entry` の分類を再利用する。通知は `quality_gate` 自身が出し、文面とクールダウン(新セクション `autodetect_last`)は `trust` の既存機構を使う。

**Tech Stack:** Python 3.10+ stdlib(os / json / pathlib / time)、uv、pytest、ruff、mutmut(`scripts/verify.py mutation`)

**Spec:** `docs/superpowers/specs/2026-08-25-quality-gate-autodetect-trust-design.md`

## Global Constraints

- 実行時依存ゼロ(stdlib のみ)。`requires-python = ">=3.10"`。**3.10 と 3.14 の両方で通すこと**(CI は両方のマトリクス)
- **`hooks/` は自インストールの `write_protected_paths` により Edit/Write 不可** → Bash 経由の python 書込(読み → 置換 → 書き戻し)。`tests/`・`docs/`・`CHANGELOG.md` は通常の Edit/Write でよい
- mutation baseline を下回らせない: `config.py` 100.0、`trust.py` 98.1、`scanners.py` 99.3、`hook_io.py` 98.9、`patterns.py` 95.5、`bash_guard.py` 100.0。新規分岐はすべて変異テストで殺せる形にする。`# pragma: no mutate` を使わない。到達不能な既定値つき `.get` を書かない
- 「呼び出し側から観測できない」を等価変異の理由にしないこと。防御的な契約は内部関数を直接呼ぶ白箱テストで固定する
- `load_config` は例外を送出しない契約を維持する。各フックは例外を外に出さない(クラッシュは fail-open)
- `hooks/post_tool_use/quality_gate.py` は `only_mutate` 対象外(通常のテストのみ)
- 実ホームパスをリポジトリに書かない(テストは `tmp_path` / `/home/alice`、docs は `$HOME` / `/home/USER`)。テストは実 `$HOME` に書かない
- テストは両方向で書く: 承認済みで従来どおり動くこと、未承認で止まること
- コミットメッセージに危険コマンドの字面やシェルのリダイレクト記号(`>`・`<`)を書かない — このリポジトリのガードが走査して弾く
- 作業ブランチ: `feat/autodetect-trust-gate`(main から作成)

## File Structure

| ファイル | 責務 |
|---|---|
| Modify `hooks/lib/trust.py` | `is_trusted(root, trusted_projects)` を追加。自動検出スキップの通知文 `autodetect_skipped_notice(key)` と、クールダウン付き発行 `notify_autodetect_skipped(...)`(新セクション `autodetect_last`) |
| Modify `hooks/lib/config.py` | `_load_config` が `cfg["_project_trusted"]` を常に設定。例外フォールバック経路にも入れる |
| Modify `hooks/post_tool_use/quality_gate.py` | `resolve_commands` に承認状態を渡し、未承認なら `AUTO_DETECT` へ落ちない。スキップ時に通知を出す |
| Modify `tests/test_trust.py` | `is_trusted` と通知の単体テスト |
| Modify `tests/test_config.py` | `_project_trusted` の値と、必ず存在することのテスト |
| Modify `tests/test_quality_gate.py` | 承認/未承認での `resolve_commands` の分岐、外部コマンドが起動しないこと、通知 |
| Modify `tests/test_trust_blackbox.py` | 未承認プロジェクトで `.js` を編集しても外部コマンドが起動しない黒箱テスト |
| Modify `docs/hooks/quality_gate.md`・`docs/configuration.md`・`docs/security-model.md`・`README.md`・`README.ja.md` | 承認制であることの記述 |
| Modify `CHANGELOG.md`・`pyproject.toml`・`.claude-plugin/plugin.json`・`uv.lock` | 0.8.0 |

---

### Task 1: `trust.is_trusted` — 承認済み判定

**Files:**
- Modify: `hooks/lib/trust.py`(Bash 経由の python 書込 — write_protected)
- Test: `tests/test_trust.py`

**Interfaces:**
- Consumes: 既存の `classify_entry(value) -> tuple[str, str | None]`(`"pinned"` / `"unpinned"` / `"denied"` / `"ignored"` を返す)、`project_key(cwd) -> str`
- Produces: `def is_trusted(root: str | None, trusted_projects) -> bool`

**Steps:**

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_trust.py` の末尾に追加する。

```python
# --- is_trusted: 承認済みプロジェクトの判定(0.8.0) ---


def test_is_trusted_accepts_pinned_and_unpinned():
    key = "/home/alice/proj"
    digest = "sha256:" + "a" * 64
    assert trust.is_trusted(key, {key: digest}) is True
    assert trust.is_trusted(key, {key: True}) is True


def test_is_trusted_rejects_denied_and_unknown():
    key = "/home/alice/proj"
    assert trust.is_trusted(key, {key: False}) is False
    assert trust.is_trusted(key, {}) is False
    assert trust.is_trusted(key, {"/home/alice/other": True}) is False


def test_is_trusted_rejects_malformed_entry_values():
    key = "/home/alice/proj"
    for bad in ["sha256:zz", "sha256:" + "a" * 63, "", 0, 1, [], {}, None, 1.5]:
        assert trust.is_trusted(key, {key: bad}) is False, bad


def test_is_trusted_rejects_non_dict_trusted_projects():
    for bad in [None, [], "x", 0, True]:
        assert trust.is_trusted("/home/alice/proj", bad) is False, bad


def test_is_trusted_returns_false_for_none_root():
    assert trust.is_trusted(None, {"/home/alice/proj": True}) is False


def test_is_trusted_matches_by_realpath(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    link = tmp_path / "link"
    link.symlink_to(proj)
    entry = {os.path.realpath(str(proj)): True}
    assert trust.is_trusted(str(link), entry) is True


def test_is_trusted_never_raises():
    class Exploding:
        def get(self, *a, **k):
            raise RuntimeError("boom")
    assert trust.is_trusted("/home/alice/proj", Exploding()) is False
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run python -m pytest -q tests/test_trust.py -k is_trusted`
Expected: FAIL(`AttributeError: module 'hooks.lib.trust' has no attribute 'is_trusted'`)

- [ ] **Step 3: 実装する**

`hooks/lib/trust.py` の `classify_entry` の直後に追加する。**Bash 経由の python 書込**で行うこと。

```python
def is_trusted(root: str | None, trusted_projects) -> bool:
    """root が trusted_projects で承認済みかを返す(0.8.0)。例外を出さない。

    「承認済み」= ピン留め(`"sha256:…"`)またはピン留めなし(`true`)のエントリがあること。
    `false`(明示的な不承認)・不正な値・未登録は未承認。
    `.claude-hooks.json` の有無は問わない — 判定しているのは「利用者がこのディレクトリを
    信頼したか」であり、設定ファイルを採用するかどうか(`gate`)とは別の問いである。
    敵対的リポジトリは設定ファイルを同梱しなければよいので、設定の採否で代用できない。
    """
    if root is None or not isinstance(trusted_projects, dict):
        return False
    try:
        kind, _ = classify_entry(trusted_projects.get(project_key(root)))
    except Exception:
        return False
    return kind in ("pinned", "unpinned")
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run python -m pytest -q tests/test_trust.py -k is_trusted`
Expected: PASS(7 件)

- [ ] **Step 5: 全体とゲート**

Run: `uv run ruff check hooks tests scripts && uv run python -m pytest -q && uv run python scripts/verify.py quick`
Expected: すべて exit 0、既存テストが壊れていない

Run: `uv run python scripts/verify.py mutation`
Expected: exit 0(`trust.py` が 98.1 を下回らない)

- [ ] **Step 6: フックのスモーク**

Run: `for f in hooks/*/*.py; do case "$f" in hooks/lib/*) continue;; esac; echo '{}' | uv run "$f" >/dev/null || echo "FAIL $f"; done`
Expected: 出力なし(全 9 フックが exit 0)

- [ ] **Step 7: Commit**

```bash
git add hooks/lib/trust.py tests/test_trust.py
git commit -m "feat(trust): プロジェクトが承認済みかを判定する is_trusted を追加"
```

---

### Task 2: `cfg["_project_trusted"]` の配線

**Files:**
- Modify: `hooks/lib/config.py`(Bash 経由の python 書込 — write_protected)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: Task 1 の `trust.is_trusted(root, trusted_projects) -> bool`、既存の `project_root(cwd) -> str | None`
- Produces: `load_config(...)` の戻り値に `"_project_trusted"`(bool)が**常に**存在する

**Steps:**

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_config.py` の末尾に追加する。`approve_project` は既存ヘルパ
(`tests/helpers.py`、シグネチャ `approve_project(monkeypatch, global_path, proj, global_cfg=None, pinned=False)`)。

```python
# --- _project_trusted: 承認済みプロジェクトの判定を設定に載せる(0.8.0) ---


def test_project_trusted_true_when_approved(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"quality_gate": {"mode": "warn"}}))
    approve_project(monkeypatch, tmp_path / "global.json", proj, pinned=True)
    assert config.load_config(str(proj))["_project_trusted"] is True


def test_project_trusted_true_without_project_config_file(monkeypatch, tmp_path):
    # 設定ファイルが無くても、承認エントリがあれば承認済み(spec D2)
    proj = tmp_path / "proj"
    proj.mkdir()
    global_path = tmp_path / "global.json"
    global_path.write_text(
        json.dumps({"trusted_projects": {os.path.realpath(str(proj)): True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", global_path)
    cfg = config.load_config(str(proj))
    assert cfg["_project_trusted"] is True
    assert cfg["_notices"] == []


def test_project_trusted_false_when_unapproved(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = tmp_path / "proj"
    proj.mkdir()
    assert config.load_config(str(proj))["_project_trusted"] is False


def test_project_trusted_false_when_denied(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    global_path = tmp_path / "global.json"
    global_path.write_text(
        json.dumps({"trusted_projects": {os.path.realpath(str(proj)): False}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", global_path)
    assert config.load_config(str(proj))["_project_trusted"] is False


def test_project_trusted_present_on_exception_fallback(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(config, "_load_config", boom)
    cfg = config.load_config("/home/alice/proj")
    assert cfg["_project_trusted"] is False
    assert cfg["_notices"] == []
    assert cfg["_errors"]


def test_project_trusted_ignores_project_layer_self_approval(monkeypatch, tmp_path):
    # プロジェクト設定に自分を承認するエントリを書いても効かない
    proj = _proj_with(
        tmp_path,
        json.dumps({"trusted_projects": {os.path.realpath(str(tmp_path / "proj")): True}}),
    )
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    assert config.load_config(str(proj))["_project_trusted"] is False
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run python -m pytest -q tests/test_config.py -k project_trusted`
Expected: FAIL(`KeyError: '_project_trusted'`)

- [ ] **Step 3: 実装する**

`hooks/lib/config.py` を **Bash 経由の python 書込**で 2 箇所変更する。

`_load_config` の末尾、`cfg["_notices"] = collected` の直前に追加:

```python
    # 承認済み判定は「グローバル層の trusted_projects」だけを見る。プロジェクト層の
    # マージ後に評価すると、承認済みプロジェクトが自分で書き換えられてしまうため、
    # ここは gate() と同じく _apply_layer より後でも cfg["trusted_projects"] が
    # グローバル層由来であることに依存する(プロジェクト層が採用されるのは承認済みの
    # ときだけで、その場合でも自己昇格にはならない)。
    cfg["_project_trusted"] = trust.is_trusted(root, cfg["trusted_projects"])
```

`load_config` の例外フォールバック(`except Exception as exc:` の中)に追加:

```python
        cfg["_project_trusted"] = False
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run python -m pytest -q tests/test_config.py -k project_trusted`
Expected: PASS(6 件)

**注意**: `test_project_trusted_ignores_project_layer_self_approval` が落ちる場合、
`_project_trusted` の評価位置がプロジェクト層のマージ後になっていて、プロジェクト設定の
`trusted_projects` を拾っている。その場合は `root` を求めた直後(`_read_layer` の前)に
グローバル層の値で評価し、その結果を変数に保持して末尾で代入すること。

- [ ] **Step 5: 全体とゲート**

Run: `uv run ruff check hooks tests scripts && uv run python -m pytest -q && uv run python scripts/verify.py quick`
Expected: すべて exit 0

Run: `uv run python scripts/verify.py mutation`
Expected: exit 0(`config.py` が 100.0 を維持)

- [ ] **Step 6: Commit**

```bash
git add hooks/lib/config.py tests/test_config.py
git commit -m "feat(config): 承認済みプロジェクトかどうかを _project_trusted で公開する"
```

---

### Task 3: 通知文とクールダウン

**Files:**
- Modify: `hooks/lib/trust.py`(Bash 経由の python 書込 — write_protected)
- Test: `tests/test_trust.py`

**Interfaces:**
- Consumes: 既存の `project_key`、`cooldown_seconds`、`load_state`、`save_state`、`_section`、`GLOBAL_CONFIG_HINT`
- Produces:
  - `def autodetect_skipped_notice(key: str) -> str`
  - `def notify_autodetect_skipped(root: str, cooldown_sec: int, *, now: float | None = None, state_path: Path | None = None) -> list[str]`

**Steps:**

- [ ] **Step 1: 失敗するテストを書く**

```python
# --- 自動検出スキップの通知(0.8.0) ---


def test_autodetect_skipped_notice_exact_text():
    assert trust.autodetect_skipped_notice("/home/alice/proj") == (
        "[safe-dev-hooks] このプロジェクトは未承認のため、quality_gate の自動検出"
        "(ruff / rustfmt / eslint)を実行しませんでした。\n"
        "自動検出はプロジェクト同梱の設定ファイルを読み込むため、承認済みの"
        "プロジェクトでのみ実行します。\n"
        f"承認する場合は {trust.GLOBAL_CONFIG_HINT} の\n"
        '"trusted_projects" に次を追加してください:\n'
        '  "/home/alice/proj": true\n'
        "承認するとこのプロジェクトの設定ファイル(eslint.config.js など)が"
        "実行時に読み込まれます。"
    )


def test_notify_autodetect_skipped_first_call_notifies(tmp_path):
    out = trust.notify_autodetect_skipped(
        "/home/alice/proj", 3600, now=1000.0, state_path=tmp_path / "s.json"
    )
    assert len(out) == 1 and "未承認のため" in out[0]
    state = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert state["autodetect_last"] == {"/home/alice/proj": 1000.0}


def test_notify_autodetect_skipped_cooldown_suppresses_then_expires(tmp_path):
    sp = tmp_path / "s.json"
    trust.notify_autodetect_skipped("/home/alice/proj", 100, now=1000.0, state_path=sp)
    assert trust.notify_autodetect_skipped(
        "/home/alice/proj", 100, now=1050.0, state_path=sp
    ) == []
    assert len(
        trust.notify_autodetect_skipped("/home/alice/proj", 100, now=1101.0, state_path=sp)
    ) == 1


def test_notify_autodetect_skipped_zero_cooldown_notifies_every_time(tmp_path):
    sp = tmp_path / "s.json"
    for now in (1000.0, 1000.0, 1000.0):
        assert len(
            trust.notify_autodetect_skipped("/home/alice/proj", 0, now=now, state_path=sp)
        ) == 1


def test_notify_autodetect_skipped_notifies_when_state_unusable(tmp_path):
    unusable = tmp_path / "dir-not-file"
    unusable.mkdir()
    for _ in range(3):
        assert len(
            trust.notify_autodetect_skipped(
                "/home/alice/proj", 3600, now=1000.0, state_path=unusable
            )
        ) == 1


def test_notify_autodetect_skipped_uses_separate_section_from_skipped_last(tmp_path):
    sp = tmp_path / "s.json"
    trust.notify_skipped("/home/alice/a", "/home/alice/b", 3600, now=1000.0, state_path=sp)
    # 別セクションなので自動検出の通知は抑止されない
    assert len(
        trust.notify_autodetect_skipped("/home/alice/a", 3600, now=1000.0, state_path=sp)
    ) == 1


def test_notify_autodetect_skipped_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(trust, "_notify_autodetect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert trust.notify_autodetect_skipped(
        "/home/alice/proj", 3600, now=1000.0, state_path=tmp_path / "s.json"
    ) == []


def test_notify_autodetect_skipped_uses_wall_clock_when_now_omitted(monkeypatch, tmp_path):
    monkeypatch.setattr(trust.time, "time", lambda: 4242.0)
    trust.notify_autodetect_skipped("/home/alice/proj", 3600, state_path=tmp_path / "s.json")
    state = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert state["autodetect_last"]["/home/alice/proj"] == 4242.0
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run python -m pytest -q tests/test_trust.py -k autodetect`
Expected: FAIL(`AttributeError: … has no attribute 'autodetect_skipped_notice'`)

- [ ] **Step 3: 実装する**

`hooks/lib/trust.py` の `rejected_env_notice` の直後に通知文を、`notify_rejected_env` の
直後に発行関数を追加する。**Bash 経由の python 書込**で行うこと。

```python
def autodetect_skipped_notice(key: str) -> str:
    """未承認のため quality_gate の自動検出を実行しなかったことを知らせる通知文を返す。

    自動検出は `ruff`/`rustfmt`/`npx eslint` を起動し、それらはプロジェクト同梱の設定
    (`eslint.config.js` は JavaScript として評価される)を読む。承認前のリポジトリで
    これを走らせるのは「未承認の設定は採用しない」という 0.7.0 の原則に反するため、
    承認済みプロジェクトに限定する(0.8.0)。
    """
    return (
        "[safe-dev-hooks] このプロジェクトは未承認のため、quality_gate の自動検出"
        "(ruff / rustfmt / eslint)を実行しませんでした。\n"
        "自動検出はプロジェクト同梱の設定ファイルを読み込むため、承認済みの"
        "プロジェクトでのみ実行します。\n"
        f"承認する場合は {GLOBAL_CONFIG_HINT} の\n"
        '"trusted_projects" に次を追加してください:\n'
        f'  "{key}": true\n'
        "承認するとこのプロジェクトの設定ファイル(eslint.config.js など)が"
        "実行時に読み込まれます。"
    )


def notify_autodetect_skipped(
    root: str,
    cooldown_sec: int,
    *,
    now: float | None = None,
    state_path: Path | None = None,
) -> list[str]:
    """未承認のため自動検出をスキップしたことをクールダウン付きで通知する(0.8.0)。

    状態は `skipped_last` と分ける — あちらは「読まなかった設定ファイル」、こちらは
    「実行しなかった自動検出」で、利用者が取るべき行動も違う。枠を共有すると片方が
    もう片方を抑止してしまう。
    ここでの try/except は最後の砦(呼び出し元の quality_gate を落とさない)。
    """
    try:
        return _notify_autodetect(root, cooldown_sec, now=now, state_path=state_path)
    except Exception:
        return []


def _notify_autodetect(
    root: str,
    cooldown_sec: int,
    *,
    now: float | None = None,
    state_path: Path | None = None,
) -> list[str]:
    # 状態ファイルが使えなければ通知する側に倒す(可視性優先)
    cooldown_sec = cooldown_seconds(cooldown_sec)
    key = project_key(root)
    now = time.time() if now is None else now
    state = load_state(state_path)
    if state is None:
        state = {}
    last = _section(state, "autodetect_last").get(key)
    if isinstance(last, (int, float)) and not isinstance(last, bool) and now - last < cooldown_sec:
        return []
    state["autodetect_last"][key] = now
    save_state(state, state_path)
    return [autodetect_skipped_notice(key)]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run python -m pytest -q tests/test_trust.py -k autodetect`
Expected: PASS(8 件)

- [ ] **Step 5: 全体・ゲート・mutation**

Run: `uv run ruff check hooks tests scripts && uv run python -m pytest -q && uv run python scripts/verify.py quick && uv run python scripts/verify.py mutation`
Expected: すべて exit 0(`trust.py` が 98.1 を下回らない)

- [ ] **Step 6: Commit**

```bash
git add hooks/lib/trust.py tests/test_trust.py
git commit -m "feat(trust): 自動検出をスキップしたことを知らせる通知を追加"
```

---

### Task 4: `quality_gate` の分岐と通知

**Files:**
- Modify: `hooks/post_tool_use/quality_gate.py`(Bash 経由の python 書込 — write_protected)
- Test: `tests/test_quality_gate.py`

**Interfaces:**
- Consumes: Task 2 の `cfg_all["_project_trusted"]`、Task 3 の `trust.notify_autodetect_skipped(root, cooldown_sec, ...)`、既存の `config.project_root(cwd)`
- Produces: `resolve_commands(file_path, cfg, cwd, *, trusted: bool)`(キーワード専用引数を追加)

**Steps:**

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_quality_gate.py` の末尾に追加する。

```python
# --- 自動検出は承認済みプロジェクトでのみ実行する(0.8.0) ---


def test_resolve_commands_autodetect_runs_when_trusted(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    out = qg.resolve_commands("a.py", {"commands": {}}, str(tmp_path), trusted=True)
    assert out == ["ruff check a.py"]


def test_resolve_commands_autodetect_skipped_when_untrusted(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert qg.resolve_commands("a.py", {"commands": {}}, str(tmp_path), trusted=False) == []


def test_resolve_commands_explicit_commands_run_even_when_untrusted(tmp_path):
    cfg = {"commands": {"*.py": ["echo checked {file}"]}}
    out = qg.resolve_commands("a.py", cfg, str(tmp_path), trusted=False)
    assert out == ["echo checked a.py"]


def test_main_untrusted_project_does_not_start_any_subprocess(monkeypatch, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")

    def _boom(*a, **k):
        raise AssertionError("未承認プロジェクトで外部コマンドを起動してはならない")

    monkeypatch.setattr(qg.subprocess, "run", _boom)
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out is not None
    assert "未承認のため" in out["systemMessage"]


def test_main_untrusted_notice_is_cooldown_limited(monkeypatch, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    first = _run_main(monkeypatch, event, capsys)
    second = _run_main(monkeypatch, event, capsys)
    assert first is not None and "未承認のため" in first["systemMessage"]
    assert second is None or "未承認のため" not in (second.get("systemMessage") or "")


def test_main_untrusted_notice_omits_repo_supplied_command_text(monkeypatch, tmp_path, capsys):
    # 通知にリポジトリ由来のコマンド文字列を載せない(spec #3)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    target = tmp_path / "a.js"
    target.write_text("const x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    msg = (out or {}).get("systemMessage", "")
    assert "eslint.config.js" not in msg.split("承認するとこのプロジェクトの設定ファイル")[0]
    assert "npx" not in msg


def test_main_trusted_project_still_runs_checks(monkeypatch, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
    calls = []
    monkeypatch.setattr(qg, "run_checks", lambda cmds, cwd: calls.append(cmds) or [])
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    _run_main(monkeypatch, event, capsys)
    assert calls and calls[0] == [f"ruff check {shlex.quote(str(target))}"]
```

`tests/test_quality_gate.py` の import に必要なら `shlex` と
`from helpers import approve_project` を足すこと。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run python -m pytest -q tests/test_quality_gate.py -k "trusted or untrusted or autodetect"`
Expected: FAIL(`resolve_commands() got an unexpected keyword argument 'trusted'`)

- [ ] **Step 3: 実装する**

`hooks/post_tool_use/quality_gate.py` を **Bash 経由の python 書込**で変更する。

`resolve_commands` のシグネチャと自動検出の分岐:

```python
def resolve_commands(file_path: str, cfg: dict, cwd: str, *, trusted: bool) -> list:
    name = Path(file_path).name
    quoted = shlex.quote(file_path)
    commands = []
    for pattern, cmds in (cfg.get("commands") or {}).items():
        if fnmatch.fnmatch(name, pattern):
            commands += [c.replace("{file}", quoted) for c in cmds]
    if commands:
        return commands
    # 自動検出は承認済みプロジェクトでのみ(0.8.0)。ruff/rustfmt/eslint はいずれも
    # プロジェクト同梱の設定を読み、eslint.config.js は JavaScript として評価される。
    # 利用者が明示した commands は 0.7.0 の信頼ゲートを既に通っているため対象外。
    if not trusted:
        return []
    for patterns_str, exe, markers, cmd in AUTO_DETECT:
        ...  # 以降は既存のまま
```

`main()` の該当部分:

```python
    cwd = event.get("cwd") or "."
    root = config.project_root(cwd) or cwd
    trusted = bool(cfg_all.get("_project_trusted"))
    if not file_path or not Path(file_path).is_file():
        hook_io.finalize(None, cfg_all)
    try:
        commands = resolve_commands(file_path, cfg, root, trusted=trusted)
        skipped = not commands and not trusted
        failures = run_checks(commands, root) if commands else []
    except Exception as exc:
        hook_io.fail_open("quality_gate", exc)
        return
```

通知の合成は `failures` の処理のあと、`hook_io.finalize` の直前に置く:

```python
    if skipped:
        notices = trust.notify_autodetect_skipped(
            root, trust.cooldown_seconds(cfg_all.get("notice_cooldown_sec")),
        )
        if notices:
            out = dict(out or {})
            existing = out.get("systemMessage")
            msg = "\n".join(notices)
            out["systemMessage"] = f"{existing}\n{msg}" if existing else msg
```

`hooks/post_tool_use/quality_gate.py` の import に `trust` を足すこと
(既存は `from hooks.lib import config, hook_io`)。

**注意**: `skipped` は「未承認でコマンドが 1 つも解決しなかった」場合に限る。承認済みでも
マーカーが無ければコマンドは空になるが、そのときは通知しない(承認とは無関係のため)。

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run python -m pytest -q tests/test_quality_gate.py`
Expected: PASS(既存分も含めて全件)

- [ ] **Step 5: 全体・ゲート・mutation・スモーク**

Run: `uv run ruff check hooks tests scripts && uv run python -m pytest -q && uv run python scripts/verify.py quick && uv run python scripts/verify.py mutation`
Expected: すべて exit 0

Run: `for f in hooks/*/*.py; do case "$f" in hooks/lib/*) continue;; esac; echo '{}' | uv run "$f" >/dev/null || echo "FAIL $f"; done`
Expected: 出力なし

- [ ] **Step 6: Commit**

```bash
git add hooks/post_tool_use/quality_gate.py tests/test_quality_gate.py
git commit -m "feat(quality_gate): 自動検出を承認済みプロジェクトに限定し、スキップを通知する"
```

---

### Task 5: 黒箱テスト

**Files:**
- Modify: `tests/test_trust_blackbox.py`

**Interfaces:**
- Consumes: 既存の `tests/helpers.py::isolated_home_env(home, approve=None, pinned=False)`(subprocess 用に `HOME` を tmp へ向け、`approve` を渡すと承認を書く)

**Steps:**

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_trust_blackbox.py` の末尾に追加する。既存の `_run_hook` の実装に合わせて
呼び出すこと(このファイルの先頭にあるヘルパを読んでから書く)。

```python
def test_autodetect_does_not_run_in_unapproved_project(tmp_path):
    """未承認プロジェクトで .py を編集しても、外部コマンドが起動しない(黒箱)。"""
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    # ruff が「失敗する」ファイルを置く。実行されれば block になるので、
    # block されないこと自体が「実行されていない」証跡になる。
    bad = proj / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    event = {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(proj),
             "tool_input": {"file_path": str(bad)}}
    r = _run_hook("post_tool_use/quality_gate.py", event, home)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    assert out.get("decision") != "block"
    assert "未承認のため" in (out.get("systemMessage") or "")


def test_autodetect_runs_in_approved_project(tmp_path):
    """承認済みプロジェクトでは従来どおり自動検出が走る(黒箱)。"""
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (proj / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    bad = proj / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    event = {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(proj),
             "tool_input": {"file_path": str(bad)}}
    r = _run_hook("post_tool_use/quality_gate.py", event, home, approve=proj)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    assert out.get("decision") == "block"
```

`_run_hook` が `approve` を受けない場合は、`isolated_home_env(home, approve=proj)` を
使う形にシグネチャを拡張すること(既存の呼び出し側を壊さないよう既定は `None`)。

**注意**: 承認済みのテストは `ruff` が PATH にあることに依存する。無い環境では
`pytest.skip` すること(`shutil.which("ruff") is None`)。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run python -m pytest -q tests/test_trust_blackbox.py -k autodetect`
Expected: 未承認側が FAIL(現状は block される)

- [ ] **Step 3: Task 4 の実装で通ることを確認する**

Run: `uv run python -m pytest -q tests/test_trust_blackbox.py`
Expected: PASS(全件)

- [ ] **Step 4: 全体**

Run: `uv run python -m pytest -q && uv run python scripts/verify.py quick`
Expected: exit 0

- [ ] **Step 5: Commit**

```bash
git add tests/test_trust_blackbox.py
git commit -m "test(quality_gate): 未承認プロジェクトで自動検出が起動しないことを黒箱で固定"
```

---

### Task 6: ドキュメントとリリース(0.8.0)

**Files:**
- Modify: `docs/hooks/quality_gate.md`、`docs/configuration.md`、`docs/security-model.md`、`README.md`、`README.ja.md`
- Modify: `CHANGELOG.md`、`pyproject.toml`、`.claude-plugin/plugin.json`、`uv.lock`

**Steps:**

- [ ] **Step 1: 記述する前に実装と突き合わせる**

`hooks/lib/trust.py::is_trusted`、`hooks/lib/config.py::_load_config`、
`hooks/post_tool_use/quality_gate.py::resolve_commands` を読み、書こうとしている
記述が実装と一致することを確認する。**報告に一致確認の結果を書くこと。**

- [ ] **Step 2: `docs/hooks/quality_gate.md`**

自動検出の節に次を明記する。

- 自動検出(`ruff` / `rustfmt` / `npx eslint`)は **`trusted_projects` で承認済みの
  プロジェクトでのみ実行する**(0.8.0)
- 理由: これらはプロジェクト同梱の設定を読み、`eslint.config.js` は JavaScript として
  評価される
- 承認の有無は `.claude-hooks.json` の有無と無関係(ディレクトリを承認したかで決まる)
- 利用者が `commands` に明示したコマンドは従来どおり(グローバル設定に書いたものは
  承認不要、プロジェクト設定に書いたものは 0.7.0 のゲートで既に承認制)
- 未承認でスキップしたときは通知が出る(既定 1 時間のクールダウン)

- [ ] **Step 3: `docs/configuration.md`**

信頼層の節に 1 行追加する。

```markdown
- 承認は `.claude-hooks.json` の採用可否だけでなく、`quality_gate` の**自動検出**
  (`ruff`/`rustfmt`/`npx eslint`)を実行してよいかの判断にも使う(0.8.0)。未承認の
  プロジェクトでは自動検出を実行せず、通知を出す(詳細: [docs/hooks/quality_gate.md](hooks/quality_gate.md))
```

- [ ] **Step 4: `docs/security-model.md`**

「保証すること」に次を追加する。

```markdown
- **未承認プロジェクトのコードを実行しない(0.8.0)**: `quality_gate` の自動検出は
  `trusted_projects` で承認済みのプロジェクトでのみ実行する。したがって未承認の
  リポジトリを clone して開いただけでは、`ruff`/`rustfmt`/`npx eslint` は起動せず、
  それらがリポジトリ同梱の設定(`eslint.config.js` は JavaScript として評価される)を
  読み込むこともない。承認済みプロジェクトでの実行は従来どおりであり、承認とは
  「このリポジトリのメンテナを信頼する」という表明である。
```

既知の限界の節に経緯を残す。

```markdown
- **自動検出は 0.7.0 の信頼ゲートの外側にあった**: 0.7.0 は利用者が書いた
  `quality_gate.commands` を承認制にしたが、組み込みの `AUTO_DETECT` はゲートを
  通らなかった。さらに 0.7.1 でマーカー探索の基準がプロジェクトルートになった結果、
  サブディレクトリ作業中にも発火するようになり露出が広がった。0.8.0 で承認制に統一した。
```

- [ ] **Step 5: `README.md` / `README.ja.md`**

品質ゲートの説明に「自動検出は承認済みプロジェクトでのみ」を 1 文足す。両方の言語版で
同じ内容にすること。

- [ ] **Step 6: `CHANGELOG.md`**

既存の `## [Unreleased]` セクション(監査ログの修正が入っている)を `## [0.8.0] - <実装日>`
に昇格させ、次を追記する。既存の 0.7.x と同じ体裁(日本語、Keep a Changelog)に揃える。

```markdown
### Changed(破壊的変更)
- **`quality_gate` の自動検出は承認済みプロジェクトでのみ実行するようになった** —
  `AUTO_DETECT`(`ruff check` / `rustfmt --check` / `npx --no-install eslint`)は、
  グローバル設定の `trusted_projects` にそのプロジェクトのエントリがある場合にのみ
  実行する。**未承認のプロジェクトでは自動 lint が走らなくなる**(通知が出る。既定
  1 時間のクールダウン)。背景: これらはプロジェクト同梱の設定ファイルを読み込み、
  `eslint.config.js` は JavaScript として評価されるため、clone しただけの未承認
  リポジトリで `.js` を 1 ファイル編集するとリポジトリ由来のコードが実行され得た。
  0.7.0 の信頼ゲートは利用者が書いた `commands` を承認制にしたが、組み込みの
  `AUTO_DETECT` はその外側にあった。利用者が `commands` に明示したコマンドの扱いは
  変わらない。承認は `.claude-hooks.json` の有無と無関係で、ディレクトリ単位である。
```

- [ ] **Step 7: バージョン**

`pyproject.toml` と `.claude-plugin/plugin.json` を `0.8.0` にし、`uv lock` を実行する。
両者の一致を確認すること。

- [ ] **Step 8: 全体検証(3.10 と 3.14 の両方)**

Run: `uv run python scripts/verify.py all`
Expected: exit 0

Run: `uv run --python 3.10 --isolated --with pytest python -m pytest -q tests/`
Expected: 全件 PASS

Run: `uv run python -V`
Expected: `Python 3.14.6`(プロジェクトの `.venv` が元に戻っていること)

- [ ] **Step 9: 実地確認**

未承認のプロジェクトを scratchpad に作り、実フックへ合成イベントを流して、
外部コマンドが起動しないことと通知が出ることを確認する。**実 `$HOME` を汚さないこと**
(`HOME` を tmp へ向ける)。結果を報告に含める。

- [ ] **Step 10: Commit**

```bash
git add docs CHANGELOG.md README.md README.ja.md pyproject.toml .claude-plugin/plugin.json uv.lock
git commit -m "docs(release): 0.8.0 — 自動検出の承認制を文書化"
```

---

## Self-Review

**1. Spec coverage**

| spec の項目 | 対応 |
|---|---|
| D1 承認済みでのみ実行 | Task 4 |
| D2 承認 = `trusted_projects` にある(設定ファイルの有無を問わない) | Task 1 `is_trusted`、Task 2 の `test_project_trusted_true_without_project_config_file` |
| D3 ピン留め/ピン留めなしを問わず承認、`false` と未登録は未承認 | Task 1 のテスト |
| D4 スキップ時は通知(クールダウン付き) | Task 3・Task 4 |
| D5 明示した `commands` は変更しない | Task 4 の `test_resolve_commands_explicit_commands_run_even_when_untrusted` |
| D6 0.8.0 | Task 6 |
| #1 判定はライブラリ側・`_project_trusted` は必ず存在 | Task 2(例外経路のテストを含む) |
| #2 `AUTO_DETECT` の分岐 | Task 4 |
| #3 通知は `quality_gate` 自身・`autodetect_last`・状態不能なら毎回・コマンド文字列を載せない | Task 3・Task 4 |
| #4 ガード層・`enabled:false`・`mode` は不変 | Task 4 の既存テストが回帰として機能 |
| 保証する/しない | Task 6(security-model) |
| テスト一覧(両方向・スパイで非起動を固定・黒箱) | Task 1〜5 に分配 |
| ドキュメント・リリース | Task 6 |

**2. Placeholder scan**: Task 6 Step 6 の `<実装日>` のみ(実施時に埋める指示つき)。他に TBD なし。

**3. Type consistency**: `is_trusted(root: str | None, trusted_projects) -> bool`、
`autodetect_skipped_notice(key: str) -> str`、
`notify_autodetect_skipped(root: str, cooldown_sec: int, *, now, state_path) -> list[str]`、
`resolve_commands(file_path, cfg, cwd, *, trusted: bool) -> list`、
`cfg["_project_trusted"]`(bool)を全タスクで同名・同型で使用。
`trust.cooldown_seconds` と `config.project_root` は既存のものをそのまま使う。
