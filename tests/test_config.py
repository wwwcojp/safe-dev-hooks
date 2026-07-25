import json
import subprocess
import sys
from pathlib import Path

from hooks.lib import config


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
    proj = tmp_path / ".claude-hooks.json"
    proj.write_text("{broken", encoding="utf-8")
    cfg = config.load_config(str(tmp_path))
    assert len(cfg["_errors"]) == 1
    assert cfg["_errors"][0].startswith(f"{proj}: ")
    assert cfg["exfil_guard"]["mode"] == "detect"


def test_broken_global_json_does_not_block_project_layer(monkeypatch, tmp_path):
    g = tmp_path / "global.json"
    g.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", g)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8"
    )
    cfg = config.load_config(str(proj))
    # continue でなく break だと、global のエラー後に project 層の読み込みがスキップされる
    assert cfg["exfil_guard"]["mode"] == "always"
    assert len(cfg["_errors"]) == 1


def test_non_dict_config_records_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = tmp_path / ".claude-hooks.json"
    proj.write_text("[1,2]", encoding="utf-8")
    cfg = config.load_config(str(tmp_path))
    assert cfg["_errors"] == [f"{proj}: オブジェクトではありません"]


def test_non_dict_global_config_does_not_block_project_layer(monkeypatch, tmp_path):
    g = tmp_path / "global.json"
    g.write_text("[1,2]", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", g)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8"
    )
    cfg = config.load_config(str(proj))
    # continue でなく break だと、global のエラー後に project 層の読み込みがスキップされる
    assert cfg["exfil_guard"]["mode"] == "always"
    assert len(cfg["_errors"]) == 1


def test_load_config_default_cwd_uses_current_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    cfg = config.load_config()
    assert cfg["exfil_guard"]["mode"] == "always"


