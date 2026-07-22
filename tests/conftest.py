import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))


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
