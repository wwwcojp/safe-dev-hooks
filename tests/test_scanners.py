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
