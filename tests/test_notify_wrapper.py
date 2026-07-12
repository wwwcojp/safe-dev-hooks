import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "examples" / "notify_wrapper.sh"
BASH = shutil.which("bash")


def _run_isolated(tmp_path, args):
    """通知コマンドが一切見つからない環境でラッパーを実行する。"""
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir(exist_ok=True)
    return subprocess.run(
        [BASH, str(SCRIPT), *args],
        env={"PATH": str(empty_bin)},
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_fallback_bell_and_message_to_stderr(tmp_path):
    result = _run_isolated(tmp_path, ["許可待ちです"])
    assert result.returncode == 0
    assert "\a" in result.stderr
    assert "許可待ちです" in result.stderr


def test_no_message_still_exits_zero(tmp_path):
    result = _run_isolated(tmp_path, [])
    assert result.returncode == 0
    assert "\a" in result.stderr


def test_script_is_executable():
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111
