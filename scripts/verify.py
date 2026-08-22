"""検証ランナー。チェックを束ねて実行し、結果を .loop/evidence.jsonl に記録する。

loop-hooks の Stop ゲートから `uv run python scripts/verify.py quick` として呼ばれる。
`quick` の中身は CI(.github/workflows/ci.yml)と同じコマンド・同じ順序に保つこと
(tests/test_verify.py::test_quick_stage_mirrors_ci が一致を検査する)。
stdlib のみで書く(hooks/ と同じ流儀)。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# CI の「実ホームパスのリークチェック」と同一。変えるときは ci.yml も変える
LEAK_REGEX = r"/(home|Users)/(?!USER\b|alice\b|user\b)[A-Za-z_][A-Za-z0-9._-]*"


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]
    # 終了コードがこの集合に含まれれば成功。git grep は「不一致=1」が成功なので反転に使う
    ok_codes: frozenset[int] = frozenset({0})


STAGES: dict[str, list[Check]] = {
    "quick": [
        Check("leak", ["git", "grep", "-nP", LEAK_REGEX, "--"], ok_codes=frozenset({1})),
        Check("lint", ["uv", "run", "ruff", "check", "hooks", "tests", "scripts"]),
        Check("tests", ["uv", "run", "pytest", "-q"]),
    ],
}

FAIL_OUTPUT_TAIL = 2000


def run_stage(
    stage: str, checks: Sequence[Check] | None = None, repo_root: Path = Path(".")
) -> bool:
    """チェックを順に実行し、最初の失敗で打ち切る。結果を evidence に1行追記して成否を返す。"""
    checks = STAGES[stage] if checks is None else checks
    results: list[dict[str, Any]] = []
    ok_all = True
    fail_output = ""
    for check in checks:
        start = time.monotonic()
        proc = subprocess.run(
            check.cmd, capture_output=True, text=True, cwd=repo_root, check=False
        )
        ms = int((time.monotonic() - start) * 1000)
        ok = proc.returncode in check.ok_codes
        results.append({"name": check.name, "ok": ok, "ms": ms})
        if not ok:
            ok_all = False
            fail_output = (proc.stdout + proc.stderr)[-FAIL_OUTPUT_TAIL:]
            break
    _append_evidence(repo_root, stage, ok_all, results)
    if not ok_all:
        print(fail_output, file=sys.stderr)
    return ok_all


def _git_rev(repo_root: Path) -> str:
    rev = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, cwd=repo_root, check=False,
    ).stdout.strip()
    if not rev:
        return "unknown"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=repo_root, check=False,
    ).stdout.strip()
    return rev + ("+dirty" if dirty else "")


def _append_evidence(
    repo_root: Path, stage: str, ok: bool, checks: Sequence[dict[str, Any]]
) -> None:
    loop_dir = Path(repo_root) / ".loop"
    loop_dir.mkdir(exist_ok=True)
    line = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "rev": _git_rev(repo_root),
        "stage": stage,
        "pass": ok,
        "checks": list(checks),
    }
    with open(loop_dir / "evidence.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "quick"
    if stage not in STAGES:
        raise SystemExit(f"unknown stage: {stage} (available: {', '.join(STAGES)})")
    sys.exit(0 if run_stage(stage) else 1)


if __name__ == "__main__":
    main()
