#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""編集されたファイルへ lint/format チェックを実行し、失敗をClaudeへフィードバックする。"""
import fnmatch
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from hooks.lib import config, hook_io, trust  # noqa: E402

WRITE_TOOLS = ("Edit", "Write")
COMMAND_TIMEOUT_SEC = 45
OUTPUT_TAIL_CHARS = 1500

# 自動検出: (globパターン, 必要な実行ファイル, 前提設定ファイル(いずれか必須), コマンド)
AUTO_DETECT = [
    ("*.py", "ruff", ("pyproject.toml", "ruff.toml", ".ruff.toml"), "ruff check {file}"),
    ("*.rs", "rustfmt", ("Cargo.toml",), "rustfmt --check {file}"),
    ("*.js|*.jsx|*.ts|*.tsx", "npx", ("package.json",), "npx --no-install eslint {file}"),
]


def _crosses_nested_repo(file_dir: str, root_r: str) -> bool:
    """file_dir から root_r へ遡る途中に、root_r とは別の `.git` 境界がないか調べる(0.8.0 I2)。

    root_r 自身の `.git` は対象外(見つかる前に走査を終える)。両辺は呼び出し元で
    realpath 済みの前提。
    """
    d = Path(file_dir)
    while str(d) != root_r:
        try:
            if (d / ".git").exists():
                return True
        except OSError:
            return True
        parent = d.parent
        if parent == d:
            return True
        d = parent
    return False


def _in_trusted_scope(file_path: str, root: str) -> bool:
    """file_path が root の実プロジェクト境界内にあるかを判定する(0.8.0 I2)。

    「root 配下」というパス包含だけでは自動検出の起点のずれを2つ塞ぎ切れない:
    (a) 承認済み root を cwd にしたまま、そこに物理的にネストしていない
        別ディレクトリ(未承認の別クローン)のファイルを file_path に指定する経路。
        ESLint 等は lint 対象ファイルの場所から設定を探索するため、cwd 側の承認だけでは
        file_path 側の未承認の設定ファイルを読ませないという保証にならない。
    (b) `CLAUDE_PROJECT_DIR` で祖先へ持ち上げられた root の内側に、自前の `.git` を持つ
        未承認のネストしたリポジトリ(vendored clone 等)がある経路。単純なパス包含は
        「物理的に配下にある」を満たしてしまうため、file_path から root へ遡る途中に
        別の `.git` がないことも要求する(env は経由しない素朴な祖先探索)。
    realpath 同士で比較し、symlink 越えを避ける。file_path が相対パスのときは
    (`resolve_commands` の単体テストが素の相対ファイル名を渡す契約のため)root 基準で
    解決する。
    """
    target = file_path if os.path.isabs(file_path) else os.path.join(root, file_path)
    try:
        file_dir = os.path.realpath(str(Path(target).parent))
        root_r = os.path.realpath(root)
    except OSError:
        return False
    if file_dir != root_r and not file_dir.startswith(root_r + os.sep):
        return False
    return not _crosses_nested_repo(file_dir, root_r)


def would_autodetect(file_path: str, root: str) -> bool:
    """trusted であれば自動検出が実際にコマンドを生成したかを、副作用なしに判定する(0.8.0 I3)。

    見るのは拡張子一致・実行ファイルの有無(`shutil.which`)・前提設定ファイルの有無・
    プロジェクト境界(`_in_trusted_scope`)だけで、プロセスは一切起動しない。
    「未承認だったこと」自体はここでは見ない — 呼び出し側で「未承認」と「そうであっても
    どのみちコマンドは生まれなかった」を区別するための関数。承認しても何も変わらない
    場面(マーカー無し・対象外の拡張子・境界外のファイル)で承認を促す通知を出さないため。
    """
    if not _in_trusted_scope(file_path, root):
        return False
    name = Path(file_path).name
    return any(
        any(fnmatch.fnmatch(name, p) for p in patterns_str.split("|"))
        and shutil.which(exe) is not None
        and any((Path(root) / m).is_file() for m in markers)
        for patterns_str, exe, markers, _cmd in AUTO_DETECT
    )


