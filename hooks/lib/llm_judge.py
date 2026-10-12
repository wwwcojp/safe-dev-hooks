"""ヘッドレスClaude(`claude -p`)の呼び出しに共通する処理。

判定用の呼び出しはツールを全て無効にし(`--tools=`)、プロンプトは標準入力で渡す。
argv で渡すと長い入力で引数長の上限に当たり、stdin を閉じないと入力待ちで止まる。
"""
import json
import os
import shutil
import subprocess

RECURSION_ENV = "SAFE_DEV_HOOKS_SEMANTIC"


class Unavailable(Exception):
    """判定を試みなかった(claude CLI 不在・再帰防止)。呼び出し側は通知しない。"""


class JudgeError(Exception):
    """判定を試みたが結果を得られなかった。メッセージは利用者向け。"""


def _parse_object(stdout: str) -> dict:
    candidates = [stdout]
    start, end = stdout.find("{"), stdout.rfind("}")
    if start != -1 and end > start:
        candidates.append(stdout[start:end + 1])
    for text in candidates:
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
        raise JudgeError("出力が JSON オブジェクトではありません")
    raise JudgeError("出力から JSON を取り出せません")


def run_json(prompt: str, model: str, timeout: int) -> dict:
    """`claude -p` を実行し、出力の JSON オブジェクトを返す。"""
    if os.environ.get(RECURSION_ENV) == "1":
        raise Unavailable("再帰防止のため LLM 判定を行いません")
    if shutil.which("claude") is None:
        raise Unavailable("claude CLI が見つかりません")
    env = dict(os.environ, **{RECURSION_ENV: "1"})
    try:
        r = subprocess.run(
            ["claude", "-p", "--model", model, "--tools="],
            input=prompt, capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise JudgeError(f"タイムアウトしました({timeout}秒)") from exc
    except OSError as exc:
        raise JudgeError(f"claude CLI を起動できません: {exc}") from exc
    if r.returncode != 0:
        raise JudgeError(f"claude CLI が終了コード {r.returncode} で終了しました")
    return _parse_object(r.stdout)
