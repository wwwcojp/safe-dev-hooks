import json
import os
import subprocess
import sys
from pathlib import Path

from helpers import approve_project, isolated_home_env

from hooks.lib import config, trust


def test_defaults_when_no_files(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"
    assert cfg["quality_gate"]["mode"] == "block"
    assert cfg["secrets_scan"]["custom_patterns"] == []
    assert cfg.get("_errors", []) == []


def test_project_overrides_global(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "detect", "trusted_servers": ["mcp__kb"]}}),
        encoding="utf-8",
    )
    approve_project(
        monkeypatch, tmp_path / "global.json", proj,
        global_cfg={"exfil_guard": {"mode": "always"}},
    )
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["exfil_guard"]["trusted_servers"] == ["mcp__kb"]
    # 未指定キーは既定値が残る(deepマージ)
    assert cfg["exfil_guard"]["categories"]["pii"] == "ask"


def test_broken_json_records_error_and_keeps_defaults(monkeypatch, tmp_path):
    proj = tmp_path / ".claude-hooks.json"
    proj.write_text("{broken", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert len(cfg["_errors"]) == 1
    assert cfg["_errors"][0].startswith(f"{proj}: ")
    assert cfg["exfil_guard"]["mode"] == "detect"


def test_broken_global_json_does_not_block_project_layer(monkeypatch, tmp_path):
    """壊れたグローバル設定でも project 層の処理(信頼判定)自体はスキップされない。

    0.7.0 以降、承認記録はグローバル層にしかないため、グローバルが壊れていれば
    プロジェクト層は承認しようがなく値は適用されない。continue でなく break だと
    project 層の信頼判定自体に到達しない(通知が出ない)ので、そちらを検証する。
    """
    g = tmp_path / "global.json"
    g.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", g)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8"
    )
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"  # 承認機構(global)が壊れ非承認
    assert len(cfg["_errors"]) == 1 and cfg["_errors"][0].startswith(f"{g}: ")
    assert len(cfg["_notices"]) == 1  # project 層の処理まで到達した証跡


def test_non_dict_config_records_error(monkeypatch, tmp_path):
    proj = tmp_path / ".claude-hooks.json"
    proj.write_text("[1,2]", encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["_errors"] == [f"{proj}: オブジェクトではありません"]


def test_non_dict_global_config_does_not_block_project_layer(monkeypatch, tmp_path):
    """グローバル設定がオブジェクトでなくても project 層の処理(信頼判定)は続く。

    test_broken_global_json_does_not_block_project_layer と同様、0.7.0 以降は
    承認記録がグローバル層にしかないため、グローバルが不正型なら承認しようがなく
    値は適用されない。continue でなく break だと project 層の信頼判定自体に
    到達しない(通知が出ない)ので、そちらを検証する。
    """
    g = tmp_path / "global.json"
    g.write_text("[1,2]", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", g)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8"
    )
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"  # 承認機構(global)が不正型で非承認
    assert cfg["_errors"] == [f"{g}: オブジェクトではありません"]
    assert len(cfg["_notices"]) == 1  # project 層の処理まで到達した証跡


def test_load_config_default_cwd_uses_current_directory(monkeypatch, tmp_path):
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path)
    monkeypatch.chdir(tmp_path)
    cfg = config.load_config()
    assert cfg["exfil_guard"]["mode"] == "always"


