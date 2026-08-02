"""Git worktree primitives for parallel job isolation."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


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


def get_main_repo_root(path: str | Path) -> Path:
    """Get the main/shared repo root for a checkout or linked worktree path."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=str(path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"Not a git repo: {path}")

    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (Path(path) / common_dir).resolve()
    return common_dir.parent


def is_dirty(repo_path: str | Path) -> bool:
    """Check if the working directory has uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def auto_stash(repo_path: str | Path, message: str = "tcd: auto-stash before worktree") -> str | None:
    """Stash uncommitted changes. Returns stash ref on success, None if nothing to stash."""
    if not is_dirty(repo_path):
        return None

    result = subprocess.run(
        ["git", "stash", "push", "--include-untracked", "-m", message],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git stash failed: {result.stderr.strip()}")

    # Get the stash ref (stash@{0} after push)
    ref_result = subprocess.run(
        ["git", "stash", "list", "--max-count=1", "--format=%H"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    return ref_result.stdout.strip() or "stash@{0}"


def find_stash_selector(repo_path: str | Path, ref: str) -> str | None:
    """Resolve a stash commit SHA to its current ``stash@{n}`` selector.

    Stash selectors are positional, so they shift whenever any stash is pushed
    or popped. Parallel worktree jobs each push their own stash, so the ref
    recorded at ``tcd start`` time must be re-resolved right before popping.
    Returns None if the stash is no longer on the stack.
    """
    if not ref:
        return None
    result = subprocess.run(
        ["git", "stash", "list", "--format=%H %gd"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        sha, _, selector = line.strip().partition(" ")
        if sha == ref and selector:
            return selector
    return None


def stash_pop(repo_path: str | Path, ref: str | None = None) -> bool:
    """Restore a stash. Returns True on success.

    With *ref* (a stash commit SHA, as recorded by :func:`auto_stash`) the exact
    stash is resolved and popped. Popping the top of the stack instead would
    hand job A's stash to job B whenever two worktree jobs overlap — the stack
    is shared by the whole repo, not per job.

    Without *ref* this falls back to popping the top, which is only correct for
    jobs created before the ref was recorded.
    """
    args = ["git", "stash", "pop"]
    if ref:
        selector = find_stash_selector(repo_path, ref)
        if selector is None:
            logger.warning("stash %s no longer on the stack in %s", ref[:8], repo_path)
            return False
        args.append(selector)

    result = subprocess.run(
        args,
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("git stash pop failed: %s", result.stderr.strip())
    return result.returncode == 0


def worktree_is_dirty(worktree_path: str | Path) -> bool:
    """Whether a worktree has uncommitted changes the agent would lose."""
    wt = Path(worktree_path)
    if not wt.exists():
        return False
    try:
        return is_dirty(wt)
    except Exception:
        # If we cannot tell, assume there is something to protect.
        return True


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


def remove_worktree(worktree_path: str | Path) -> bool:
    """Remove a worktree and prune.

    Returns True if the worktree was successfully removed (or was already gone).
    Returns False if git worktree remove failed (callers should skip branch cleanup).
    """
    wt = Path(worktree_path)
    if not wt.exists():
        return True  # Already gone = success

    try:
        common_dir_result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(wt),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        # Directory disappeared between exists() check and subprocess call
        logger.warning("Worktree %s disappeared during cleanup", wt)
        return True  # Already gone = success

    if common_dir_result.returncode != 0:
        logger.warning("Unable to locate repo for worktree %s, skipping removal", wt)
        return False

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
    remove_succeeded = result.returncode == 0
    if not remove_succeeded:
        logger.warning("git worktree remove failed: %s", result.stderr.strip())

    prune = subprocess.run(
        ["git", "worktree", "prune"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if prune.returncode != 0:
        logger.warning("git worktree prune failed: %s", prune.stderr.strip())

    return remove_succeeded


class MergeResult:
    """Result of a merge_branch() call."""

    def __init__(self, success: bool, *, noop: bool = False, stdout: str = "", stderr: str = ""):
        self.success = success
        self.noop = noop  # True when "Already up to date"
        self.stdout = stdout
        self.stderr = stderr

    def __bool__(self) -> bool:
        return self.success


def merge_branch(
    repo_path: str | Path,
    branch: str,
    *,
    strategy: str = "merge",  # "merge" | "squash"
) -> MergeResult:
    """Merge a branch into the current HEAD.

    Returns MergeResult with success/noop/stdout info.
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
    noop = "Already up to date" in result.stdout
    return MergeResult(
        success=result.returncode == 0,
        noop=noop,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
    )


def branch_has_new_commits(repo_path: str | Path, branch: str) -> bool:
    """Check if branch has commits not reachable from HEAD.

    Returns True if branch has diverged (has new commits to merge).
    Returns False if branch is already fully merged (merge would be a noop).
    """
    # git log HEAD..branch --oneline: shows commits in branch but not in HEAD
    result = subprocess.run(
        ["git", "log", f"HEAD..{branch}", "--oneline", "--max-count=1"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # If the command fails (e.g. branch doesn't exist), assume there might be commits
        # so we don't block the merge — let merge_branch() handle the error.
        logger.warning("branch_has_new_commits: git log failed for branch=%s: %s", branch, result.stderr.strip())
        return True
    return bool(result.stdout.strip())


def delete_branch(repo_path: str | Path, branch: str, *, force: bool = False) -> None:
    """Delete a local branch after merge."""
    delete_flag = "-D" if force else "-d"
    result = subprocess.run(
        ["git", "branch", delete_flag, branch],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git branch {delete_flag} failed: {result.stderr.strip()}")
