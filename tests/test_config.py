import copy
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
    # 承認が要る(0.7.0)。承認しないとプロジェクト層が解析されず、検証する revert 経路に
    # 到達しないまま「cfg は DEFAULTS の deepcopy のまま」で通ってしまう(空回りする)。
    (tmp_path / ".claude-hooks.json").write_text(
        json.dumps({"exfil_guard": "not-a-dict"}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", tmp_path, pinned=True)
    cfg = config.load_config(str(tmp_path))
    assert cfg["_errors"]  # revert が実際に走った証跡
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
    def boom(cwd=None, *, notices=True):
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


# --- revert は fallback の値を「複製」して採用する(共有しない) ---
# 呼び出し側から見れば fallback は破棄されるため現状この差は観測できない(0.6.1 で等価変異と
# 記録した箇所)。しかし「戻した値が下位層のオブジェクトと繋がっていない」ことは _validate の
# 防御的な契約であり、将来 fallback を再利用する形に変わったときに壊れると気付けない。
# 直接呼び出して契約自体を固定する。


def test_validate_revert_deep_copies_from_fallback():
    fallback = copy.deepcopy(config.DEFAULTS)
    fallback["exfil_guard"]["mode"] = "always"
    fallback["exfil_guard"]["categories"]["pii"] = "deny"
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["exfil_guard"] = "not-a-dict"  # 型不正 → セクションごと fallback へ戻る
    errors = []
    out = config._validate(cfg, fallback, errors)

    assert out["exfil_guard"]["mode"] == "always"  # 直下の層の値を採用
    assert out["exfil_guard"] is not fallback["exfil_guard"]  # 浅い別名でない
    assert out["exfil_guard"]["categories"] is not fallback["exfil_guard"]["categories"]
    # 入れ子まで複製されている: 採用後に書き換えても下位層は無傷(shallow copy では壊れる)
    out["exfil_guard"]["categories"]["pii"] = "mutated"
    assert fallback["exfil_guard"]["categories"]["pii"] == "deny"
    assert errors == ["exfil_guard: 不正な値 'not-a-dict' のため無視しました(下位層の値を使用)"]


def test_validate_revert_deep_copies_nested_category_value():
    # カテゴリ単位の revert(fallback_categories[cat_key])も同様に複製する
    fallback = copy.deepcopy(config.DEFAULTS)
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["exfil_guard"]["categories"]["pii"] = "bogus"
    errors = []
    out = config._validate(cfg, fallback, errors)
    expected = config.DEFAULTS["exfil_guard"]["categories"]["pii"]
    assert out["exfil_guard"]["categories"]["pii"] == expected
    assert errors == [
        "exfil_guard.categories.pii: 不正な値 'bogus' のため無視しました(下位層の値を使用)"
    ]


# ---- プロジェクトルートの基準差し替え(project_root) ----


def test_project_root_prefers_claude_project_dir_env(monkeypatch, tmp_path):
    root = tmp_path / "root"
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert config.project_root(str(sub)) == str(root)


def test_project_root_empty_env_is_treated_as_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    assert config.project_root(str(cwd)) == str(cwd)


def test_project_root_finds_nearest_git_ancestor(monkeypatch, tmp_path):
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    assert config.project_root(str(sub)) == str(root)


def test_project_root_recognizes_git_file_for_worktrees(monkeypatch, tmp_path):
    """worktree では `.git` はディレクトリではなくファイルなので is_dir() では取り逃す。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / ".git").write_text("gitdir: /somewhere/.git/worktrees/x", encoding="utf-8")
    sub = root / "a"
    sub.mkdir()
    assert config.project_root(str(sub)) == str(root)


def test_project_root_falls_back_to_cwd_without_git(monkeypatch, tmp_path):
    cwd = tmp_path / "no-git-here"
    cwd.mkdir()
    assert config.project_root(str(cwd)) == str(cwd)


def test_project_root_none_cwd_returns_none_without_git_search(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # tmp_path 自体には .git は無い
    assert config.project_root(None) is None


def test_project_root_swallows_oserror_from_ancestor_exists(monkeypatch, tmp_path):
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    original_exists = Path.exists

    def boom(self):
        raise OSError("denied")

    monkeypatch.setattr(Path, "exists", boom)
    try:
        assert config.project_root(str(sub)) == str(sub)
    finally:
        monkeypatch.setattr(Path, "exists", original_exists)


def test_project_root_env_overrides_even_when_cwd_has_git(monkeypatch, tmp_path):
    """env が cwd の祖先であれば、cwd 自身が git ルートでも env が優先される。"""
    env_root = tmp_path / "env-root"
    env_root.mkdir()
    git_root = env_root / "inner"
    (git_root / ".git").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(env_root))
    assert config.project_root(str(git_root)) == str(env_root)


def test_load_config_uses_claude_project_dir_env_with_differing_cwd(monkeypatch, tmp_path):
    """CLAUDE_PROJECT_DIR が設定されていれば、cwd が別ディレクトリでもそこの
    .claude-hooks.json が読まれる(承認済みの状態で)。load_config レベルの end-to-end
    検証: env 側のプロジェクトを pinned 承認し、cwd 側には .claude-hooks.json を置かない。
    env 経路が無視され cwd 基準に戻ってしまうと、cwd 側に設定が無いため既定値の
    "auto" のままになり、この assert で落ちる。
    """
    env_root = tmp_path / "env-root"
    env_root.mkdir()
    (env_root / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "bell"}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", env_root, pinned=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(env_root))
    cwd = env_root / "other-cwd"
    cwd.mkdir()  # env_root とは別ディレクトリで、独自の .claude-hooks.json は置かない
    cfg = config.load_config(str(cwd))
    assert cfg["notify"]["method"] == "bell"
    assert cfg["_errors"] == []
    assert cfg["_notices"] == []  # 未承認/不一致通知が無い = env 経路で正しく承認一致した証跡


def test_load_config_write_protected_paths_reachable_from_subdirectory(monkeypatch, tmp_path):
    """回帰: プロジェクトルートの承認済み設定は cwd をサブディレクトリにしても読まれる。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    (root / ".claude-hooks.json").write_text(
        json.dumps({"secrets_guard": {"write_protected_paths": ["secret.txt"]}}),
        encoding="utf-8",
    )
    approve_project(monkeypatch, tmp_path / "global.json", root, pinned=True)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    cfg = config.load_config(str(sub))
    assert cfg["secrets_guard"]["write_protected_paths"] == ["secret.txt"]
    assert cfg["_errors"] == []


def test_trusted_projects_key_is_resolved_root_realpath_regardless_of_cwd(monkeypatch, tmp_path):
    """基準がどう解決されても trusted_projects のキーは解決後ルートの realpath になる。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    (root / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "bell"}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", root, pinned=True)
    sub = root / "deep" / "sub"
    sub.mkdir(parents=True)
    # 承認エントリのキーは root の realpath。cwd をサブディレクトリにしても
    # 同じキーで一致し、既存の承認エントリが採用される(=設定が適用される)。
    cfg = config.load_config(str(sub))
    assert cfg["notify"]["method"] == "bell"
    assert cfg["_notices"] == []  # 未承認/不一致の通知が出ない = 正しいキーで一致した証跡


def test_apply_layer_does_not_share_untouched_sections_with_lower_layer():
    """マージ結果は下位層と入れ子オブジェクトを共有しない(_merge の別名化を断つ)。

    revert と同じく、現状は下位層が破棄されるため呼び出し側から差は観測できないが、
    「層をまたいで同じ dict を書き換えてしまう」経路を作らない契約として固定する。
    """
    lower = copy.deepcopy(config.DEFAULTS)
    lower["exfil_guard"]["mode"] = "always"
    raw = json.dumps({"audit_log": {"path": "logs"}}).encode("utf-8")  # 別セクションだけ触る
    out = config._apply_layer(lower, Path("dummy.json"), raw, [])

    assert out["audit_log"]["path"] == "logs"
    assert out["exfil_guard"]["mode"] == "always"  # 触っていない層の値は残る
    assert out["exfil_guard"] is not lower["exfil_guard"]  # が、同じオブジェクトではない
    out["exfil_guard"]["mode"] = "detect"
    assert lower["exfil_guard"]["mode"] == "always"


# ---- D2: 読まなかったプロジェクト設定の通知(_skipped_notices) ----


def test_load_config_notifies_when_cwd_has_config_but_root_differs(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    cwd = root / "cwd"
    cwd.mkdir()
    (cwd / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "bell"}}), encoding="utf-8"
    )
    cfg = config.load_config(str(cwd))
    assert cfg["_notices"] == [trust.skipped_notice(os.path.realpath(str(cwd)), str(root))]
    assert cfg["notify"]["method"] == "auto"  # cwd 側は読まれていない(既定値のまま)


def test_load_config_skipped_notice_respects_cooldown(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    cwd = root / "cwd"
    cwd.mkdir()
    (cwd / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    first = config.load_config(str(cwd))
    second = config.load_config(str(cwd))
    assert len(first["_notices"]) == 1
    assert second["_notices"] == []  # クールダウン内は抑止


def test_load_config_skipped_notice_cooldown_zero_notifies_every_time(monkeypatch, tmp_path):
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"notice_cooldown_sec": 0}), encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", global_path)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    cwd = root / "cwd"
    cwd.mkdir()
    (cwd / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    assert len(config.load_config(str(cwd))["_notices"]) == 1
    assert len(config.load_config(str(cwd))["_notices"]) == 1  # 0 = 毎回


def test_load_config_skipped_notice_notifies_every_time_when_state_unusable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    state_dir = tmp_path / "state-dir"
    state_dir.mkdir()  # ディレクトリ = 状態ファイルとして使えない(読み書き失敗)
    monkeypatch.setattr(trust, "STATE_PATH", state_dir)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    cwd = root / "cwd"
    cwd.mkdir()
    (cwd / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    assert len(config.load_config(str(cwd))["_notices"]) == 1
    assert len(config.load_config(str(cwd))["_notices"]) == 1  # 毎回


def test_load_config_no_skipped_notice_when_cwd_equals_root(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    cfg = config.load_config(str(root))
    assert cfg["_notices"] == []


def test_load_config_no_skipped_notice_when_cwd_has_no_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    cwd = root / "cwd"
    cwd.mkdir()  # .claude-hooks.json を置かない
    cfg = config.load_config(str(cwd))
    assert cfg["_notices"] == []


def test_load_config_no_skipped_notice_when_cwd_is_none():
    assert config._skipped_notices(None, "/home/alice/root", 3600) == []


# ---- C1: 通知を表示しない呼び出し(notices=False)は通知の状態を進めない ----


def test_quiet_load_does_not_consume_skipped_notice_cooldown(monkeypatch, tmp_path):
    """audit_log 相当の静かな呼び出しの後でも、guard 相当の呼び出しは D2 通知を出す。

    0.7.1 以前は audit_log(SessionStart と全 PreToolUse/PostToolUse で走る)が
    load_config を呼ぶだけで skipped_last のクールダウン枠を消費し、以後 1 時間
    どの対話フックも通知を出せなくなっていた(C1)。
    """
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    cwd = root / "sub"
    cwd.mkdir()
    (cwd / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    quiet = config.load_config(str(cwd), notices=False)      # audit_log 相当
    loud = config.load_config(str(cwd))                      # bash_guard 相当
    assert quiet["_notices"] == []
    assert loud["_notices"] == [
        trust.skipped_notice(os.path.realpath(str(cwd)), str(root))
    ]


def test_quiet_load_does_not_consume_untrusted_notice_cooldown(monkeypatch, tmp_path):
    """0.7.0 の「未承認のため無視しました」通知も同じ経路で消費されない。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    root = tmp_path / "root"
    root.mkdir()
    (root / ".claude-hooks.json").write_text(
        json.dumps({"notify": {"method": "bell"}}), encoding="utf-8"
    )
    quiet = config.load_config(str(root), notices=False)
    loud = config.load_config(str(root))
    assert quiet["_notices"] == []
    assert len(loud["_notices"]) == 1
    assert "未承認" in loud["_notices"][0]


def test_quiet_load_does_not_update_unpinned_seen(monkeypatch, tmp_path):
    """静かな呼び出しは変化検知の記録も進めない(変化を見逃さない側に倒す)。"""
    root = tmp_path / "root"
    root.mkdir()
    project = root / ".claude-hooks.json"
    project.write_text(json.dumps({"notify": {"method": "bell"}}), encoding="utf-8")
    approve_project(monkeypatch, tmp_path / "global.json", root)  # ピン留めなし承認
    assert config.load_config(str(root))["_notices"] == []        # v1 を記録
    project.write_text(json.dumps({"notify": {"method": "auto"}}), encoding="utf-8")
    quiet = config.load_config(str(root), notices=False)
    assert quiet["_notices"] == []
    assert quiet["notify"]["method"] == "auto"                    # 採用判定は同じ
    loud = config.load_config(str(root))
    assert len(loud["_notices"]) == 1 and "変更されています" in loud["_notices"][0]


def test_quiet_load_keeps_adopt_decision_unchanged(monkeypatch, tmp_path):
    """採用/不採用は notices に依存しない(deny 層はこのフラグで変わらない)。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / ".claude-hooks.json").write_text(
        json.dumps({"secrets_guard": {"write_protected_paths": ["x.txt"]}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", root, pinned=True)
    for notices in (True, False):
        cfg = config.load_config(str(root), notices=notices)
        assert cfg["secrets_guard"]["write_protected_paths"] == ["x.txt"], notices


def test_quiet_load_still_rejects_unapproved_project_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    root = tmp_path / "root"
    root.mkdir()
    (root / ".claude-hooks.json").write_text(
        json.dumps({"secrets_guard": {"allow_paths": ["*"]}}), encoding="utf-8"
    )
    cfg = config.load_config(str(root), notices=False)
    assert cfg["secrets_guard"]["allow_paths"] == []  # 未承認は静かな呼び出しでも不採用


def test_quiet_load_still_reports_config_errors(monkeypatch, tmp_path):
    """_errors は通知(_notices)ではないので notices=False でも記録される。"""
    global_path = tmp_path / "global.json"
    global_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", global_path)
    cfg = config.load_config(str(tmp_path), notices=False)
    assert len(cfg["_errors"]) == 1


def test_quiet_load_never_raises_on_bad_input(monkeypatch, tmp_path):
    def boom(cwd=None, *, notices=True):
        raise RuntimeError("boom")

    monkeypatch.setattr(config, "_load_config", boom)
    cfg = config.load_config(str(tmp_path), notices=False)
    assert cfg["_notices"] == []
    assert cfg["_errors"] == ["設定の読み込みに失敗したため既定値を使用します: boom"]


# ---- I1/M1: CLAUDE_PROJECT_DIR の検証(_env_root) ----


def test_env_root_rejects_relative_path(monkeypatch, tmp_path):
    """相対パスはフックプロセスの cwd 基準で解決されてしまうので採用しない。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    sub = root / "sub"
    sub.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "sub")
    assert config.project_root(str(sub)) == str(root)  # git 探索へフォールバック


def test_env_root_rejects_nonexistent_directory(monkeypatch, tmp_path):
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "does-not-exist"))
    assert config.project_root(str(root)) == str(root)