def test_load_config_reads_project_file_with_utf8_encoding(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = tmp_path / ".claude-hooks.json"
    proj.write_text(json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8")
    seen_encodings = []
    orig_read_text = Path.read_text

    def spy_read_text(self, *args, **kwargs):
        if self == proj:
            seen_encodings.append(kwargs.get("encoding"))
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read_text)
    config.load_config(str(tmp_path))
    assert seen_encodings == ["utf-8"]


def test_load_config_returns_independent_copy_of_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["audit_log"] is not config.DEFAULTS["audit_log"]
    cfg["audit_log"]["path"] = "/mutated/path"
    assert config.DEFAULTS["audit_log"]["path"] == ".claude/logs"


def test_type_mismatch_reset_does_not_alias_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": "not-a-dict"}), encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["exfil_guard"] == config.DEFAULTS["exfil_guard"]
    assert cfg["exfil_guard"]["categories"] is not config.DEFAULTS["exfil_guard"]["categories"]
    cfg["exfil_guard"]["categories"]["custom"] = "mutated"
    assert config.DEFAULTS["exfil_guard"]["categories"]["custom"] == "ask"


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
    assert cfg["_errors"] == [
        "exfil_guard.mode: 未知の値 'detct' のため既定値を使用します",
        "exfil_guard.categories.credentials: 未知の値 'denny' のため既定値を使用します",
    ]


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
    assert cfg["_errors"] == [
        "bash_guard.protected_branches: 文字列リストでないため既定値を使用します"
    ]


def test_write_protected_paths_invalid_type_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        '{"secrets_guard": {"write_protected_paths": "x"}}', encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["secrets_guard"]["write_protected_paths"] == []
    assert cfg["_errors"] == [
        "secrets_guard.write_protected_paths: 文字列リストでないため既定値を使用します"
    ]


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
    assert cfg["_errors"] == [
        "scanners.gitleaks_config: 文字列またはnullでないため既定値を使用します"
    ]


def test_scanners_gitleaks_image_type_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        '{"scanners": {"gitleaks_image": 123}}', encoding="utf-8"
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks_image"] == config.DEFAULTS["scanners"]["gitleaks_image"]
    assert cfg["_errors"] == [
        "scanners.gitleaks_image: 文字列でないため既定値を使用します"
    ]


def test_invalid_utf8_config_records_error_and_keeps_defaults(monkeypatch, tmp_path):
    """不正UTF-8の設定ファイルでも例外を出さず既定値で継続する。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_bytes(b"\xff{}")
    cfg = config.load_config(str(tmp_path))
    assert cfg["bash_guard"]["enabled"] is True
    assert cfg["secrets_guard"]["enabled"] is True
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert len(cfg["_errors"]) == 1


def test_invalid_utf8_global_config_records_error_and_keeps_defaults(monkeypatch, tmp_path):
    """グローバル設定側が不正UTF-8でも同様(読込は2層とも同じ経路)。"""
    g = tmp_path / "global.json"
    g.write_bytes(b"\xff{}")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", g)
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = config.load_config(str(proj))
    assert cfg["bash_guard"]["enabled"] is True
    assert len(cfg["_errors"]) == 1


def test_deeply_nested_json_records_error_and_keeps_defaults(monkeypatch, tmp_path):
    """再帰上限を超える深いネスト(RecursionError)でも例外を出さず既定値で継続する。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    depth = 200_000
    (tmp_path / ".claude-hooks.json").write_text("[" * depth + "]" * depth, encoding="utf-8")
    cfg = config.load_config(str(tmp_path))
    assert cfg["bash_guard"]["enabled"] is True
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"
    assert len(cfg["_errors"]) == 1


def test_non_dict_categories_falls_back_to_defaults(monkeypatch, tmp_path):
    """exfil_guard.categories がオブジェクトでない場合も落ちずに既定値へ倒す。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    for bad in ([], None, "x", 3):
        (tmp_path / ".claude-hooks.json").write_text(
            json.dumps({"exfil_guard": {"categories": bad}}), encoding="utf-8"
        )
        cfg = config.load_config(str(tmp_path))
        assert cfg["exfil_guard"]["categories"] == config.DEFAULTS["exfil_guard"]["categories"], bad
        assert any("categories" in e for e in cfg["_errors"]), bad


def test_load_config_never_raises_on_unexpected_error(monkeypatch, tmp_path):
    """想定外の内部エラーでもガードを死なせず既定値へフォールバックする。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "bell"}}), encoding="utf-8"
    )

    def boom(*args, **kwargs):
        raise RuntimeError("想定外")

    monkeypatch.setattr(config, "_merge", boom)
    cfg = config.load_config(str(tmp_path))
    assert cfg["notify"]["method"] == "auto"
    assert cfg["bash_guard"]["enabled"] is True
    assert len(cfg["_errors"]) == 1


def test_valid_config_still_applies(monkeypatch, tmp_path):
    """正常な設定(categories上書き・マルチバイト文字)は従来どおり反映され警告も出ない。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps(
            {
                "exfil_guard": {"categories": {"pii": "deny", "semantic": "off"}},
                "notify": {"command": "notify-send 通知"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["exfil_guard"]["categories"]["pii"] == "deny"
    assert cfg["exfil_guard"]["categories"]["semantic"] == "off"
    # 未指定のカテゴリは既定値が残る
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"
    assert cfg["notify"]["command"] == "notify-send 通知"
    assert cfg["_errors"] == []


def test_malformed_config_does_not_disable_deny_layer(tmp_path):
    """不正UTF-8の設定ファイルがあっても bash_guard の deny 層は動く(黒箱)。"""
    (tmp_path / ".claude-hooks.json").write_bytes(b"\xff{}")
    script = Path(__file__).resolve().parent.parent / "hooks" / "pre_tool_use" / "bash_guard.py"
    event = {
        "tool_name": "Bash",
        "cwd": str(tmp_path),
        "tool_input": {"command": "mkfs.ext4 /dev/sda1"},
    }
    r = subprocess.run(
        [sys.executable, str(script)], input=json.dumps(event),
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
