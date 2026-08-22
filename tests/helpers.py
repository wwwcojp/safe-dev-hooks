import importlib
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def load_hook(relpath: str):
    """hooks/配下のスクリプトを `hooks.<dir>.<name>` として読み込む(__main__ガード前提)。

    呼び出しごとに fresh なモジュールを返す(以前の spec_from_file_location 方式と同じ契約)。
    ルート起点の完全修飾名で import するのは mutmut が変異キーをファイルパス由来
    (hooks.pre_tool_use.bash_guard)で照合するため。
    """
    name = "hooks." + relpath[: -len(".py")].replace("/", ".")
    sys.modules.pop(name, None)
    return importlib.import_module(name)