def test_env_root_rejects_plain_file(monkeypatch, tmp_path):
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    plain = root / "afile"
    plain.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plain))
    assert config.project_root(str(root)) == str(root)


def test_env_root_rejects_unrelated_directory(monkeypatch, tmp_path):
    """cwd の祖先でない値は採用しない(敵対的な `.claude/settings.json` の env 対策)。"""
    other = tmp_path / "other"
    other.mkdir()
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))
    assert config.project_root(str(sub)) == str(root)


def test_env_root_rejects_descendant_of_cwd(monkeypatch, tmp_path):
    """cwd の子孫(祖先でない)も採用しない。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    deeper = root / "deeper"
    deeper.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(deeper))
    assert config.project_root(str(root)) == str(root)


def test_env_root_accepts_cwd_itself(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert config.project_root(str(root)) == str(root)


def test_env_root_accepts_ancestor_through_symlinked_cwd(monkeypatch, tmp_path):
    """祖先判定は realpath 基準(シンボリックリンク経由の cwd でも正しく祖先と分かる)。"""
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    link = tmp_path / "link-to-sub"
    link.symlink_to(sub)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert config.project_root(str(link)) == str(root)


def test_env_root_used_when_cwd_is_none(monkeypatch, tmp_path):
    """cwd が無ければ祖先判定のしようがないので、ディレクトリでありさえすれば使う。"""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert config.project_root(None) == str(root)


def test_env_root_rejects_nondirectory_when_cwd_is_none(monkeypatch, tmp_path):
    plain = tmp_path / "afile"
    plain.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plain))
    assert config.project_root(None) is None


def test_env_root_swallows_oserror_from_isdir(monkeypatch, tmp_path):
    """検証中に OSError が起きても例外を外へ出さず git 探索へ落ちる。"""
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))

    def boom(_path):
        raise OSError("denied")

    monkeypatch.setattr(os.path, "isdir", boom)
    assert config.project_root(str(root)) == str(root)


def test_add_dir_style_cwd_outside_session_root_uses_its_own_git_root(monkeypatch, tmp_path):
    """`/add-dir` 等で cwd がセッションルート外に出た場合はエラーでなくフォールバック。

    その場所の git ルートが基準になり、そこの承認済み設定がちゃんと適用される。
    """
    session_root = tmp_path / "session"
    (session_root / ".git").mkdir(parents=True)
    added = tmp_path / "added"
    (added / ".git").mkdir(parents=True)
    (added / ".claude-hooks.json").write_text(
        json.dumps({"secrets_guard": {"write_protected_paths": ["added.txt"]}}), encoding="utf-8"
    )
    approve_project(monkeypatch, tmp_path / "global.json", added, pinned=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(session_root))
    cwd = added / "pkg"
    cwd.mkdir()
    cfg = config.load_config(str(cwd))
    assert cfg["secrets_guard"]["write_protected_paths"] == ["added.txt"]
    assert cfg["_errors"] == []
    assert cfg["_notices"] == []  # 異常ではないので通知も出さない


def test_env_cannot_substitute_another_approved_projects_config(monkeypatch, tmp_path):
    """env で「別の承認済みプロジェクト」の緩和設定を持ち込めない(I1 の主要ケース)。"""
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    (proj / ".claude-hooks.json").write_text(
        json.dumps({"secrets_guard": {"write_protected_paths": ["*/secret.txt"]}}),
        encoding="utf-8",
    )
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    (other / ".claude-hooks.json").write_text(
        json.dumps({"secrets_guard": {"allow_paths": ["*.env"]},
                    "bash_guard": {"allow": ["ANYTHING"]}}),
        encoding="utf-8",
    )
    global_path = tmp_path / "global.json"
    approve_project(monkeypatch, global_path, proj, pinned=True)
    existing = json.loads(global_path.read_text(encoding="utf-8"))
    approve_project(monkeypatch, global_path, other, global_cfg=existing, pinned=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))
    sub = proj / "sub"
    sub.mkdir()
    cfg = config.load_config(str(sub))
    assert cfg["secrets_guard"]["write_protected_paths"] == ["*/secret.txt"]  # proj のまま
    assert cfg["secrets_guard"]["allow_paths"] == []
    assert cfg["bash_guard"]["allow"] == []


# ---- I1(b)/I2: 落ちたプロジェクト層は cwd 直下に無くても通知する ----


def test_notifies_when_env_anchor_drops_the_real_project_config(monkeypatch, tmp_path):
    """env が本来のプロジェクトルートの上位を指し、ルート直下の設定が落ちる場合(D3)。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    workspace = tmp_path / "workspace"
    proj = workspace / "proj"
    (proj / ".git").mkdir(parents=True)
    (proj / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    sub = proj / "sub"
    sub.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(workspace))  # cwd の祖先なので採用される
    cfg = config.load_config(str(sub))
    assert cfg["_notices"] == [
        trust.skipped_notice(os.path.realpath(str(proj)), str(workspace))
    ]