def resolve_commands(file_path: str, cfg: dict, root: str, *, trusted: bool) -> list:
    """`root` は `config.load_config` が承認判定に使った基準ディレクトリ(呼び出し元が渡す)。

    ここで `config.project_root` を再計算しない(0.8.0 ブランチレビュー I-1)。
    event["cwd"] はBashのcdに追従する一時的な値なので基準にできない(D1)が、その解決は
    設定の読み込みと同じ 1 か所で済ませ、「承認したディレクトリ」と「自動検出の起点」を
    構造的に同じ値にする。
    """
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
    # I2: 承認は root に対するものであり、file_path が root の実境界の外(別クローン・
    # ネストした別リポジトリ)にあるなら、その承認を自動検出の根拠にしない。
    if not _in_trusted_scope(file_path, root):
        return []
    for patterns_str, exe, markers, cmd in AUTO_DETECT:
        if not any(fnmatch.fnmatch(name, p) for p in patterns_str.split("|")):
            continue
        if shutil.which(exe) is None:
            continue
        if not any((Path(root) / m).is_file() for m in markers):
            continue
        commands.append(cmd.replace("{file}", quoted))
    return commands


def run_checks(commands: list, root: str) -> list:
    """D4: 実行ディレクトリもプロジェクトルート基準にする(マーカー探索と同じ基準)。

    `root` は呼び出し元が解決済みの基準ディレクトリ。ここでも再解決はしない(I-1)。
    """
    failures = []
    for cmd in commands:
        try:
            r = subprocess.run(
                shlex.split(cmd), cwd=root, capture_output=True, text=True,
                timeout=COMMAND_TIMEOUT_SEC,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            failures.append(f"$ {cmd}\n実行できませんでした: {exc}")
            continue
        if r.returncode != 0:
            tail = (r.stdout + r.stderr)[-OUTPUT_TAIL_CHARS:]
            failures.append(f"$ {cmd}\n{tail}")
    return failures


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") not in WRITE_TOOLS:
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("quality_gate", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all)
    file_path = (event.get("tool_input") or {}).get("file_path", "")
    cwd = event.get("cwd") or "."
    if not file_path or not Path(file_path).is_file():
        hook_io.finalize(None, cfg_all)
    try:
        # I-1(0.8.0 ブランチレビュー): 基準ディレクトリは config.load_config が承認判定に
        # 使った値(`_project_root`)をそのまま受け取り、ここで再計算しない。再計算すると
        # event["cwd"] が欠落/None のときだけ、承認キー(検証を通していない
        # CLAUDE_PROJECT_DIR)とアンカー(フックプロセス自身の cwd)が分岐し、承認済み
        # プロジェクトの名義で未承認クローンのファイルを lint してしまう。
        # 未承認・解決不能(None)のときは自動検出を一切行わない。
        root = cfg_all.get("_project_root")
        trusted = bool(cfg_all.get("_project_trusted")) and root is not None
        anchor = root or cwd
        commands = resolve_commands(file_path, cfg, anchor, trusted=trusted)
        # I-2: 明示的に `false` にしたプロジェクトへ承認を催促しない(0.7.0 の `_gate` が
        # `denied` で沈黙するのと同じ規約。利用者はすでに「いいえ」と答えている)。
        skipped = (
            root is not None
            and not commands
            and not trusted
            and not trust.is_denied(root, cfg_all.get("trusted_projects"))
            and would_autodetect(file_path, root)
        )
        # I-3: 同じキーに対する貼り付け行が 2 つ並ばないようにするための材料。
        # `.claude-hooks.json` があれば未承認設定の通知がピン留め形式のエントリを出す。
        config_present = skipped and os.path.isfile(
            os.path.join(root, config.PROJECT_CONFIG_NAME)
        )
        failures = run_checks(commands, anchor) if commands else []
    except Exception as exc:
        hook_io.fail_open("quality_gate", exc)
        return
    out = None
    if failures:
        detail = "\n\n".join(failures)
        if cfg.get("mode", "block") == "block":
            out = hook_io.post_block(
                f"品質チェックが失敗しました。修正してください:\n{detail}"
            )
        else:
            out = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": f"[safe-dev-hooks] 品質チェック警告:\n{detail}",
                }
            }
    if skipped:
        notices = trust.notify_autodetect_skipped(
            root, trust.cooldown_seconds(cfg_all.get("notice_cooldown_sec")),
            config_present=config_present,
        )
        if notices:
            out = dict(out or {})
            existing = out.get("systemMessage")
            msg = "\n".join(notices)
            out["systemMessage"] = f"{existing}\n{msg}" if existing else msg
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
