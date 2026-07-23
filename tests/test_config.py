import json

from lib import config


def test_defaults_when_no_files(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"
    assert cfg["quality_gate"]["mode"] == "block"
    assert cfg["secrets_scan"]["custom_patterns"] == []
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


def test_config_section_type_mismatch_resets_to_default(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text('{"audit_log": true}', encoding="utf-8")
    cfg = config.load_config(str(tmp_path))
    assert cfg["audit_log"]["enabled"] is True
    assert len(cfg["_errors"]) == 1


def test_enum_typo_falls_back_to_safe_default(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "detct", "categories": {"credentials": "denny"}}}),
        encoding="utf-8",
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"
    assert len(cfg["_errors"]) == 2


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


def test_protected_branches_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["bash_guard"]["protected_branches"] == [
        "main", "master", "develop", "release", "production"
    ]
    assert cfg["secrets_guard"]["write_protected_paths"] == []


def test_protected_branches_override(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        '{"bash_guard": {"protected_branches": ["main", "trunk"]}}', encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["bash_guard"]["protected_branches"] == ["main", "trunk"]


def test_protected_branches_invalid_type_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        '{"bash_guard": {"protected_branches": "main"}}', encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["bash_guard"]["protected_branches"] == [
        "main", "master", "develop", "release", "production"
    ]
    assert any("protected_branches" in e for e in cfg["_errors"])


def test_write_protected_paths_invalid_type_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        '{"secrets_guard": {"write_protected_paths": "x"}}', encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["secrets_guard"]["write_protected_paths"] == []
    assert any("write_protected_paths" in e for e in cfg["_errors"])


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