def test_load_config_reads_project_file_with_utf8_encoding(monkeypatch, tmp_path):
    """project 設定はロケール依存でなく明示的に utf-8 として解釈される(多バイト文字往復)。

    実装は Path.read_bytes() で生バイト列を読み、承認後に bytes.decode("utf-8") で
    解析する(Path.read_text(encoding=...) は経由しない)ため、エンコーディング引数を
    スパイするのではなく多バイト文字が壊れず往復することで検証する。
    """
    proj = tmp_path / ".claude-hooks.json"
    proj.write_text(
        json.dumps({"notify": {"command": "notify-send 通知"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["notify"]["command"] == "notify-send 通知"


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
    (tmp_path / ".claude-hooks.json").write_text('{"audit_log": true}', encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["audit_log"]["enabled"] is True
    assert len(cfg["_errors"]) == 1


def test_enum_typo_falls_back_to_safe_default(monkeypatch, tmp_path):
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "detct", "categories": {"credentials": "denny"}}}),
        encoding="utf-8",
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"
    assert cfg["_errors"] == [
        "exfil_guard.mode: 不正な値 'detct' のため無視しました(下位層の値を使用)",
        "exfil_guard.categories.credentials: 不正な値 'denny' のため無視しました(下位層の値を使用)",
    ]


def test_notify_method_default_and_typo_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["notify"]["method"] == "auto"
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "toast"}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
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
    (tmp_path / ".claude-hooks.json").write_text(
        '{"bash_guard": {"protected_branches": ["main", "trunk"]}}', encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["bash_guard"]["protected_branches"] == ["main", "trunk"]


def test_protected_branches_invalid_type_falls_back(tmp_path, monkeypatch):
    (tmp_path / ".claude-hooks.json").write_text(
        '{"bash_guard": {"protected_branches": "main"}}', encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["bash_guard"]["protected_branches"] == [
        "main", "master", "develop", "release", "production"
    ]
    assert cfg["_errors"] == [
        "bash_guard.protected_branches: 不正な値 'main' のため無視しました(下位層の値を使用)"
    ]


def test_write_protected_paths_invalid_type_falls_back(tmp_path, monkeypatch):
    (tmp_path / ".claude-hooks.json").write_text(
        '{"secrets_guard": {"write_protected_paths": "x"}}', encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["secrets_guard"]["write_protected_paths"] == []
    assert cfg["_errors"] == [
        "secrets_guard.write_protected_paths: 不正な値 'x' のため無視しました(下位層の値を使用)"
    ]


def test_scanners_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks"] == "auto"
    assert cfg["scanners"]["gitleaks_image"].startswith("ghcr.io/gitleaks/gitleaks:")
    assert cfg["scanners"]["gitleaks_config"] is None


def test_scanners_gitleaks_enum_fallback(monkeypatch, tmp_path):
    (tmp_path / ".claude-hooks.json").write_text(
        '{"scanners": {"gitleaks": "bogus"}}', encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks"] == "auto"
    assert any("scanners.gitleaks" in e for e in cfg["_errors"])


def test_scanners_gitleaks_docker_accepted(monkeypatch, tmp_path):
    (tmp_path / ".claude-hooks.json").write_text(
        '{"scanners": {"gitleaks": "docker"}}', encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks"] == "docker"


def test_scanners_config_type_fallback(monkeypatch, tmp_path):
    (tmp_path / ".claude-hooks.json").write_text(
        '{"scanners": {"gitleaks_config": 123}}', encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks_config"] is None
    assert cfg["_errors"] == [
        "scanners.gitleaks_config: 不正な値 123 のため無視しました(下位層の値を使用)"
    ]


def test_scanners_gitleaks_image_type_fallback(monkeypatch, tmp_path):
    (tmp_path / ".claude-hooks.json").write_text(
        '{"scanners": {"gitleaks_image": 123}}', encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["scanners"]["gitleaks_image"] == config.DEFAULTS["scanners"]["gitleaks_image"]
    assert cfg["_errors"] == [
        "scanners.gitleaks_image: 不正な値 123 のため無視しました(下位層の値を使用)"
    ]


def test_invalid_utf8_config_records_error_and_keeps_defaults(monkeypatch, tmp_path):
    """不正UTF-8の設定ファイルでも例外を出さず既定値で継続する(承認済みで解析まで到達)。"""
    (tmp_path / ".claude-hooks.json").write_bytes(b"\xff{}")
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
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
    depth = 200_000
    (tmp_path / ".claude-hooks.json").write_text("[" * depth + "]" * depth, encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["bash_guard"]["enabled"] is True
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"
    assert len(cfg["_errors"]) == 1


def test_non_dict_categories_falls_back_to_defaults(monkeypatch, tmp_path):
    """exfil_guard.categories がオブジェクトでない場合も落ちずに既定値へ倒す。"""
    for bad in ([], None, "x", 3):
        (tmp_path / ".claude-hooks.json").write_text(
            json.dumps({"exfil_guard": {"categories": bad}}), encoding="utf-8"
        )
        approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
        cfg = config.load_config(str(tmp_path))
        assert cfg["exfil_guard"]["categories"] == config.DEFAULTS["exfil_guard"]["categories"], bad
        assert any("categories" in e for e in cfg["_errors"]), bad


def test_load_config_never_raises_on_unexpected_error(monkeypatch, tmp_path):
    """想定外の内部エラーでもガードを死なせず既定値へフォールバックする。"""
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "bell"}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)

    def boom(*args, **kwargs):
        raise RuntimeError("想定外")

    monkeypatch.setattr(config, "_merge", boom)
    cfg = config.load_config(str(tmp_path))
    assert cfg["notify"]["method"] == "auto"
    assert cfg["bash_guard"]["enabled"] is True
    assert len(cfg["_errors"]) == 1


def test_valid_config_still_applies(monkeypatch, tmp_path):
    """正常な設定(categories上書き・マルチバイト文字)は従来どおり反映され警告も出ない。"""
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
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
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
        capture_output=True, text=True, timeout=30, env=isolated_home_env(tmp_path / "home"),
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "未承認のため無視しました" in out["systemMessage"]


# --- 層の縮退先(spec #5): 上位層の不正値はビルトインではなく直下の層へ戻る ---

_TYPE_CONFUSION_SHAPES = [0, "x", [], None, True, 1.5]


def _with_layers(monkeypatch, tmp_path, global_cfg, project_cfg):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    (proj / ".claude-hooks.json").write_text(json.dumps(project_cfg), encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", proj, global_cfg=global_cfg)
    return config.load_config(str(proj))


def test_project_type_confusion_keeps_global_hardening(monkeypatch, tmp_path):
    """セクションを非dictで潰してもグローバルの強化値は消えない(却下ラウンド1の回帰)。"""
    hardened = {
        "exfil_guard": {"mode": "always", "categories": {"pii": "deny"}},
        "bash_guard": {"protected_branches": ["main", "staging"]},
    }
    for shape in _TYPE_CONFUSION_SHAPES:
        cfg = _with_layers(
            monkeypatch, tmp_path, hardened,
            {"exfil_guard": shape, "bash_guard": shape},
        )
        assert cfg["exfil_guard"]["mode"] == "always", shape
        assert cfg["exfil_guard"]["categories"]["pii"] == "deny", shape
        assert cfg["bash_guard"]["protected_branches"] == ["main", "staging"], shape
        assert cfg["_errors"], shape


def test_project_nested_confusion_keeps_global_value(monkeypatch, tmp_path):
    """ネストしたdictを非dictで潰した場合も直下の層へ戻る。"""
    cfg = _with_layers(
        monkeypatch, tmp_path,
        {"exfil_guard": {"categories": {"pii": "deny"}}},
        {"exfil_guard": {"categories": "x"}},
    )
    assert cfg["exfil_guard"]["categories"]["pii"] == "deny"


def test_project_bad_enum_and_category_keep_global_value(monkeypatch, tmp_path):
    """列挙・カテゴリの不正値もビルトインでなくグローバルの値へ戻る。"""
    cfg = _with_layers(
        monkeypatch, tmp_path,
        {"exfil_guard": {"mode": "always", "categories": {"pii": "deny"}}},
        {"exfil_guard": {"mode": "bogus", "categories": {"pii": "bogus"}}},
    )
    assert cfg["exfil_guard"]["mode"] == "always"
    assert cfg["exfil_guard"]["categories"]["pii"] == "deny"


def test_project_bad_list_keeps_global_list(monkeypatch, tmp_path):
    """文字列リストの不正値もグローバルの値へ戻る(既定値の空リストにしない)。"""
    cfg = _with_layers(
        monkeypatch, tmp_path,
        {"secrets_guard": {"write_protected_paths": ["infra/**"]}},
        {"secrets_guard": {"write_protected_paths": [1, 2]}},
    )
    assert cfg["secrets_guard"]["write_protected_paths"] == ["infra/**"]


def test_project_bad_scanners_keep_global_values(monkeypatch, tmp_path):
    """scanners の不正値もグローバルの値へ戻る。"""
    cfg = _with_layers(
        monkeypatch, tmp_path,
        {"scanners": {"gitleaks_image": "example/gitleaks:pinned",
                      "gitleaks_config": ".gitleaks.toml"}},
        {"scanners": {"gitleaks_image": 1, "gitleaks_config": []}},
    )
    assert cfg["scanners"]["gitleaks_image"] == "example/gitleaks:pinned"
    assert cfg["scanners"]["gitleaks_config"] == ".gitleaks.toml"


def test_global_bad_type_still_falls_back_to_builtin(monkeypatch, tmp_path):
    """最上位でない層(グローバル)の不正値はビルトイン既定へ戻る(従来どおり)。"""
    cfg = _with_layers(monkeypatch, tmp_path, {"exfil_guard": 0}, {})
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"


def test_legitimate_project_override_unaffected(monkeypatch, tmp_path):
    """正当なプロジェクト上書きは従来どおり効き、警告も出ない(false-positive方向)。"""
    cfg = _with_layers(
        monkeypatch, tmp_path,
        {"exfil_guard": {"mode": "detect", "categories": {"pii": "ask"}}},
        {"exfil_guard": {"mode": "always", "categories": {"pii": "deny"}}},
    )
    assert cfg["exfil_guard"]["mode"] == "always"
    assert cfg["exfil_guard"]["categories"]["pii"] == "deny"
    assert cfg["_errors"] == []


def test_unknown_category_key_absent_from_lower_layer_is_dropped(monkeypatch, tmp_path):
    """下位層に存在しない未知カテゴリの不正値は削除する(戻す先がないため)。"""
    cfg = _with_layers(
        monkeypatch, tmp_path, {}, {"exfil_guard": {"categories": {"unknown": "bogus"}}},
    )
    assert "unknown" not in cfg["exfil_guard"]["categories"]
    assert cfg["exfil_guard"]["categories"]["credentials"] == "deny"
    # メッセージも厳密に(戻す先が無いので「下位層の値を使用」は付かない)
    assert cfg["_errors"] == [
        "exfil_guard.categories.unknown: 不正な値 'bogus' のため無視しました"
    ]


# ---- 層ごと縮退(8cd921d)の補強: mutation トリアージで見つかった穴 ----


def test_no_config_files_returns_nested_copies_not_defaults(monkeypatch, tmp_path):
    """設定ファイルが1つも無い経路でも、ネスト辞書が DEFAULTS と別名化していない。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    cfg["exfil_guard"]["categories"]["credentials"] = "off"
    cfg["bash_guard"]["protected_branches"].append("polluted")
    assert config.DEFAULTS["exfil_guard"]["categories"]["credentials"] == "deny"
    assert "polluted" not in config.DEFAULTS["bash_guard"]["protected_branches"]


def test_top_level_type_confusion_message_names_key_and_value(monkeypatch, tmp_path):
    """セクション丸ごとの型すり替えは、キー名と不正値を含む文言で下位層へ戻す。"""
    cfg = _with_layers(
        monkeypatch, tmp_path,
        {"bash_guard": {"protected_branches": ["trunk"]}},
        {"bash_guard": 0},
    )
    assert cfg["bash_guard"]["protected_branches"] == ["trunk"]  # 下位層(グローバル)の値
    assert cfg["_errors"] == ["bash_guard: 不正な値 0 のため無視しました(下位層の値を使用)"]


def test_unexpected_error_fallback_returns_nested_copies_not_defaults(monkeypatch, tmp_path):
    """想定外例外の縮退経路(load_config の except)でもネスト辞書が DEFAULTS と別名化しない。"""
    def boom(cwd=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(config, "_load_config", boom)
    cfg = config.load_config(str(tmp_path))
    assert cfg["_errors"] == ["設定の読み込みに失敗したため既定値を使用します: boom"]
    cfg["exfil_guard"]["categories"]["credentials"] = "off"
    assert config.DEFAULTS["exfil_guard"]["categories"]["credentials"] == "deny"


def test_non_dict_categories_message_names_key_and_value(monkeypatch, tmp_path):
    cfg = _with_layers(monkeypatch, tmp_path, {}, {"exfil_guard": {"categories": "x"}})
    assert cfg["exfil_guard"]["categories"] == config.DEFAULTS["exfil_guard"]["categories"]
    assert cfg["_errors"] == [
        "exfil_guard.categories: 不正な値 'x' のため無視しました(下位層の値を使用)"
    ]


# ---- プロジェクト設定のオプトイン信頼(0.7.0) ----


def _proj_with(tmp_path, project_cfg_text):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    if isinstance(project_cfg_text, bytes):
        (proj / ".claude-hooks.json").write_bytes(project_cfg_text)
    else:
        (proj / ".claude-hooks.json").write_text(project_cfg_text, encoding="utf-8")
    return proj


SINKS = {
    "bash_guard": {"allow": ["git-force-push"], "protected_branches": [], "extra_deny": ["evil"]},
    "secrets_guard": {"allow_paths": [".env"]},
    "exfil_guard": {
        "trusted_servers": ["evil"], "categories": {"credentials": "off"}, "mode": "always"
    },
    "quality_gate": {"commands": {"*.py": "echo pwned"}},
    "notify": {"command": "echo pwned"},
    "scanners": {"gitleaks": "docker", "gitleaks_image": "evil/img", "gitleaks_config": "/tmp/x"},
}


def test_unapproved_project_config_has_no_effect_and_notifies(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = _proj_with(tmp_path, json.dumps(SINKS))
    cfg = config.load_config(str(proj))
    baseline = config.load_config(str(tmp_path / "empty-dir-does-not-exist"))
    for section in SINKS:
        assert cfg[section] == baseline[section], section
    assert cfg["_errors"] == []
    assert len(cfg["_notices"]) == 1
    raw = (proj / ".claude-hooks.json").read_bytes()
    assert cfg["_notices"][0] == trust.untrusted_notice(
        os.path.realpath(str(proj)), trust.content_hash(raw)
    )


def test_unapproved_project_is_not_parsed_even_if_invalid(monkeypatch, tmp_path):
    """原則1: 非承認は解析しない → 不正 UTF-8 / 深いネストでも _errors は増えず通知だけ出る。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = _proj_with(tmp_path, b"\xff{}")
    cfg = config.load_config(str(proj))
    assert cfg["_errors"] == []
    assert len(cfg["_notices"]) == 1
    assert cfg["bash_guard"]["enabled"] is True
    depth = 200_000
    (tmp_path / "two").mkdir()
    proj2 = _proj_with(tmp_path / "two", "[" * depth + "]" * depth)
    cfg2 = config.load_config(str(proj2))
    assert cfg2["_errors"] == [] and len(cfg2["_notices"]) == 1


def test_pinned_approval_applies_project_config_silently(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"exfil_guard": {"mode": "always"}}))
    approve_project(monkeypatch, tmp_path / "global.json", proj, pinned=True)
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "always"
    assert cfg["_notices"] == [] and cfg["_errors"] == []


def test_pinned_approval_rejects_after_one_byte_change(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"exfil_guard": {"mode": "always"}}))
    approve_project(monkeypatch, tmp_path / "global.json", proj, pinned=True)
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "always"}}) + " ", encoding="utf-8"
    )
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"
    raw = (proj / ".claude-hooks.json").read_bytes()
    assert cfg["_notices"] == [
        trust.mismatch_notice(os.path.realpath(str(proj)), trust.content_hash(raw))
    ]


