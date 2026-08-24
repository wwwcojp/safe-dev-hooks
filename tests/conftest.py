import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture(autouse=True)
def _hide_external_secret_scanners(monkeypatch):
    """実 gitleaks/docker へ出ずテストを決定論化する。gitleaks を検証するテストは
    shutil.which / scanners._run_gitleaks を再度上書きしてスタブを指す。"""
    real_which = shutil.which

    def fake_which(name, *args, **kwargs):
        if name in ("gitleaks", "docker"):
            return None
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", fake_which)


@pytest.fixture(autouse=True)
def _isolate_trust_state(monkeypatch, tmp_path):
    """信頼判定の状態ファイルを実 $HOME に書かない(テストごとに tmp へ)。"""
    from hooks.lib import trust

    monkeypatch.setattr(trust, "STATE_PATH", tmp_path / "safe-dev-hooks-state.json")


@pytest.fixture(autouse=True)
def _clear_project_dir_env(monkeypatch):
    """CLAUDE_PROJECT_DIR がテスト実行環境から漏れ込んで基準をすり替えないようにする。"""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