def test_notifies_when_nested_git_re_anchors_below_the_project(monkeypatch, tmp_path):
    """vendored clone / submodule のネストした `.git` で親の設定が落ちる場合(I2)。"""
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    (proj / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    dep = proj / "vendor" / "dep"
    (dep / ".git").mkdir(parents=True)
    src = dep / "src"
    src.mkdir()
    cfg = config.load_config(str(src))
    assert cfg["_notices"] == [
        trust.skipped_notice(os.path.realpath(str(proj)), str(dep))
    ]


def test_notifies_when_submodule_git_file_re_anchors(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    (proj / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    smod = proj / "smod"
    smod.mkdir()
    (smod / ".git").write_text("gitdir: ../.git/modules/smod", encoding="utf-8")
    cfg = config.load_config(str(smod))
    assert cfg["_notices"] == [
        trust.skipped_notice(os.path.realpath(str(proj)), str(smod))
    ]


def test_skipped_config_dirs_skips_the_adopted_root(tmp_path):
    """基準ディレクトリ自身は「読まなかった」場所ではないので列挙しない。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    assert config._skipped_config_dirs(str(sub), str(root)) == []


def test_skipped_config_dirs_lists_from_cwd_upward(tmp_path):
    """cwd 側と祖先側の両方を、近い順に列挙する。"""
    outer = tmp_path / "outer"
    inner = outer / "inner"
    cwd = inner / "cwd"
    cwd.mkdir(parents=True)
    for d in (outer, cwd):
        (d / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    found = config._skipped_config_dirs(str(cwd), str(inner))
    assert found == [os.path.realpath(str(cwd)), os.path.realpath(str(outer))]


def test_skipped_config_dirs_compares_by_realpath(tmp_path):
    """root がシンボリックリンク表記でも、同じ実ディレクトリなら列挙しない。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(root)
    sub = root / "sub"
    sub.mkdir()
    assert config._skipped_config_dirs(str(sub), str(link)) == []


def test_skipped_notices_uses_independent_cooldown_per_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    outer = tmp_path / "outer"
    proj = outer / "proj"
    (proj / ".git").mkdir(parents=True)
    (outer / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    (proj / "sub").mkdir()
    (proj / "sub" / ".claude-hooks.json").write_text("{}", encoding="utf-8")
    first = config.load_config(str(proj / "sub"))
    second = config.load_config(str(proj / "sub"))
    assert len(first["_notices"]) == 2   # sub と outer、それぞれ独立に通知
    assert second["_notices"] == []      # どちらもクールダウン中
