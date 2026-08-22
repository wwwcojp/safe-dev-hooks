"""検証ランナー。チェックを束ねて実行し、結果を .loop/evidence.jsonl に記録する。

loop-hooks の Stop ゲートから `uv run python scripts/verify.py quick` として呼ばれる。
`mutation` は mutmut を実行し、ファイル別 score を .loop/mutation-baseline.json とラチェット
比較する。`all` は quick 成功後に mutation を実行する。
`quick` の中身は CI(.github/workflows/ci.yml)と同じコマンド・同じ順序に保つこと
(tests/test_verify.py::test_quick_stage_mirrors_ci が一致を検査する)。
stdlib のみで書く(hooks/ と同じ流儀)。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# CI の「実ホームパスのリークチェック」と同一。変えるときは ci.yml も変える
LEAK_REGEX = r"/(home|Users)/(?!USER\b|alice\b|user\b)[A-Za-z_][A-Za-z0-9._-]*"

# このファイルの場所から解決したリポジトリルート。手動実行がサブディレクトリからでも
# evidence がこのリポジトリの .loop/ に落ちるよう、run_stage の既定 repo_root に使う
REPO_ROOT = Path(__file__).resolve().parent.parent


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

# mutmut の終了コード→状態(mutmut/__main__.py status_by_exit_code)のうち "killed" のもの。
# survived(0)・no tests(5/33)・timeout・suspicious 等はすべて「検出できていない」として数える
MUTATION_KILLED_CODES = frozenset({1, 3, -24})
MUTMUT_CMD = ["uv", "run", "mutmut", "run"]
BASELINE_REL = Path(".loop") / "mutation-baseline.json"


def run_stage(
    stage: str, checks: Sequence[Check] | None = None, repo_root: Path = REPO_ROOT
) -> bool:
    """チェックを順に実行し、最初の失敗で打ち切る。結果を evidence に1行追記して成否を返す。"""
    checks = STAGES[stage] if checks is None else checks
    results: list[dict[str, Any]] = []
    ok_all = True
    fail_output = ""
    for check in checks:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                check.cmd,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                cwd=repo_root,
                check=False,
            )
            ms = int((time.monotonic() - start) * 1000)
            ok = proc.returncode in check.ok_codes
            output = proc.stdout + proc.stderr
        except OSError as e:
            ms = int((time.monotonic() - start) * 1000)
            ok = False
            output = f"{check.cmd[0]}: {e}"
        results.append({"name": check.name, "ok": ok, "ms": ms})
        if not ok:
            ok_all = False
            fail_output = output[-FAIL_OUTPUT_TAIL:]
            break
    _append_evidence(repo_root, stage, ok_all, results)
    if not ok_all:
        print(fail_output, file=sys.stderr)
    return ok_all


def _git_rev(repo_root: Path) -> str:
    rev = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, encoding="utf-8", errors="replace", cwd=repo_root, check=False,
    ).stdout.strip()
    if not rev:
        return "unknown"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, encoding="utf-8", errors="replace", cwd=repo_root, check=False,
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mutation_scores(repo_root: Path) -> dict[str, dict[str, Any]]:
    """mutants/ 配下の *.py.meta からファイル別 {score, killed, total} を集計する。

    キーはリポジトリ相対パス(例: hooks/lib/patterns.py)。
    """
    mutants = Path(repo_root) / "mutants"
    scores: dict[str, dict[str, Any]] = {}
    for meta in sorted(mutants.rglob("*.py.meta")):
        codes = json.loads(meta.read_text(encoding="utf-8"))["exit_code_by_key"]
        if not codes:
            continue
        total = len(codes)
        killed = sum(1 for c in codes.values() if c in MUTATION_KILLED_CODES)
        rel = meta.relative_to(mutants).as_posix()[: -len(".meta")]
        scores[rel] = {"score": round(killed / total * 100, 1), "killed": killed, "total": total}
    return scores


def check_mutation_baseline(
    repo_root: Path, scores: dict[str, dict[str, Any]]
) -> tuple[bool, list[str]]:
    """ファイル別ラチェット。(ok, 問題の一覧) を返す。ok で変化があれば baseline を書き換える。

    - 下回ったファイル / baseline にあって結果に無いファイル → fail(全件列挙)
    - 新規ファイルは登録、上回った分だけ更新。変化が無ければファイルに触らない
    """
    path = Path(repo_root) / BASELINE_REL
    baseline: dict[str, float] = {}
    if path.exists():
        baseline = json.loads(path.read_text(encoding="utf-8")).get("files", {})
    problems: list[str] = []
    for f, b in sorted(baseline.items()):
        if f not in scores:
            problems.append(
                f"{f}: baseline {b} にあるが今回の結果に無い(only_mutate から外れている?"
                " 対象の縮小は baseline を手で外す必要がある)"
            )
        elif scores[f]["score"] < b:
            problems.append(f"{f}: score {scores[f]['score']} < baseline {b}")
    if problems:
        return False, problems
    new = {f: max(s["score"], baseline.get(f, 0.0)) for f, s in scores.items()}
    if new != baseline:
        path.parent.mkdir(exist_ok=True)
        payload = {"files": dict(sorted(new.items())), "updated": _utc_now()}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, []


def _run_mutmut(repo_root: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            MUTMUT_CMD, capture_output=True, encoding="utf-8", errors="replace",
            cwd=repo_root, check=False,
        )
    except OSError as e:
        return 1, f"{MUTMUT_CMD[0]}: {e}"
    return proc.returncode, proc.stdout + proc.stderr


def run_mutation(
    repo_root: Path = REPO_ROOT, runner: Callable[[Path], tuple[int, str]] | None = None
) -> bool:
    """mutmut を実行し、ファイル別 score を baseline とラチェット比較して evidence に記録する。"""
    run = runner or _run_mutmut
    start = time.monotonic()
    code, output = run(repo_root)
    checks: list[dict[str, Any]] = [
        {"name": "mutmut", "ok": code == 0, "ms": int((time.monotonic() - start) * 1000)}
    ]
    if code != 0:
        _append_evidence(repo_root, "mutation", False, checks)
        print(output[-FAIL_OUTPUT_TAIL:], file=sys.stderr)
        return False
    scores = mutation_scores(repo_root)
    if not scores:
        checks.append({"name": "baseline", "ok": False, "scores": {}})
        _append_evidence(repo_root, "mutation", False, checks)
        print(
            "mutants/ に変異結果(*.py.meta)が無い。[tool.mutmut] の only_mutate を確認する",
            file=sys.stderr,
        )
        return False
    ok, problems = check_mutation_baseline(repo_root, scores)
    checks.append({"name": "baseline", "ok": ok, "scores": scores})
    _append_evidence(repo_root, "mutation", ok, checks)
    if ok:
        for f, s in sorted(scores.items()):
            print(f"{f}: {s['score']} ({s['killed']}/{s['total']})")
    else:
        print(
            "mutation score が baseline を下回りました:\n  " + "\n  ".join(problems)
            + "\n生き残りは `uv run mutmut results` / `uv run mutmut show <id>` で確認し、"
            "テストを補強してください(等価変異のみ理由付き `# pragma: no mutate`)。",
            file=sys.stderr,
        )
    return ok


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "quick"
    if stage == "mutation":
        sys.exit(0 if run_mutation() else 1)
    if stage == "all":
        sys.exit(0 if (run_stage("quick") and run_mutation()) else 1)
    if stage not in STAGES:
        raise SystemExit(
            f"unknown stage: {stage} (available: {', '.join(STAGES)}, mutation, all)"
        )
    sys.exit(0 if run_stage(stage) else 1)


if __name__ == "__main__":
    main()
