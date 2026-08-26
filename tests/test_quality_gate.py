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


def test_autodetect_finds_marker_from_project_root_in_subdirectory(tmp_path, monkeypatch):
    """回帰: cwd がサブディレクトリでもプロジェクトルートのマーカーファイルで検出する。"""
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    got = qg.resolve_commands(str(sub / "app.py"), {"commands": {}}, str(sub), trusted=True)
    assert got and got[0].startswith("ruff check")


def test_autodetect_ignores_marker_outside_project_root(tmp_path, monkeypatch):
    """正当な挙動の保持: プロジェクトルート外(無関係な祖先)のマーカーは拾わない。"""
    monkeypatch.setattr(qg.shutil, "which", lambda exe: "/usr/bin/" + exe)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    got = qg.resolve_commands(str(sub / "app.py"), {"commands": {}}, str(sub), trusted=True)
    assert got == []


def test_run_checks_executes_in_project_root_not_subdirectory(tmp_path):
    """D4: 実行ディレクトリもプロジェクトルート基準になる。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    stub = tmp_path / "record_cwd.py"
    stub.write_text(
        "import os, sys\n"
        "open(sys.argv[1], 'w', encoding='utf-8').write(os.getcwd())\n",
        encoding="utf-8",
    )
    marker = tmp_path / "cwd-marker.txt"
    qg.run_checks([f"python3 {stub} {marker}"], str(sub))
    assert marker.read_text(encoding="utf-8") == os.path.realpath(str(root))


def test_run_checks_uses_cwd_when_no_project_root_found(tmp_path):
    """正当な挙動の保持: git ルートが無ければ従来どおり cwd で実行する。"""
    stub = tmp_path / "record_cwd.py"
    stub.write_text(
        "import os, sys\n"
        "open(sys.argv[1], 'w', encoding='utf-8').write(os.getcwd())\n",
        encoding="utf-8",
    )
    marker = tmp_path / "cwd-marker.txt"
    qg.run_checks([f"python3 {stub} {marker}"], str(tmp_path))
    assert marker.read_text(encoding="utf-8") == os.path.realpath(str(tmp_path))


def _run_main(monkeypatch, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit):
        qg.main()
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
    assert "additionalContext" in out["hookSpecificOutput"]


def test_main_skips_missing_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {
        "tool_name": "Write", "cwd": str(tmp_path),
        "tool_input": {"file_path": str(tmp_path / "gone.py")},
    }
    assert _run_main(monkeypatch, event, capsys) is None


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


# --- レビュー ラウンド1 修正の回帰(0.8.0) ---


def test_main_cwd_non_str_fails_open(monkeypatch, tmp_path, capsys):
    """I1 回帰: cwd が str でなくても fail_open(exit 0, systemMessage)で終わる(traceback で
    exit 1 になってはならない)。"""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    event = {"tool_name": "Write", "cwd": 123, "tool_input": {"file_path": str(target)}}
    out = _run_main(monkeypatch, event, capsys)
    assert out is not None
    assert "異常終了" in out["systemMessage"]


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
