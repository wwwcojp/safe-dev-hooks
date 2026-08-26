import io
import json
import os
import shlex
from unittest import mock

import pytest
from helpers import approve_project, load_hook

from hooks.lib import config

qg = load_hook("post_tool_use/quality_gate.py")


def test_resolve_commands_from_config(tmp_path):
    cfg = {"commands": {"*.py": ["mylint {file}"]}}
    got = qg.resolve_commands(str(tmp_path / "app.py"), cfg, str(tmp_path), trusted=False)
    assert got == [f"mylint {tmp_path / 'app.py'}"]


def test_resolve_commands_no_match(tmp_path):
    cfg = {"commands": {"*.py": ["mylint {file}"]}}
    assert qg.resolve_commands(str(tmp_path / "app.md"), cfg, str(tmp_path), trusted=False) == []


def test_resolve_commands_quotes_spaced_paths(tmp_path):
    cfg = {"commands": {"*.py": ["mylint {file}"]}}
    spaced = str(tmp_path / "my dir" / "app.py")
    got = qg.resolve_commands(spaced, cfg, str(tmp_path), trusted=False)
    assert got and shlex.split(got[0])[-1] == spaced


def test_autodetect_requires_project_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    assert qg.resolve_commands(
        str(tmp_path / "a.py"), {"commands": {}}, str(tmp_path), trusted=True
    ) == []
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    got = qg.resolve_commands(str(tmp_path / "a.py"), {"commands": {}}, str(tmp_path), trusted=True)
    assert got and got[0].startswith("ruff check")


def test_run_checks_collects_failures(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    failures = qg.run_checks([f"python3 -m py_compile {bad}"], str(tmp_path))
    assert len(failures) == 1


def test_run_checks_passes(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1\n", encoding="utf-8")
    assert qg.run_checks([f"python3 -m py_compile {ok}"], str(tmp_path)) == []


# ---- プロジェクトルートの基準差し替え(project_root) ----


def test_autodetect_ignores_marker_outside_project_root(tmp_path, monkeypatch):
    """正当な挙動の保持: 渡された基準ディレクトリの外(無関係な祖先)のマーカーは拾わない。"""
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    got = qg.resolve_commands(str(sub / "app.py"), {"commands": {}}, str(sub), trusted=True)
    assert got == []


def test_run_checks_executes_in_given_root(tmp_path):
    """D4: 実行ディレクトリは呼び出し元が解決した基準ディレクトリそのものになる。"""
    stub = tmp_path / "record_cwd.py"
    stub.write_text(
        "import os, sys\n"
        "open(sys.argv[1], 'w', encoding='utf-8').write(os.getcwd())\n",
        encoding="utf-8",
    )
    marker = tmp_path / "cwd-marker.txt"
    qg.run_checks([f"python3 {stub} {marker}"], str(tmp_path))
    assert marker.read_text(encoding="utf-8") == os.path.realpath(str(tmp_path))


def _run_main(monkeypatch, event, capsys, expect_code=0):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit) as excinfo:
        qg.main()
    assert excinfo.value.code == expect_code
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_main_block_mode(monkeypatch, tmp_path, capsys):
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {"commands": {"*.py": ["python3 -m py_compile {file}"]}}}),
        encoding="utf-8",
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(bad)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"
    assert out["reason"].startswith("品質チェックが失敗しました。修正してください:\n")
    assert "py_compile" in out["reason"]


def test_main_warn_mode(monkeypatch, tmp_path, capsys):
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {
            "mode": "warn",
            "commands": {"*.py": ["python3 -m py_compile {file}"]},
        }}),
        encoding="utf-8",
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(bad)}}
    out = _run_main(monkeypatch, event, capsys)
    assert "decision" not in out
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert out["hookSpecificOutput"]["additionalContext"].startswith(
        "[safe-dev-hooks] 品質チェック警告:\n"
    )


def test_main_skips_missing_file(monkeypatch, tmp_path, capsys):
    # マーカーのある未承認プロジェクトにしておく = 先へ進んでしまえば通知が出る形。
    # 「存在しないファイルなら何もしない」が空パスの場合と独立に効くことを固定する。
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "tool_name": "Write", "cwd": str(tmp_path),
        "tool_input": {"file_path": str(tmp_path / "gone.py")},
    }
    assert _run_main(monkeypatch, event, capsys) is None


