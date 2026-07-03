import importlib.util
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def load_hook(relpath: str):
    """hooks/配下のスクリプトをモジュールとして読み込む(__main__ガード前提)。"""
    path = HOOKS_DIR / relpath
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod
