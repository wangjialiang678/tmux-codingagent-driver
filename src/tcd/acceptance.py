"""Acceptance contracts: what makes a job *done*, as opposed to idle.

tcd's native unit is a turn. A turn going idle is not a finished task — agents
spawn sub-agents and return to an empty prompt mid-work, so every caller ended
up reinventing "check the deliverables" as a convention, and conventions get
skipped.

A contract moves that judgement into tcd: the caller declares at ``start`` what
must be true for the task to count as done, and tcd evaluates it. Any task whose
completion can be stated this way needs no human to confirm it.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tcd.job import Job

logger = logging.getLogger(__name__)

# A stuck acceptance command must not hang the caller's poll loop.
COMMAND_TIMEOUT_SECONDS = 120


@dataclass
class Check:
    """One clause of the contract and how it evaluated."""

    kind: str  # "file" | "command" | "commit"
    target: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "target": self.target, "ok": self.ok, "detail": self.detail}


@dataclass
class AcceptanceResult:
    """Verdict for a whole contract."""

    state: str  # "complete" | "incomplete" | "unchecked"
    checks: list[Check] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.state == "complete"

    def to_dict(self) -> dict:
        return {"task_state": self.state, "checks": [c.to_dict() for c in self.checks]}

    def summary(self) -> str:
        if self.state == "unchecked":
            return "no acceptance contract declared"
        failed = [c for c in self.checks if not c.ok]
        if not failed:
            return f"all {len(self.checks)} acceptance check(s) passed"
        return f"{len(failed)}/{len(self.checks)} acceptance check(s) failed"


def has_contract(job: Job) -> bool:
    return bool(job.acceptance_files or job.acceptance_commands or job.acceptance_require_commit)


def evaluate(job: Job) -> AcceptanceResult:
    """Evaluate the job's acceptance contract.

    Never raises: a contract that cannot be evaluated is reported as a failed
    check, because silently treating it as passed is how an unfinished task gets
    accepted.
    """
    if not has_contract(job):
        return AcceptanceResult(state="unchecked")

    workdir = Path(job.cwd)
    checks: list[Check] = []

    for rel in job.acceptance_files:
        path = rel if Path(rel).is_absolute() else str(workdir / rel)
        exists = Path(path).exists()
        checks.append(
            Check("file", rel, exists, "" if exists else "not found")
        )

    for cmd in job.acceptance_commands:
        checks.append(_run_command(cmd, workdir))

    if job.acceptance_require_commit:
        checks.append(_check_commit(job))

    state = "complete" if all(c.ok for c in checks) else "incomplete"
    return AcceptanceResult(state=state, checks=checks)


def _run_command(cmd: str, workdir: Path) -> Check:
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return Check("command", cmd, False, f"timed out after {COMMAND_TIMEOUT_SECONDS}s")
    except OSError as exc:
        return Check("command", cmd, False, str(exc))

    if result.returncode == 0:
        return Check("command", cmd, True)
    tail = (result.stderr or result.stdout or "").strip().splitlines()
    detail = f"exit {result.returncode}"
    if tail:
        detail += f": {tail[-1][:200]}"
    return Check("command", cmd, False, detail)


def _check_commit(job: Job) -> Check:
    """Require the job's worktree branch to carry work not already on HEAD."""
    if not job.worktree_branch:
        return Check("commit", "-", False, "job has no worktree branch")

    from tcd.worktree import branch_has_new_commits, get_main_repo_root

    try:
        repo_root = (
            Path(job.worktree_repo_root)
            if job.worktree_repo_root
            else get_main_repo_root(job.cwd)
        )
        has_new = branch_has_new_commits(repo_root, job.worktree_branch)
    except Exception as exc:
        logger.warning("acceptance %s: commit check failed", job.id, exc_info=True)
        return Check("commit", job.worktree_branch, False, f"could not compare: {exc}")

    return Check(
        "commit",
        job.worktree_branch,
        has_new,
        "" if has_new else "no commits beyond HEAD (agent likely never committed)",
    )