def test_main_skips_empty_file_path(monkeypatch, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": ""}}
    assert _run_main(monkeypatch, event, capsys) is None


def test_main_ignores_non_write_tools(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    event = {"tool_name": "Read", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    assert _run_main(monkeypatch, event, capsys) is None


def test_main_disabled_by_config_does_nothing(monkeypatch, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    approve_project(
        monkeypatch, tmp_path / "global.json", tmp_path,
        global_cfg={"quality_gate": {"enabled": False}},
    )
    spy = mock.Mock(side_effect=AssertionError("無効化中にコマンドを起動してはならない"))
    monkeypatch.setattr(qg.subprocess, "run", spy)
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    assert _run_main(monkeypatch, event, capsys) is None
    spy.assert_not_called()


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


def test_resolve_commands_untrusted_never_invokes_subprocess(monkeypatch, tmp_path):
    """0.8.0 回帰ピン: 未承認では自動検出のコマンド解決自体で外部プロセスを起動しない。

    実行ファイルの有無(shutil.which)を確認するだけでも外部プロセス起動には当たらないが、
    念のため where subprocess.run が一切呼ばれないことを spy で固定する
    (返り値のコマンド一覧が空であることのチェックだけでは、将来の変更で
    「一覧は空だが起動はしてしまう」経路が紛れ込むのを検出できない)。
    """
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    spy = mock.Mock(
        side_effect=AssertionError("resolve_commands中にsubprocessを起動してはならない")
    )
    monkeypatch.setattr(qg.subprocess, "run", spy)
    got = qg.resolve_commands("a.py", {"commands": {}}, str(tmp_path), trusted=False)
    assert got == []
    spy.assert_not_called()


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


def test_main_trusted_project_without_markers_emits_no_autodetect_notice(
    monkeypatch, tmp_path, capsys
):
    """承認済みでもマーカーファイルが無ければコマンドは空になるが、これは承認とは無関係
    (自動検出の対象外)なので `notify_autodetect_skipped` の通知を出してはならない。"""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("マーカーが無いのに外部コマンドを起動してはならない")
    ))
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    msg = (out or {}).get("systemMessage", "")
    assert "未承認のため" not in msg


def test_main_subdirectory_cwd_anchors_on_project_root(monkeypatch, tmp_path, capsys):
    """D1/D4 回帰: 基準の解決を config へ一本化した後も、サブディレクトリで編集した
    ファイルはプロジェクトルートのマーカーで検出され、ルートを実行ディレクトリにする。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    target = sub / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", root)
    seen = {}
    monkeypatch.setattr(
        qg, "run_checks", lambda cmds, anchor: seen.update(cmds=cmds, anchor=anchor) or []
    )
    event = {"tool_name": "Write", "cwd": str(sub), "tool_input": {"file_path": str(target)}}
    _run_main(monkeypatch, event, capsys)
    assert seen["cmds"] == [f"ruff check {shlex.quote(str(target))}"]
    assert seen["anchor"] == str(root)


# --- レビュー ラウンド1 修正の回帰(0.8.0) ---


def test_main_cwd_non_str_degrades_visibly_without_crashing(monkeypatch, tmp_path, capsys):
    """I1 回帰: cwd が str でなくても exit 0 と可視のメッセージで終わる(traceback で
    exit 1 になってはならない)。

    ブランチレビュー I-1 の修正で基準ディレクトリの解決が `config.load_config` の 1 か所に
    まとまったため、この型不正は load_config の fail-safe(既定値へ縮退 + `_errors`)が
    吸収する。文面は fail_open ではなく設定エラー通知になるが、守るべき契約
    (exit 0・可視・自動検出を走らせない)は同じ。
    """
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("cwd が不正なときに外部コマンドを起動してはならない")
    ))
    event = {"tool_name": "Write", "cwd": 123, "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out is not None
    assert "既定値" in out["systemMessage"] or "異常終了" in out["systemMessage"]
    assert "Traceback" not in json.dumps(out)


def test_main_cwd_null_does_not_crash(monkeypatch, tmp_path, capsys):
    """I1 回帰: cwd が null でも exit 0 で処理できる(traceback を出さない)。"""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {"tool_name": "Write", "cwd": None, "tool_input": {"file_path": str(target)}}
    # 例外で SystemExit が上がらず終了コード以外で落ちないことを確認する。
    out = _run_main(monkeypatch, event, capsys)
    assert out is None or "Traceback" not in json.dumps(out)


def test_main_file_outside_approved_root_blocks_autodetect(monkeypatch, tmp_path, capsys):
    """I2(a) 回帰: cwd は承認済みだが、file_path が cwd 配下ですらない未承認ディレクトリを
    指す場合、自動検出コマンドを起動してはならない(subprocess.run をスパイして確認)。"""
    approved = tmp_path / "P"
    approved.mkdir()
    (approved / "package.json").write_text("{}", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", approved)
    untrusted = tmp_path / "U"
    untrusted.mkdir()
    target = untrusted / "a.js"
    target.write_text("const x = 1\n", encoding="utf-8")
    spy = mock.Mock(side_effect=AssertionError("root外のfile_pathでコマンドを起動してはならない"))
    monkeypatch.setattr(qg.subprocess, "run", spy)
    event = {"tool_name": "Write", "cwd": str(approved),
             "tool_input": {"file_path": str(target)}}
    _run_main(monkeypatch, event, capsys)
    spy.assert_not_called()


def test_main_nested_untrusted_clone_without_env_is_blocked(monkeypatch, tmp_path, capsys):
    """基準線: CLAUDE_PROJECT_DIR が無ければ、ネストした未承認クローン自身が基準になり
    未承認としてブロックされる(既存動作。(b) の対照)。"""
    anc = tmp_path / "anc"
    anc.mkdir()
    (anc / "pyproject.toml").write_text("", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", anc)
    child = anc / "child"
    (child / ".git").mkdir(parents=True)
    target = child / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    spy = mock.Mock(
        side_effect=AssertionError("ネストした未承認クローンでコマンドを起動してはならない")
    )
    monkeypatch.setattr(qg.subprocess, "run", spy)
    event = {"tool_name": "Write", "cwd": str(child), "tool_input": {"file_path": str(target)}}
    _run_main(monkeypatch, event, capsys)
    spy.assert_not_called()


def test_main_nested_untrusted_clone_under_env_elevated_root_is_blocked(
    monkeypatch, tmp_path, capsys
):
    """I2(b) 回帰: CLAUDE_PROJECT_DIR で承認済みの祖先へ基準を持ち上げても、自前の `.git`
    を持つ未承認のネストしたクローンでは自動検出を起動してはならない(subprocess.run を
    スパイして確認)。"""
    anc = tmp_path / "anc"
    anc.mkdir()
    (anc / "pyproject.toml").write_text("", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", anc)
    child = anc / "child"
    (child / ".git").mkdir(parents=True)
    target = child / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(anc))
    spy = mock.Mock(
        side_effect=AssertionError("env で持ち上げた祖先配下の未承認クローンで起動してはならない")
    )
    monkeypatch.setattr(qg.subprocess, "run", spy)
    event = {"tool_name": "Write", "cwd": str(child), "tool_input": {"file_path": str(target)}}
    _run_main(monkeypatch, event, capsys)
    spy.assert_not_called()


def test_main_untrusted_no_markers_emits_no_notice(monkeypatch, tmp_path, capsys):
    """I3 回帰: 未承認プロジェクトでもマーカーファイルが1つも無ければ、承認しても何も
    変わらないので通知を出してはならない。"""
    target = tmp_path / "README.md"
    target.write_text("# hi\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    msg = (out or {}).get("systemMessage", "")
    assert "未承認のため" not in msg


def test_main_untrusted_no_markers_with_unrelated_global_commands_emits_no_notice(
    monkeypatch, tmp_path, capsys
):
    """I3 回帰: グローバル設定の commands が編集対象に一致しない場合も、マーカーが無ければ
    通知を出してはならない(レビュアの2件目の実証)。"""
    (tmp_path / "global.json").write_text(
        json.dumps({"quality_gate": {"commands": {"*.py": ["echo {file}"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "global.json")
    target = tmp_path / "README.md"
    target.write_text("# hi\n", encoding="utf-8")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    msg = (out or {}).get("systemMessage", "")
    assert "未承認のため" not in msg


def test_main_untrusted_with_markers_still_emits_notice(monkeypatch, tmp_path, capsys):
    """I3 対照: 未承認かつマーカーがあれば(承認すれば自動検出が動く場面)通知は出る。"""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out is not None and "未承認のため" in out["systemMessage"]


def test_would_autodetect_false_without_marker(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert qg.would_autodetect(str(tmp_path / "a.py"), str(tmp_path)) is False


def test_would_autodetect_true_with_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert qg.would_autodetect(str(tmp_path / "a.py"), str(tmp_path)) is True


def test_would_autodetect_false_outside_root(monkeypatch, tmp_path):
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    root = tmp_path / "root"
    root.mkdir()
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    outside = tmp_path / "outside" / "a.py"
    outside.parent.mkdir()
    outside.write_text("x = 1\n", encoding="utf-8")
    assert qg.would_autodetect(str(outside), str(root)) is False


def test_in_trusted_scope_true_for_plain_subdirectory(tmp_path):
    root = tmp_path / "root"
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    assert qg._in_trusted_scope(str(sub / "app.py"), str(root)) is True


def test_in_trusted_scope_false_for_unrelated_directory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    assert qg._in_trusted_scope(str(other / "app.py"), str(root)) is False


def test_in_trusted_scope_false_when_nested_git_boundary_crossed(tmp_path):
    root = tmp_path / "root"
    nested = root / "vendor" / "clone"
    (nested / ".git").mkdir(parents=True)
    target = nested / "app.py"
    assert qg._in_trusted_scope(str(target), str(root)) is False


def test_in_trusted_scope_true_when_root_itself_has_git(tmp_path):
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    assert qg._in_trusted_scope(str(sub / "app.py"), str(root)) is True


# --- submodule 相当の境界(0.8.0 レビュー ラウンド2 追記) ---
# git submodule の `.git` はディレクトリでなくファイル(`gitdir: ...` を指す)である。
# `_crosses_nested_repo` は `.exists()` で判定するため file/dir を区別しないはずだが、
# それを単体レベルと黒箱レベルの両方で固定する(既存はディレクトリの `.git` しか
# 使っておらず、file の場合の回帰を検出できなかった)。


def test_in_trusted_scope_false_when_git_is_a_file_like_submodule(tmp_path):
    """submodule 相当: `.git` がディレクトリでなくファイルでも境界として扱う。"""
    root = tmp_path / "root"
    sub = root / "vendor" / "sub"
    sub.mkdir(parents=True)
    (sub / ".git").write_text("gitdir: ../../.git/modules/sub\n", encoding="utf-8")
    target = sub / "app.py"
    assert qg._in_trusted_scope(str(target), str(root)) is False


def test_main_submodule_blocks_autodetect_and_emits_no_notice(monkeypatch, tmp_path, capsys):
    """submodule 回帰(黒箱・両方向固定):承認済み root 配下の submodule
    (`.git` ファイルを持つディレクトリ)内のファイルを編集しても、(1) 自動検出コマンドは
    一切起動せず(subprocess.run をスパイして確認)、(2) 『未承認のため』の通知も出ない
    (fail-safe だが無言。docs/security-model.md の既知の限界に明記する挙動)。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", root)
    sub = root / "vendor" / "sub"
    sub.mkdir(parents=True)
    (sub / ".git").write_text("gitdir: ../../.git/modules/sub\n", encoding="utf-8")
    target = sub / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    spy = mock.Mock(side_effect=AssertionError("submodule内でコマンドを起動してはならない"))
    monkeypatch.setattr(qg.subprocess, "run", spy)
    event = {"tool_name": "Write", "cwd": str(root), "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    spy.assert_not_called()
    msg = (out or {}).get("systemMessage", "")
    assert "未承認のため" not in msg


# --- 最終ブランチレビュー(0.8.0)の修正の回帰 ---


def _make_repo(path, marker="package.json", body="{}"):
    (path / ".git").mkdir(parents=True)
    (path / marker).write_text(body, encoding="utf-8")
    return path


def test_main_missing_cwd_does_not_lint_outside_env_root(monkeypatch, tmp_path, capsys):
    """I-1 回帰: event に cwd キーが無いとき、承認キー(config が使った基準)とアンカーが
    分岐して、承認済みプロジェクトの名義で未承認クローンのファイルを lint してはならない。

    `CLAUDE_PROJECT_DIR` は承認済み P を指すが、フックプロセスの cwd は未承認クローン U。
    0.7.2 の祖先制約は比較対象の cwd が無いと働かないため、config 側の基準は検証されない
    まま P になる。main が自分で基準を再計算すると U になり、`_in_trusted_scope` が
    U 基準で評価されて `npx eslint U/evil.js` が起動していた。
    """
    approved = _make_repo(tmp_path / "P")
    untrusted = _make_repo(tmp_path / "U")
    target = untrusted / "evil.js"
    target.write_text("const x = 1\n", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", approved)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(approved))
    monkeypatch.chdir(untrusted)
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    spy = mock.Mock(side_effect=AssertionError("未承認クローンでコマンドを起動してはならない"))
    monkeypatch.setattr(qg.subprocess, "run", spy)
    event = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
    _run_main(monkeypatch, event, capsys)
    spy.assert_not_called()


def test_main_null_cwd_does_not_lint_outside_env_root(monkeypatch, tmp_path, capsys):
    """I-1 回帰: `cwd: null` でも同じ(欠落と null は同じ経路に落ちる)。"""
    approved = _make_repo(tmp_path / "P")
    untrusted = _make_repo(tmp_path / "U")
    target = untrusted / "evil.js"
    target.write_text("const x = 1\n", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", approved)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(approved))
    monkeypatch.chdir(untrusted)
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    spy = mock.Mock(side_effect=AssertionError("未承認クローンでコマンドを起動してはならない"))
    monkeypatch.setattr(qg.subprocess, "run", spy)
    event = {"tool_name": "Write", "cwd": None, "tool_input": {"file_path": str(target)}}
    _run_main(monkeypatch, event, capsys)
    spy.assert_not_called()


def test_main_uses_config_project_root_not_a_recomputed_one(monkeypatch, tmp_path, capsys):
    """I-1 の構造的な固定: main は `_project_root` をそのまま使い、再計算しない。"""
    root = _make_repo(tmp_path / "root", marker="pyproject.toml", body="[project]\nname='x'\n")
    target = root / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", root)
    real = config.project_root
    calls = []
    monkeypatch.setattr(
        config, "project_root", lambda *a, **k: (calls.append(a), real(*a, **k))[1]
    )
    seen = {}
    monkeypatch.setattr(qg, "run_checks", lambda cmds, anchor: seen.update(anchor=anchor) or [])
    event = {"tool_name": "Write", "cwd": str(root), "tool_input": {"file_path": str(target)}}
    _run_main(monkeypatch, event, capsys)
    # 1 イベントにつき解決は 1 回だけ(以前は config / main / resolve_commands / run_checks で
    # 最大 3 回。承認キーとアンカーが別々に導出される余地をここで塞ぐ)。
    assert len(calls) == 1, calls
    assert seen["anchor"] == str(root)


def test_main_denied_project_gets_no_autodetect_notice(monkeypatch, tmp_path, capsys):
    """I-2 回帰: 明示的に `false`(不承認)にしたプロジェクトには承認を催促しない。

    0.7.0 の `_gate` は `denied` で沈黙する(docs/configuration.md にも明記)。同じ規約を
    自動検出スキップ通知にも適用する。以前は最も緩い `true` をクールダウンごとに勧めていた。
    """
    root = _make_repo(tmp_path / "root", marker="pyproject.toml", body="[project]\nname='x'\n")
    target = root / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    global_path = tmp_path / "global.json"
    global_path.write_text(
        json.dumps({"trusted_projects": {os.path.realpath(str(root)): False}}), encoding="utf-8"
    )
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", global_path)
    spy = mock.Mock(side_effect=AssertionError("不承認プロジェクトでコマンドを起動してはならない"))
    monkeypatch.setattr(qg.subprocess, "run", spy)
    event = {"tool_name": "Write", "cwd": str(root), "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    spy.assert_not_called()
    assert out is None or "未承認のため" not in (out.get("systemMessage") or "")


def test_main_unregistered_project_still_gets_autodetect_notice(monkeypatch, tmp_path, capsys):
    """I-2 の逆方向: 未登録(`denied` ではない)は従来どおり通知する。"""
    root = _make_repo(tmp_path / "root", marker="pyproject.toml", body="[project]\nname='x'\n")
    target = root / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    event = {"tool_name": "Write", "cwd": str(root), "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out is not None and "未承認のため" in out["systemMessage"]


def test_main_untrusted_clone_shows_exactly_one_approval_entry(monkeypatch, tmp_path, capsys):
    """I-3 回帰: 未承認 clone(`.claude-hooks.json` あり)で貼り付け用の行が 1 本だけになる。

    以前は自動検出通知の `"key": true` と未承認設定通知の `"key": "sha256:…"` が同じ
    systemMessage に並び、同一キーに矛盾する 2 つの値を提示していた(しかも弱い方が先)。
    """
    root = _make_repo(tmp_path / "root", marker="pyproject.toml", body="[project]\nname='x'\n")
    (root / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {"mode": "warn"}}), encoding="utf-8"
    )
    target = root / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    event = {"tool_name": "Write", "cwd": str(root), "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    msg = out["systemMessage"]
    key = os.path.realpath(str(root))
    paste_lines = [ln for ln in msg.splitlines() if ln.startswith(f'  "{key}": ')]
    assert len(paste_lines) == 1, msg
    assert paste_lines[0].startswith(f'  "{key}": "sha256:')
    assert f'"{key}": true' not in msg
    # 自動検出をスキップした事実自体は残す(理由が消えてはならない)
    assert "未承認のため" in msg


def test_main_untrusted_without_config_file_still_offers_true(monkeypatch, tmp_path, capsys):
    """I-3 の逆方向: `.claude-hooks.json` が無ければピン留めする内容が存在しないので、
    従来どおり `true` の貼り付け行を出す(唯一の指示なので矛盾しない)。"""
    root = _make_repo(tmp_path / "root", marker="pyproject.toml", body="[project]\nname='x'\n")
    target = root / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    event = {"tool_name": "Write", "cwd": str(root), "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    key = os.path.realpath(str(root))
    assert f'  "{key}": true' in out["systemMessage"]


# --- 信頼境界の変異ラチェット向けの契約テスト(M-5) ---


def test_resolve_commands_accumulates_all_matching_patterns(tmp_path):
    """複数の glob が同じファイルに一致したら、全部のコマンドを積む(上書きしない)。"""
    cfg = {"commands": {"*.py": ["first {file}"], "a*": ["second {file}"]}}
    got = qg.resolve_commands("a.py", cfg, str(tmp_path), trusted=False)
    assert sorted(got) == ["first a.py", "second a.py"]


@pytest.mark.parametrize("name", ["a.js", "a.jsx", "a.ts", "a.tsx"])
def test_resolve_commands_autodetect_matches_each_alternate_extension(tmp_path, monkeypatch, name):
    """`|` 区切りの複数拡張子がすべて一致する(区切りを落とすと 1 つも一致しない)。

    併せて、AUTO_DETECT の走査が先頭エントリ(`*.py`)の不一致で打ち切られないことも固定する。
    """
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    got = qg.resolve_commands(name, {"commands": {}}, str(tmp_path), trusted=True)
    assert got == [f"npx --no-install eslint {name}"]


@pytest.mark.parametrize("name", ["a.js", "a.jsx", "a.ts", "a.tsx"])
def test_would_autodetect_matches_each_alternate_extension(tmp_path, monkeypatch, name):
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert qg.would_autodetect(name, str(tmp_path)) is True


def test_crosses_nested_repo_true_when_walk_reaches_filesystem_root(tmp_path):
    """root へ到達せずファイルシステムの根まで遡ったら「境界を越えた」と扱う(安全側)。

    root_r が file_dir の祖先でないときに呼ばれると起きる。ここで False に倒すと
    無限ループか、承認外ディレクトリを承認済みとして扱うかのどちらかになる。
    """
    assert qg._crosses_nested_repo(str(tmp_path), str(tmp_path / "not-an-ancestor")) is True


def test_crosses_nested_repo_true_when_stat_raises(tmp_path, monkeypatch):
    """`.git` の存在確認が OSError(権限等)になったら安全側(境界あり)に倒す。"""
    class _Boom:
        def __truediv__(self, other):
            raise OSError("boom")

    monkeypatch.setattr(qg, "Path", lambda *a, **k: _Boom())
    assert qg._crosses_nested_repo("/a/b", "/a") is True


def test_in_trusted_scope_false_when_realpath_raises(monkeypatch):
    """realpath が OSError を出したら安全側(境界外)に倒す。"""
    def boom(_path):
        raise OSError("boom")

    monkeypatch.setattr(qg.os.path, "realpath", boom)
    assert qg._in_trusted_scope("/a/b/app.py", "/a/b") is False


def test_main_reports_every_failing_command_separated(monkeypatch, tmp_path, capsys):
    """複数コマンドが失敗したら全部を空行区切りで提示する(1 本目で上書きしない)。"""
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"quality_gate": {"commands": {"*.py": ["false one", "false two"]}}}),
        encoding="utf-8",
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(qg, "run_checks", lambda cmds, anchor: ["$ one\nNG1", "$ two\nNG2"])
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    assert "$ one\nNG1\n\n$ two\nNG2" in out["reason"]


def test_main_fail_open_names_the_hook_and_the_exception(monkeypatch, tmp_path, capsys):
    """try の内側で想定外の例外が出たら fail-open。フック名と例外を必ず可視化する。"""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)

    def boom(*a, **k):
        raise RuntimeError("設計外の異常")

    monkeypatch.setattr(qg, "resolve_commands", boom)
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    assert "quality_gate が異常終了したため検査をスキップしました" in out["systemMessage"]
    assert "設計外の異常" in out["systemMessage"]


def test_main_autodetect_notice_respects_configured_cooldown(monkeypatch, tmp_path, capsys):
    """`notice_cooldown_sec` が自動検出通知にも効く(既定値へ読み替えない)。"""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"notice_cooldown_sec": 0}), encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", global_path)
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(target)}}
    first = _run_main(monkeypatch, event, capsys)
    second = _run_main(monkeypatch, event, capsys)
    assert "未承認のため" in first["systemMessage"]
    assert "未承認のため" in second["systemMessage"]


