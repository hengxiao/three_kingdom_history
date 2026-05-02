"""Local cron-friendly wrapper for retrying ctext variant fetches.

Designed to be invoked from a crontab while the user's machine is running.
On each run:

  1. Invoke `build_variants` in auto mode (only fetches chapters whose
     sources/ctext/<NN>.html is missing — already-cached snapshots are reused).
  2. Inspect git status for new/changed files under variants/ and sources/ctext/.
  3. If anything new: run pytest. If tests pass, stage and commit.
  4. If nothing new (typical: ctext still 403'ing), exit cleanly with status 0.
  5. If tests failed, exit with non-zero status and skip the commit.

Why a separate wrapper instead of a bash one-liner: keeps the orchestration
testable per project rules. The cron entry just runs `python -m tools.retry_variants_locally`.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Outcome:
    n_new_yaml: int
    committed: bool
    tests_passed: bool
    pushed: bool
    note: str


# Indirection so tests can inject a fake runner without touching subprocess.
RunnerResult = subprocess.CompletedProcess
Runner = Callable[..., RunnerResult]


def _default_runner(*args, **kwargs) -> RunnerResult:
    return subprocess.run(*args, **kwargs)


def count_new_variant_yamls(repo_root: Path, *, runner: Runner = _default_runner) -> int:
    """Count untracked or modified .yaml files under variants/ via git status."""
    proc = runner(
        ["git", "status", "--porcelain", "--", "variants/"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    n = 0
    for line in proc.stdout.splitlines():
        # Lines look like "?? variants/.../foo.yaml" or " M variants/.../foo.yaml".
        if line.endswith(".yaml"):
            n += 1
    return n


def has_changes(repo_root: Path, paths: list[str], *, runner: Runner = _default_runner) -> bool:
    proc = runner(
        ["git", "status", "--porcelain", "--"] + paths,
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return bool(proc.stdout.strip())


def run_once(
    repo_root: Path,
    *,
    runner: Runner = _default_runner,
) -> Outcome:
    """One end-to-end retry pass. Returns Outcome describing what happened."""
    # Step 1: try to fetch missing chapters and (re)build variants.
    runner(
        [sys.executable, "-m", "tools.build_variants", "--sleep", "5"],
        cwd=repo_root, check=False,
    )

    # Step 2: did anything change?
    paths = ["variants/", "sources/ctext/"]
    if not has_changes(repo_root, paths, runner=runner):
        return Outcome(n_new_yaml=0, committed=False, tests_passed=True, pushed=False,
                       note="no new ctext variants; ctext probably still rate-limiting")

    # Step 3: run tests before committing.
    test_proc = runner(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo_root, check=False,
    )
    if test_proc.returncode != 0:
        return Outcome(n_new_yaml=0, committed=False, tests_passed=False, pushed=False,
                       note="pytest failed; refusing to commit. Inspect manually.")

    # Step 4: stage and commit.
    n_new = count_new_variant_yamls(repo_root, runner=runner)
    runner(["git", "add", "variants/", "sources/ctext/"], cwd=repo_root, check=True)
    msg = (
        f"Retry: {n_new} new ctext variant chapter(s) fetched\n\n"
        "Auto-commit by tools/retry_variants_locally.py — ctext rate limit lifted."
    )
    runner(["git", "commit", "-m", msg], cwd=repo_root, check=True)

    # Step 5: push to origin/main. Don't fail the whole orchestrator if push errors
    # (e.g. transient network) — the commit is already on disk; next retry's push
    # will catch up.
    pushed = False
    push_proc = runner(["git", "push", "origin", "main"], cwd=repo_root, check=False)
    if push_proc.returncode == 0:
        pushed = True
        note = f"committed and pushed {n_new} new variant file(s)"
    else:
        note = (f"committed {n_new} new variant file(s) locally; push failed "
                f"(rc={push_proc.returncode}) — next retry will try again")
    return Outcome(n_new_yaml=n_new, committed=True, tests_passed=True, pushed=pushed,
                   note=note)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    args = p.parse_args(argv)

    outcome = run_once(args.repo_root)
    print(f"[retry_variants] {outcome.note}")
    if not outcome.tests_passed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
