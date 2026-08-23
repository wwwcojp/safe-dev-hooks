import importlib
import sys


def load_hook(relpath: str):
    """hooks/配下のスクリプトを `hooks.<dir>.<name>` として読み込む(__main__ガード前提)。

    呼び出しごとに fresh なモジュールを返す(以前の spec_from_file_location 方式と同じ契約)。
    ルート起点の完全修飾名で import するのは mutmut が変異キーをファイルパス由来
    (hooks.pre_tool_use.bash_guard)で照合するため。
    """
    name = "hooks." + relpath[: -len(".py")].replace("/", ".")
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def approve_project(monkeypatch, global_path, proj, global_cfg=None, pinned=False):
    """テスト用: proj を承認するグローバル設定を global_path に書き、GLOBAL_CONFIG_PATH を向ける。

    pinned=False は「ピン留めなし(true)」、True は proj/.claude-hooks.json の
    現在の内容ハッシュで承認。
    """
    import json
    import os

    from hooks.lib import config, trust

    cfg = dict(global_cfg or {})
    trusted = dict(cfg.get("trusted_projects") or {})
    if pinned:
        raw = (proj / ".claude-hooks.json").read_bytes()
        trusted[os.path.realpath(str(proj))] = trust.content_hash(raw)
    else:
        trusted[os.path.realpath(str(proj))] = True
    cfg["trusted_projects"] = trusted
    global_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", global_path)


def isolated_home_env(home, approve=None, pinned=False):
    """テスト用: subprocess 黒箱テストで live hook に実 $HOME を触らせないための環境変数。

    HOME を home(tmp)に向ける(Path.home() は HOME を参照する)。approve にプロジェクト
    ディレクトリを渡すと、home/.claude/claude-hooks.json にその承認を書く。
    """
    import json
    import os
    from pathlib import Path

    from hooks.lib import trust

    home = Path(home)
    if approve is not None:
        trusted = {}
        if pinned:
            raw = (Path(approve) / ".claude-hooks.json").read_bytes()
            trusted[os.path.realpath(str(approve))] = trust.content_hash(raw)
        else:
            trusted[os.path.realpath(str(approve))] = True
        (home / ".claude").mkdir(parents=True, exist_ok=True)
        (home / ".claude" / "claude-hooks.json").write_text(
            json.dumps({"trusted_projects": trusted}), encoding="utf-8"
        )
    return {**os.environ, "HOME": str(home)}
