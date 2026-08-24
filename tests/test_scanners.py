import json
import os

from hooks.lib import scanners

# --- _resolve_config_path(純関数) ---

def test_resolve_config_path_returns_candidate_str(tmp_path):
    p = tmp_path / ".gitleaks.toml"
    p.write_text("", encoding="utf-8")
    result = scanners._resolve_config_path({}, str(tmp_path))
    assert result == str(p)


def test_resolve_config_path_uses_project_root_from_subdirectory(tmp_path):
    """回帰: cwd がサブディレクトリでもプロジェクトルートの .gitleaks.toml を使う。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    (root / ".gitleaks.toml").write_text("", encoding="utf-8")
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    result = scanners._resolve_config_path({}, str(sub))
    assert result == str(root / ".gitleaks.toml")


def test_resolve_config_path_explicit_still_wins_from_subdirectory(tmp_path):
    """明示指定(gitleaks_config)は project_root 基準へ変えても従来どおり優先される。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    (root / ".gitleaks.toml").write_text("", encoding="utf-8")
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("", encoding="utf-8")
    result = scanners._resolve_config_path({"gitleaks_config": str(explicit)}, str(sub))
    assert result == str(explicit)


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


def test_argv_docker_flag_shaped_image_separated(monkeypatch):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/usr/bin/docker" if n == "docker" else None)
    argv = scanners._gitleaks_argv(
        {"gitleaks": "docker", "gitleaks_image": "--privileged"}, None)
    assert "--" in argv
    assert argv[argv.index("--") + 1] == "--privileged"


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


def test_argv_default_mode_is_auto_when_key_absent(monkeypatch):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/usr/bin/gitleaks" if n == "gitleaks" else None)
    argv = scanners._gitleaks_argv({}, None)
    assert argv is not None
    assert argv[0] == "gitleaks"


def test_argv_off_does_not_resolve_config(monkeypatch, tmp_path):
    def boom(sc, cwd):
        raise AssertionError("gitleaks=off のとき _resolve_config_path を呼んではいけない")
    monkeypatch.setattr(scanners, "_resolve_config_path", boom)
    assert scanners._gitleaks_argv({"gitleaks": "off"}, str(tmp_path)) is None


def test_argv_auto_with_config_exact_list(monkeypatch, tmp_path):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/usr/bin/gitleaks" if n == "gitleaks" else None)
    p = tmp_path / "gl.toml"
    p.write_text("", encoding="utf-8")
    argv = scanners._gitleaks_argv({"gitleaks": "auto", "gitleaks_config": str(p)}, None)
    assert argv == ["gitleaks", *scanners._COMMON_FLAGS, "-c", str(p)]


def test_argv_docker_with_config_exact_list(monkeypatch, tmp_path):
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/usr/bin/docker" if n == "docker" else None)
    p = tmp_path / "gl.toml"
    p.write_text("", encoding="utf-8")
    argv = scanners._gitleaks_argv({"gitleaks": "docker", "gitleaks_config": str(p)}, None)
    expected = [
        "docker", "run", "--rm", "-i",
        "-v", f"{os.path.abspath(str(p))}:/tmp/gl.toml:ro",
        "--", scanners.DEFAULT_IMAGE, *scanners._COMMON_FLAGS, "-c", "/tmp/gl.toml",
    ]
    assert argv == expected


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


def test_run_gitleaks_calls_subprocess_with_expected_kwargs(monkeypatch):
    captured = {}

    class FakeResult:
        returncode = 0
        stdout = "[]"

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeResult()

    monkeypatch.setattr(scanners.subprocess, "run", fake_run)
    scanners._run_gitleaks(["gitleaks", "stdin"], "some-input-text")
    assert captured["argv"] == ["gitleaks", "stdin"]
    assert captured["input"] == "some-input-text"
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == scanners.GITLEAKS_TIMEOUT_SEC


def test_run_gitleaks_skips_non_dict_entries_but_continues(tmp_path):
    payload = json.dumps([
        "not-a-dict",
        {"RuleID": "generic-api-key", "Secret": "AFTER-VALUE"},
    ])
    stub = _make_stub(tmp_path, payload, 1)
    out = scanners._run_gitleaks([stub], "x")
    assert out == [{"rule": "gitleaks:generic-api-key", "match": "AFTER-VALUE"}]


def test_run_gitleaks_requires_rule_id_and_secret(tmp_path):
    payload = json.dumps([
        {"RuleID": "only-rule"},
        {"Secret": "only-secret"},
        {"RuleID": "both-rule", "Secret": "both-secret"},
    ])
    stub = _make_stub(tmp_path, payload, 1)
    out = scanners._run_gitleaks([stub], "x")
    assert out == [{"rule": "gitleaks:both-rule", "match": "both-secret"}]


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


def test_scan_secrets_passes_cwd_argv_text_through(monkeypatch):
    captured = {}

    def fake_gitleaks_argv(sc, cwd):
        captured["cwd"] = cwd
        return ["FAKE_ARGV"]

    def fake_run_gitleaks(argv, text):
        captured["argv"] = argv
        captured["text"] = text
        return []

    monkeypatch.setattr(scanners, "_gitleaks_argv", fake_gitleaks_argv)
    monkeypatch.setattr(scanners, "_run_gitleaks", fake_run_gitleaks)
    scanners.scan_secrets("hello-world-text", {"gitleaks": "auto"}, "/some/cwd")
    assert captured["cwd"] == "/some/cwd"
    assert captured["argv"] == ["FAKE_ARGV"]
    assert captured["text"] == "hello-world-text"


def test_scan_secrets_dedup(monkeypatch):
    akia = "AKIA" + "Z" * 16
    monkeypatch.setattr(scanners.shutil, "which",
                        lambda n, *a, **k: "/x" if n == "gitleaks" else None)
    monkeypatch.setattr(scanners, "_run_gitleaks",
                        lambda argv, text: [{"rule": "aws-access-key", "match": akia}])
    out = scanners.scan_secrets(f"key={akia}", {"gitleaks": "auto"}, None)
    assert sum(1 for f in out if f["match"] == akia) == 1
