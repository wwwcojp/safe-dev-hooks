import os
import pty
import select
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "examples" / "notify_wrapper.sh"
BASH = shutil.which("bash")


def _run_isolated(tmp_path, args):
    """通知コマンドが見つからず制御端末も無い環境でラッパーを実行する。"""
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir(exist_ok=True)
    return subprocess.run(
        [BASH, str(SCRIPT), *args],
        env={"PATH": str(empty_bin)},
        capture_output=True,
        text=True,
        timeout=10,
        start_new_session=True,  # 制御端末を切り離し /dev/tty 経路を無効化
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


def test_bell_to_controlling_tty_when_available(tmp_path):
    """stdout/stderrが捕捉されても(notify.pyと同条件)、/dev/tty へベルが届く。"""
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir(exist_ok=True)
    pid, master = pty.fork()
    if pid == 0:  # 子: ptyを制御端末とし、stdout/stderrは捨てる
        os.environ.clear()
        os.environ["PATH"] = str(empty_bin)
        os.execv(BASH, [BASH, "-c", f'exec "{BASH}" "{SCRIPT}" "tty経由テスト" >/dev/null 2>&1'])
    data = b""
    while True:
        ready, _, _ = select.select([master], [], [], 5)
        if not ready:
            break
        try:
            chunk = os.read(master, 1024)
        except OSError:
            break
        if not chunk:
            break
        data += chunk
    os.close(master)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert b"\a" in data
    assert "tty経由テスト" in data.decode("utf-8", errors="replace")


def test_script_is_executable():
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111