def test_unpinned_approval_applies_and_keeps_applying_after_change(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"exfil_guard": {"mode": "always"}}))
    approve_project(monkeypatch, tmp_path / "global.json", proj)  # true
    assert config.load_config(str(proj))["exfil_guard"]["mode"] == "always"
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": {"mode": "detect"}, "notify": {"method": "bell"}}),
        encoding="utf-8",
    )
    cfg = config.load_config(str(proj))
    assert cfg["notify"]["method"] == "bell"
    assert len(cfg["_notices"]) == 1 and "ピン留めなし承認" in cfg["_notices"][0]
    assert config.load_config(str(proj))["_notices"] == []  # 同じ内容なら黙る


def test_explicit_false_rejects_silently(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"exfil_guard": {"mode": "always"}}))
    (tmp_path / "global.json").write_text(
        json.dumps({"trusted_projects": {os.path.realpath(str(proj)): False}}), encoding="utf-8"
    )
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "global.json")
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["_notices"] == []


def test_project_cannot_approve_itself(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = tmp_path / "proj"
    proj.mkdir()
    body = {"trusted_projects": {os.path.realpath(str(proj)): True},
            "exfil_guard": {"mode": "always"}}
    (proj / ".claude-hooks.json").write_text(json.dumps(body), encoding="utf-8")
    cfg = config.load_config(str(proj))
    assert cfg["exfil_guard"]["mode"] == "detect"
    assert cfg["trusted_projects"] == {}


def test_invalid_trusted_projects_in_global_is_recorded_and_all_untrusted(monkeypatch, tmp_path):
    # bad ごとに別ディレクトリを使う: 通知クールダウンは project_key 単位なので、
    # 同一 proj を使い回すと 2 件目以降が「直前に通知済み」として抑制されてしまう。
    for i, bad in enumerate([[], None, "x", 1]):
        (tmp_path / "global.json").write_text(
            json.dumps({"trusted_projects": bad, "exfil_guard": {"mode": "always"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "global.json")
        case_dir = tmp_path / f"case{i}"
        case_dir.mkdir()
        proj = _proj_with(case_dir, json.dumps({"notify": {"method": "bell"}}))
        cfg = config.load_config(str(proj))
        assert cfg["exfil_guard"]["mode"] == "always", bad          # グローバルの他の値は保たれる
        assert cfg["notify"]["method"] == "auto", bad                # 全プロジェクト非承認
        assert cfg["trusted_projects"] == {}, bad
        assert cfg["_errors"] == [
            f"trusted_projects: 不正な値 {bad!r} のため無視しました(下位層の値を使用)"
        ], bad
        assert len(cfg["_notices"]) == 1, bad


def test_approved_project_invalid_utf8_records_error(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, b"\xff{}")
    approve_project(monkeypatch, tmp_path / "global.json", proj, pinned=True)
    cfg = config.load_config(str(proj))
    assert len(cfg["_errors"]) == 1 and cfg["_notices"] == []
    assert cfg["bash_guard"]["enabled"] is True


def test_notice_cooldown_sec_from_global_is_used(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "global.json")
    (tmp_path / "global.json").write_text(json.dumps({"notice_cooldown_sec": 0}), encoding="utf-8")
    proj = _proj_with(tmp_path, "{}")
    assert len(config.load_config(str(proj))["_notices"]) == 1
    assert len(config.load_config(str(proj))["_notices"]) == 1  # 0 = 毎回


def test_no_project_file_no_notice(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["_notices"] == [] and cfg["_errors"] == []


def test_defaults_include_trust_keys():
    assert config.DEFAULTS["trusted_projects"] == {}
    assert config.DEFAULTS["notice_cooldown_sec"] == 3600
    cfg = config.load_config(None)
    assert "_notices" in cfg


def test_unexpected_error_fallback_has_empty_notices(monkeypatch):
    def boom(cwd=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(config, "_load_config", boom)
    cfg = config.load_config(None)
    assert cfg["_notices"] == []


# --- 読取失敗(OSError)は層ごとに記録し、次の層の処理は止めない ---


def _raise_read_bytes_for(monkeypatch, target):
    """target だけ Path.read_bytes が PermissionError を送出するようにする。"""
    original = Path.read_bytes

    def fake(self):
        if self == target:
            raise PermissionError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", fake)


def test_unreadable_project_layer_records_error_and_keeps_lower_layers(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"quality_gate": {"mode": "block"}}))
    approve_project(monkeypatch, tmp_path / "global.json", proj,
                    global_cfg={"quality_gate": {"mode": "warn"}})
    _raise_read_bytes_for(monkeypatch, proj / ".claude-hooks.json")
    cfg = config.load_config(str(proj))
    assert cfg["_errors"] == [f"{proj / '.claude-hooks.json'}: denied"]
    assert cfg["_notices"] == []  # 読めなければ承認判定にも進まない(通知なし)
    assert cfg["quality_gate"]["mode"] == "warn"  # グローバル層はそのまま生きる


def test_unreadable_global_layer_records_error_and_still_gates_project(monkeypatch, tmp_path):
    proj = _proj_with(tmp_path, json.dumps({"exfil_guard": {"mode": "always"}}))
    global_path = tmp_path / "global.json"
    global_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", global_path)
    _raise_read_bytes_for(monkeypatch, global_path)
    cfg = config.load_config(str(proj))
    assert cfg["_errors"] == [f"{global_path}: denied"]
    # グローバル層が読めなくても処理はプロジェクト層へ進む(承認が無いので非承認通知が出る)
    assert len(cfg["_notices"]) == 1 and "未承認" in cfg["_notices"][0]
    assert cfg["exfil_guard"]["mode"] == "detect"


def test_no_project_file_means_no_trust_notice(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["_notices"] == [] and cfg["_errors"] == []