# --- M-5 再レビュー: 「呼び出し側から観測できない」は等価変異の十分条件ではない ---
#
# `docs/superpowers/specs/2026-08-22-mutation-spike-results.md` の教訓どおり、
# 「常に存在するキーの既定値だから到達しない」という理由づけは、その不変条件を
# 確立しているのが別関数(`config.load_config`)・別テーブル(`AUTO_DETECT`)・
# 外部呼び出し(`subprocess.run`)であるかぎり、差し替えれば到達できるので
# 等価変異ではない。以下はその不変条件を壊して直接固定する契約テスト。


def test_main_defaults_enabled_true_and_mode_block_when_section_missing(
    monkeypatch, tmp_path, capsys
):
    """`cfg_all.get("quality_gate", ...)` と `cfg.get("enabled"/"mode", ...)` の
    既定値は `config.load_config` が確立する不変条件に依存しているだけで、
    `load_config` 自体を差し替えれば到達できる(=等価変異ではない)。

    quality_gate セクションを丸ごと欠いた `cfg_all` を返させ、それでも
    (1) 無効化されない(enabled 既定 True)
    (2) mode 既定は "warn" ではなく "block"(block/warn は利用者可視の挙動差)
    ことを、実際の decision で固定する。
    """
    monkeypatch.setattr(
        config, "load_config",
        lambda cwd=None: {"_errors": [], "_project_root": None, "_project_trusted": False},
    )
    monkeypatch.setattr(qg, "resolve_commands", lambda *a, **k: ["false"])
    monkeypatch.setattr(qg, "run_checks", lambda cmds, anchor: ["$ false\nNG"])
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    event = {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out["decision"] == "block"
    assert "NG" in out["reason"]


def test_resolve_commands_continues_past_missing_executable_in_next_entry(tmp_path, monkeypatch):
    """`shutil.which(exe) is None` の `continue` を `break` に変えると、後続の
    互いに素でない AUTO_DETECT エントリまで打ち切ってしまう。現在の表(拡張子が
    互いに素)ではこの差は観測できないので、同じ拡張子で実行ファイルの有無だけが
    違う2エントリの表に差し替えて可視化する。
    """
    monkeypatch.setattr(qg, "AUTO_DETECT", [
        ("*.py", "missing-tool-xyz", ("marker",), "missing-tool-xyz {file}"),
        ("*.py", "real-tool-xyz", ("marker",), "real-tool-xyz {file}"),
    ])
    monkeypatch.setattr(
        qg.shutil, "which",
        lambda exe: "/usr/bin/real-tool-xyz" if exe == "real-tool-xyz" else None,
    )
    (tmp_path / "marker").write_text("", encoding="utf-8")
    got = qg.resolve_commands(str(tmp_path / "a.py"), {"commands": {}}, str(tmp_path), trusted=True)
    assert got and got[0].startswith("real-tool-xyz")


def test_resolve_commands_continues_past_missing_marker_in_next_entry(tmp_path, monkeypatch):
    """前提設定ファイル不在時の `continue` を `break` に変えても同様に打ち切ってしまう。"""
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    monkeypatch.setattr(qg, "AUTO_DETECT", [
        ("*.py", "tool-a", ("missing-marker-only",), "tool-a {file}"),
        ("*.py", "tool-b", ("present-marker",), "tool-b {file}"),
    ])
    (tmp_path / "present-marker").write_text("", encoding="utf-8")
    got = qg.resolve_commands(str(tmp_path / "a.py"), {"commands": {}}, str(tmp_path), trusted=True)
    assert got and got[0].startswith("tool-b")


def test_run_checks_passes_exact_subprocess_kwargs(tmp_path):
    """`subprocess.run` の全 kwargs(`cwd`/`capture_output`/`text`/`timeout`)を
    スタブで捕捉し厳密一致で固定する。「呼び出し先の引数」は同一関数内で完結しない
    ので、値の変異(`text=False`)も kwargs 自体の欠落も両方この1本で kill できる。
    """
    seen = {}

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Completed()

    with mock.patch.object(qg.subprocess, "run", fake_run):
        qg.run_checks(["echo hi"], str(tmp_path))
    assert seen["kwargs"] == {
        "cwd": str(tmp_path),
        "capture_output": True,
        "text": True,
        "timeout": qg.COMMAND_TIMEOUT_SEC,
    }


def test_run_checks_truncates_output_to_exactly_output_tail_chars(tmp_path):
    """`[-OUTPUT_TAIL_CHARS:]` を削除・符号反転する変異を、末尾長そのものの検査で固定する。"""
    long_output = "A" * 3000 + "TAIL_END_MARKER"

    class _Completed:
        returncode = 1
        stdout = long_output
        stderr = ""

    with mock.patch.object(qg.subprocess, "run", lambda *a, **k: _Completed()):
        failures = qg.run_checks(["fakecmd"], str(tmp_path))
    assert len(failures) == 1
    tail = failures[0].split("\n", 1)[1]
    assert tail == long_output[-qg.OUTPUT_TAIL_CHARS:]
    assert len(tail) == qg.OUTPUT_TAIL_CHARS
    assert "TAIL_END_MARKER" in tail


def test_main_joins_multiple_autodetect_notices_with_real_newline(monkeypatch, tmp_path, capsys):
    """`msg = "\n".join(notices)` の区切り文字変異は、`trust.notify_autodetect_skipped` が
    現行実装では常に0/1件しか返さない契約に隠れて観測できない。その契約を作っているのは
    `main` 自身ではなく呼び出し先(callee)なので、差し替えれば到達できる(=等価変異ではない)。
    """
    root = _make_repo(tmp_path / "root", marker="pyproject.toml", body="[project]\nname='x'\n")
    target = root / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(qg.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(qg.trust, "notify_autodetect_skipped", lambda *a, **k: ["one", "two"])
    event = {"tool_name": "Write", "cwd": str(root), "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out["systemMessage"] == "one\ntwo"


def test_run_checks_records_real_exception_message_and_continues(tmp_path):
    """例外発生時の `failures.append(...)` 内容と、その後の `continue`(vs `break`)を
    同時に固定する。1本目が例外・2本目が成功以外の失敗、という2コマンドで両方を可視化する。
    """
    calls = []

    class _Failed:
        returncode = 1
        stdout = "second command failed"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            raise OSError("boom-message")
        return _Failed()

    with mock.patch.object(qg.subprocess, "run", fake_run):
        failures = qg.run_checks(["cmd-one", "cmd-two"], str(tmp_path))
    assert len(failures) == 2
    assert "実行できませんでした: boom-message" in failures[0]
    assert "second command failed" in failures[1]
