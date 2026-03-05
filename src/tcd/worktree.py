"""Git worktree primitives for parallel job isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeError(Exception):
    """Git worktree operation failed."""


def is_git_repo(path: str | Path) -> bool:
    """Check if path is inside a git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(path),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def get_repo_root(path: str | Path) -> Path:
    """Get the root of the git repository containing path."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"Not a git repo: {path}")
    return Path(result.stdout.strip())


def create_worktree(repo_path: str | Path, branch_name: str) -> Path:
    """Create a worktree in a sibling directory.

    Creates: <repo_parent>/<repo_name>-wt-<branch_name>/
    Branch: tcd/<branch_name>

    Returns the worktree path.
    Raises WorktreeError on failure.
    """
    repo = get_repo_root(repo_path)
    wt_path = repo.parent / f"{repo.name}-wt-{branch_name}"
    full_branch = f"tcd/{branch_name}"

    result = subprocess.run(
        ["git", "worktree", "add", "-b", full_branch, str(wt_path)],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git worktree add failed: {result.stderr.strip()}")
    return wt_path


def remove_worktree(worktree_path: str | Path) -> None:
    """Remove a worktree and prune."""
    wt = Path(worktree_path)
    if not wt.exists():
        return

    common_dir_result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=str(wt),
        capture_output=True,
        text=True,
    )
    if common_dir_result.returncode != 0:
        raise WorktreeError(f"Unable to locate repo for worktree: {wt}")

    common_dir = Path(common_dir_result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (wt / common_dir).resolve()
    repo_root = common_dir.parent

    result = subprocess.run(
        ["git", "worktree", "remove", str(wt), "--force"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git worktree remove failed: {result.stderr.strip()}")

    prune = subprocess.run(
        ["git", "worktree", "prune"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if prune.returncode != 0:
        raise WorktreeError(f"git worktree prune failed: {prune.stderr.strip()}")


def merge_branch(
    repo_path: str | Path,
    branch: str,
    *,
    strategy: str = "merge",  # "merge" | "squash"
) -> bool:
    """Merge a branch into the current HEAD.

    Returns True on success, False on conflict.
    Does NOT auto-resolve conflicts.
    """
    if strategy not in {"merge", "squash"}:
        raise ValueError(f"Unknown merge strategy: {strategy}")

    cmd = ["git", "merge", branch]
    if strategy == "squash":
        cmd = ["git", "merge", "--squash", branch]

    result = subprocess.run(
        cmd,
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def delete_branch(repo_path: str | Path, branch: str) -> None:
    """Delete a local branch after merge."""
    subprocess.run(
        ["git", "branch", "-d", branch],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
