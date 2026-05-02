"""Tests for tools/retry_variants_locally.py — orchestration logic via injected runner."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.retry_variants_locally import (
    Outcome,
    count_new_variant_yamls,
    has_changes,
    run_once,
)


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class _ScriptedRunner:
    """Returns canned CompletedProcess values per call, in order. Records what was called."""

    def __init__(self, replies: list[subprocess.CompletedProcess]):
        self.replies = list(replies)
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if not self.replies:
            return _completed()
        return self.replies.pop(0)


# ---------- has_changes / count_new_variant_yamls ----------

def test_has_changes_returns_true_for_nonempty_porcelain(tmp_path):
    runner = _ScriptedRunner([_completed(stdout="?? variants/sanguozhi/wei/05.yaml\n")])
    assert has_changes(tmp_path, ["variants/", "sources/ctext/"], runner=runner)


def test_has_changes_returns_false_for_empty_porcelain(tmp_path):
    runner = _ScriptedRunner([_completed(stdout="")])
    assert not has_changes(tmp_path, ["variants/"], runner=runner)


def test_count_new_variant_yamls_counts_only_yaml_lines(tmp_path):
    porcelain = (
        "?? variants/sanguozhi/wei/05.yaml\n"
        " M variants/sanguozhi/wei/04.yaml\n"
        "?? variants/sanguozhi/shu/01.yaml\n"
        "?? sources/ctext/sanguozhi/wei/05.html\n"  # not under variants/, ignored
    )
    runner = _ScriptedRunner([_completed(stdout=porcelain)])
    # The runner only gets called once (for variants/) — sources/ctext doesn't show because
    # the function calls git with -- variants/. But our scripted runner ignores the args
    # and returns the canned reply. The canned reply has 4 lines; we want only YAML ones.
    assert count_new_variant_yamls(tmp_path, runner=runner) == 3


# ---------- run_once orchestration ----------

def test_run_once_reports_no_change_when_porcelain_empty(tmp_path):
    runner = _ScriptedRunner([
        _completed(),                          # build_variants run
        _completed(stdout=""),                 # has_changes git status → empty
    ])
    outcome = run_once(tmp_path, runner=runner)
    assert outcome == Outcome(n_new_yaml=0, committed=False, tests_passed=True, pushed=False,
                              note="no new ctext variants; ctext probably still rate-limiting")
    # No further commands beyond build + status.
    assert len(runner.calls) == 2


def test_run_once_runs_pytest_commits_then_pushes(tmp_path):
    porcelain = "?? variants/sanguozhi/wei/05.yaml\n?? variants/sanguozhi/wei/06.yaml\n"
    runner = _ScriptedRunner([
        _completed(),                       # build_variants
        _completed(stdout=porcelain),       # has_changes → non-empty
        _completed(returncode=0),           # pytest
        _completed(stdout=porcelain),       # count_new_variant_yamls
        _completed(),                       # git add
        _completed(),                       # git commit
        _completed(returncode=0),           # git push
    ])
    outcome = run_once(tmp_path, runner=runner)
    assert outcome.committed is True
    assert outcome.tests_passed is True
    assert outcome.pushed is True
    assert outcome.n_new_yaml == 2
    cmd_heads = [c[0] for c in runner.calls]
    assert cmd_heads.count("git") == 5  # status + status + add + commit + push
    assert runner.calls[-1][:4] == ["git", "push", "origin", "main"]
    # Commit message names the count.
    commit_call = runner.calls[-2]
    commit_msg = commit_call[commit_call.index("-m") + 1]
    assert "2 new ctext variant" in commit_msg


def test_run_once_commits_but_reports_push_failure_without_raising(tmp_path):
    """Push failure (network blip, auth issue) shouldn't undo the local commit."""
    porcelain = "?? variants/x.yaml\n"
    runner = _ScriptedRunner([
        _completed(),                          # build_variants
        _completed(stdout=porcelain),          # has_changes
        _completed(returncode=0),              # pytest
        _completed(stdout=porcelain),          # count_new_variant_yamls
        _completed(),                          # git add
        _completed(),                          # git commit
        _completed(returncode=1, stderr="!! [rejected] (non-fast-forward)"),  # git push fails
    ])
    outcome = run_once(tmp_path, runner=runner)
    assert outcome.committed is True
    assert outcome.pushed is False
    assert "push failed" in outcome.note


def test_run_once_skips_commit_when_pytest_fails(tmp_path):
    runner = _ScriptedRunner([
        _completed(),                                    # build_variants
        _completed(stdout="?? variants/x.yaml\n"),       # has_changes → yes
        _completed(returncode=1, stdout="FAILED"),       # pytest fails
    ])
    outcome = run_once(tmp_path, runner=runner)
    assert outcome.committed is False
    assert outcome.tests_passed is False
    assert outcome.pushed is False
    assert "refusing to commit" in outcome.note
    # Should NOT have called git add / commit / push.
    cmd_heads = [c[0] for c in runner.calls]
    assert cmd_heads.count("git") == 1  # only the initial git status check
