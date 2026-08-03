"""Tests for acceptance contracts.

The contract exists because "turn went idle" is not "task is done" — agents
spawn sub-agents and return to an empty prompt mid-work. These tests pin the
distinction down.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tcd.acceptance import evaluate, has_contract
from tcd.job import Job


def _job(tmp_path: Path, **kwargs) -> Job:
    return Job(
        id="job1",
        provider="codex",
        status="running",
        prompt="task",
        cwd=str(tmp_path),
        tmux_session="tcd-codex-job1",
        **kwargs,
    )


def test_no_contract_is_unchecked_not_complete(tmp_path):
    """Absence of a contract must never read as success."""
    job = _job(tmp_path)
    assert not has_contract(job)

    result = evaluate(job)
    assert result.state == "unchecked"
    assert result.complete is False


def test_required_file_present(tmp_path):
    (tmp_path / "out.txt").write_text("done\n")
    result = evaluate(_job(tmp_path, acceptance_files=["out.txt"]))

    assert result.state == "complete"
    assert result.checks[0].ok


def test_required_file_missing_fails_with_reason(tmp_path):
    result = evaluate(_job(tmp_path, acceptance_files=["never_written.py"]))

    assert result.state == "incomplete"
    assert result.checks[0].ok is False
    assert "not found" in result.checks[0].detail


def test_required_command_exit_code_decides(tmp_path):
    ok = evaluate(_job(tmp_path, acceptance_commands=["exit 0"]))
    assert ok.state == "complete"

    bad = evaluate(_job(tmp_path, acceptance_commands=["exit 3"]))
    assert bad.state == "incomplete"
    assert "exit 3" in bad.checks[0].detail


def test_command_failure_detail_carries_output(tmp_path):
    result = evaluate(_job(tmp_path, acceptance_commands=["echo 'boom: 2 tests failed' >&2; exit 1"]))

    assert result.state == "incomplete"
    assert "boom" in result.checks[0].detail


def test_command_runs_in_the_job_working_directory(tmp_path):
    (tmp_path / "marker").write_text("x")
    result = evaluate(_job(tmp_path, acceptance_commands=["test -f marker"]))

    assert result.state == "complete"


def test_command_timeout_is_a_failure_not_a_hang(tmp_path, monkeypatch):
    import tcd.acceptance as acceptance

    monkeypatch.setattr(acceptance, "COMMAND_TIMEOUT_SECONDS", 1)
    result = evaluate(_job(tmp_path, acceptance_commands=["sleep 30"]))

    assert result.state == "incomplete"
    assert "timed out" in result.checks[0].detail


def test_all_clauses_must_pass(tmp_path):
    (tmp_path / "present.txt").write_text("x")
    result = evaluate(
        _job(
            tmp_path,
            acceptance_files=["present.txt", "absent.txt"],
            acceptance_commands=["exit 0"],
        )
    )

    assert result.state == "incomplete"
    assert [c.ok for c in result.checks] == [True, False, True]
    assert "1/3" in result.summary()


# ---------------------------------------------------------------------------
# require-commit: the documented failure mode is an agent that does the work
# and never commits it
# ---------------------------------------------------------------------------

@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, check=True)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (repo / "base.txt").write_text("base\n")
    run("add", "-A")
    run("commit", "-qm", "init")
    return repo


def test_require_commit_fails_when_agent_never_committed(git_repo):
    subprocess.run(["git", "branch", "tcd/job1"], cwd=git_repo, check=True, capture_output=True)
    job = Job(
        id="job1", provider="codex", status="running", prompt="t",
        cwd=str(git_repo), tmux_session="s",
        worktree_branch="tcd/job1", worktree_repo_root=str(git_repo),
        acceptance_require_commit=True,
    )

    result = evaluate(job)
    assert result.state == "incomplete"
    assert "never committed" in result.checks[0].detail


def test_require_commit_passes_once_work_is_committed(git_repo):
    run = lambda *a: subprocess.run(["git", *a], cwd=git_repo, capture_output=True, check=True)
    run("checkout", "-q", "-b", "tcd/job1")
    (git_repo / "feature.py").write_text("print('hi')\n")
    run("add", "-A")
    run("commit", "-qm", "feat")
    run("checkout", "-q", "-")

    job = Job(
        id="job1", provider="codex", status="running", prompt="t",
        cwd=str(git_repo), tmux_session="s",
        worktree_branch="tcd/job1", worktree_repo_root=str(git_repo),
        acceptance_require_commit=True,
    )

    assert evaluate(job).state == "complete"


def test_require_commit_without_branch_or_baseline_fails_loudly(tmp_path):
    """Neither shape available means we cannot tell — that is a failure, not a pass."""
    result = evaluate(_job(tmp_path, acceptance_require_commit=True))

    assert result.state == "incomplete"
    assert "no dispatch baseline" in result.checks[0].detail


# ---------------------------------------------------------------------------
# Caller-owned worktree: auto-dev creates its own, so tcd must not require a
# branch of its own to judge whether the agent committed
# ---------------------------------------------------------------------------

def _head(repo) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_require_commit_uses_dispatch_baseline_when_caller_owns_the_worktree(git_repo):
    job = Job(
        id="job1", provider="codex", status="running", prompt="t",
        cwd=str(git_repo), tmux_session="s",
        acceptance_require_commit=True,
        acceptance_base_commit=_head(git_repo),
    )

    unchanged = evaluate(job)
    assert unchanged.state == "incomplete"
    assert "HEAD unchanged" in unchanged.checks[0].detail

    (git_repo / "feature.py").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "feat"], cwd=git_repo, check=True, capture_output=True)

    assert evaluate(job).state == "complete"


def test_require_commit_reports_non_git_cwd(tmp_path):
    job = _job(tmp_path, acceptance_require_commit=True, acceptance_base_commit="a" * 40)
    result = evaluate(job)

    assert result.state == "incomplete"
    assert "not a git repository" in result.checks[0].detail
