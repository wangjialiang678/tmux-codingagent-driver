"""Git worktree primitives for parallel job isolation."""

from __future__ import annotations

import fcntl
import logging
import re
import subprocess
import time
from contextlib import contextmanager
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


STASH_MESSAGE_PREFIX = "tcd: auto-stash before worktree"


@contextmanager
def repo_lock(repo_path: str | Path, *, timeout: float = 30.0):
    """Serialise stash operations on one repo across tcd processes.

    The stash stack is repo-wide and positional, so two jobs racing on the same
    repo can misattribute or pop each other's entries. Every read-then-act
    sequence on the stack (`push` then identify, resolve then `pop`) has to run
    inside this lock to be safe.
    """
    from tcd.config import ensure_dirs, repo_lock_path

    ensure_dirs()
    lock_file = repo_lock_path(repo_path)
    handle = open(lock_file, "w")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise WorktreeError(
                        f"timed out waiting for the stash lock on {repo_path}; "
                        f"another tcd process is mid-operation"
                    )
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _stash_entries(repo_path: str | Path) -> list[tuple[str, str, str]]:
    """Return (sha, selector, message) for each stash, newest first."""
    result = subprocess.run(
        ["git", "stash", "list", "--format=%H%x1f%gd%x1f%gs"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    entries = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            entries.append((parts[0], parts[1], parts[2]))
    return entries


def auto_stash(repo_path: str | Path, job_id: str | None = None) -> str | None:
    """Stash uncommitted changes, tagged with *job_id*. Returns the stash SHA.

    Returns None when there was nothing to stash — including the case where a
    concurrent job stashed the same changes first, since `git stash push` then
    succeeds without creating an entry. Identifying the entry by its job-tagged
    message rather than by "top of the stack" is what keeps two jobs racing on
    one repo from claiming each other's stash.
    """
    message = STASH_MESSAGE_PREFIX
    if job_id:
        message = f"{STASH_MESSAGE_PREFIX} [{job_id}]"

    with repo_lock(repo_path):
        if not is_dirty(repo_path):
            return None

        before = {sha for sha, _, _ in _stash_entries(repo_path)}
        result = subprocess.run(
            ["git", "stash", "push", "--include-untracked", "-m", message],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise WorktreeError(f"git stash failed: {result.stderr.strip()}")

        for sha, _, entry_message in _stash_entries(repo_path):
            if sha not in before and message in entry_message:
                return sha

        # `git stash push` can succeed without creating an entry (nothing left
        # to stash). Claiming the previous top here is how another job's work
        # used to get attributed to this one.
        logger.info("auto_stash: nothing was stashed in %s", repo_path)
        return None


_LEGACY_SELECTOR_RE = re.compile(r"^stash@\{\d+\}$")


def find_stash_selector(repo_path: str | Path, ref: str) -> str | None:
    """Resolve a stash commit SHA to its current ``stash@{n}`` selector.

    Stash selectors are positional, so they shift whenever any stash is pushed
    or popped. Parallel worktree jobs each push their own stash, so the ref
    recorded at ``tcd start`` time must be re-resolved right before popping.
    Returns None if the stash is no longer on the stack.

    A *ref* that is already a selector comes from a pre-0.4.0 job, where
    ``auto_stash`` fell back to persisting the literal ``stash@{0}``. It is
    honoured only if the stack still has an entry at that position, and only
    because there is nothing better to go on — the position may since have
    shifted, so it is reported as best-effort.
    """
    if not ref:
        return None

    entries = _stash_entries(repo_path)

    if _LEGACY_SELECTOR_RE.match(ref):
        selectors = {selector for _, selector, _ in entries}
        if ref in selectors:
            logger.warning(
                "job recorded the legacy positional ref %s; restoring by position, "
                "which may not be the same stash it saved",
                ref,
            )
            return ref
        return None

    for sha, selector, _ in entries:
        if sha == ref:
            return selector
    return None


def stash_exists(repo_path: str | Path, ref: str) -> bool:
    """Whether *ref* still refers to an entry on the stash stack."""
    try:
        return find_stash_selector(repo_path, ref) is not None
    except Exception:
        return False


def stash_pop(repo_path: str | Path, ref: str | None = None) -> bool:
    """Restore a stash. Returns True on success.

    With *ref* (a stash commit SHA, as recorded by :func:`auto_stash`) the exact
    stash is resolved and popped. Popping the top of the stack instead would
    hand job A's stash to job B whenever two worktree jobs overlap — the stack
    is shared by the whole repo, not per job.

    Resolution and the pop itself run under the repo lock: they are separate
    git processes, so without it a concurrent push or pop can reorder the stack
    in between and the resolved position would name someone else's entry.

    Without *ref* this falls back to popping the top, which is only correct for
    jobs created before the ref was recorded.
    """
    with repo_lock(repo_path):
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
