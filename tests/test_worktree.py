"""Integration tests for git worktree primitives."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tcd.worktree import (
    WorktreeError,
    auto_stash,
    create_worktree,
    delete_branch,
    get_main_repo_root,
    get_repo_root,
    is_git_repo,
    _stash_entries,
    find_stash_selector,
    merge_branch,
    repo_lock,
    stash_exists,
    remove_worktree,
    stash_pop,
    worktree_is_dirty,
)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        joined = " ".join(args)
        raise AssertionError(f"git {joined} failed: {result.stderr.strip()}")
    return result


def _write_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, message: str) -> None:
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", message)


def _current_branch(repo: Path) -> str:
    return _run_git(repo, "branch", "--show-current").stdout.strip()


@pytest.fixture()
def git_repo():
    base_dir = Path(tempfile.mkdtemp(prefix="tcd-worktree-tests-"))
    repo = base_dir / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "TCD Test")
    _run_git(repo, "config", "user.email", "tcd@example.com")

    _write_file(repo / "README.md", "# test repo\n")
    _write_file(repo / "shared.txt", "base\n")
    _commit_all(repo, "initial commit")

    try:
        yield repo
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_is_git_repo_true(git_repo: Path):
    assert is_git_repo(git_repo)


def test_is_git_repo_false():
    not_repo = Path(tempfile.mkdtemp(prefix="tcd-not-git-"))
    try:
        assert not is_git_repo(not_repo)
    finally:
        shutil.rmtree(not_repo, ignore_errors=True)


def test_get_repo_root(git_repo: Path):
    nested = git_repo / "nested" / "child"
    nested.mkdir(parents=True, exist_ok=True)
    assert get_repo_root(nested).resolve() == git_repo.resolve()


def test_get_repo_root_not_git():
    not_repo = Path(tempfile.mkdtemp(prefix="tcd-not-git-"))
    try:
        with pytest.raises(WorktreeError):
            get_repo_root(not_repo)
    finally:
        shutil.rmtree(not_repo, ignore_errors=True)


def test_get_main_repo_root(git_repo: Path):
    nested = git_repo / "nested-main" / "child"
    nested.mkdir(parents=True, exist_ok=True)
    assert get_main_repo_root(nested).resolve() == git_repo.resolve()


def test_get_main_repo_root_from_worktree(git_repo: Path):
    worktree = create_worktree(git_repo, "main-root")
    assert get_main_repo_root(worktree).resolve() == git_repo.resolve()


def test_create_worktree(git_repo: Path):
    worktree = create_worktree(git_repo, "create")
    assert worktree.exists()
    assert worktree.is_dir()
    assert _current_branch(worktree) == "tcd/create"


def test_create_worktree_duplicate(git_repo: Path):
    create_worktree(git_repo, "dup")
    with pytest.raises(WorktreeError):
        create_worktree(git_repo, "dup")


def test_remove_worktree(git_repo: Path):
    worktree = create_worktree(git_repo, "remove")
    assert worktree.exists()
    remove_worktree(worktree)
    assert not worktree.exists()


def test_remove_worktree_nonexistent():
    base = Path(tempfile.mkdtemp(prefix="tcd-worktree-missing-"))
    missing = base / "does-not-exist"
    try:
        remove_worktree(missing)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_merge_branch(git_repo: Path):
    main = _current_branch(git_repo)
    _run_git(git_repo, "checkout", "-b", "feature-merge")
    _write_file(git_repo / "merge.txt", "merged\n")
    _commit_all(git_repo, "feature merge change")
    _run_git(git_repo, "checkout", main)

    assert merge_branch(git_repo, "feature-merge")
    assert (git_repo / "merge.txt").read_text(encoding="utf-8") == "merged\n"


def test_merge_branch_squash(git_repo: Path):
    main = _current_branch(git_repo)
    _run_git(git_repo, "checkout", "-b", "feature-squash")
    _write_file(git_repo / "squash.txt", "squashed\n")
    _commit_all(git_repo, "feature squash change")
    _run_git(git_repo, "checkout", main)

    assert merge_branch(git_repo, "feature-squash", strategy="squash")
    status = _run_git(git_repo, "status", "--short").stdout
    assert "squash.txt" in status


def test_merge_branch_conflict(git_repo: Path):
    main = _current_branch(git_repo)
    _run_git(git_repo, "checkout", "-b", "feature-conflict")
    _write_file(git_repo / "shared.txt", "feature change\n")
    _commit_all(git_repo, "feature conflict change")
    _run_git(git_repo, "checkout", main)
    _write_file(git_repo / "shared.txt", "main change\n")
    _commit_all(git_repo, "main conflict change")

    assert not merge_branch(git_repo, "feature-conflict")
    conflicts = _run_git(git_repo, "ls-files", "-u").stdout.strip()
    assert conflicts


def test_delete_branch(git_repo: Path):
    main = _current_branch(git_repo)
    _run_git(git_repo, "checkout", "-b", "feature-delete")
    _write_file(git_repo / "delete.txt", "delete branch\n")
    _commit_all(git_repo, "feature delete branch change")
    _run_git(git_repo, "checkout", main)
    assert merge_branch(git_repo, "feature-delete")

    delete_branch(git_repo, "feature-delete")
    listed = _run_git(git_repo, "branch", "--list", "feature-delete").stdout.strip()
    assert listed == ""


def test_delete_branch_force(git_repo: Path):
    main = _current_branch(git_repo)
    _run_git(git_repo, "checkout", "-b", "feature-force-delete")
    _write_file(git_repo / "force-delete.txt", "force delete branch\n")
    _commit_all(git_repo, "feature force delete branch change")
    _run_git(git_repo, "checkout", main)

    delete_branch(git_repo, "feature-force-delete", force=True)
    listed = _run_git(git_repo, "branch", "--list", "feature-force-delete").stdout.strip()
    assert listed == ""


# ---------------------------------------------------------------------------
# End-to-end: worktree create → commit in worktree → merge back to main
# ---------------------------------------------------------------------------


def test_e2e_worktree_merge_back_to_main(git_repo: Path):
    """Create a worktree, commit changes there, merge back via main repo root."""
    wt = create_worktree(git_repo, "e2e-merge")

    # Make a change in the worktree
    _write_file(wt / "from_worktree.txt", "created in worktree\n")
    _commit_all(wt, "worktree commit")

    # Resolve main repo from worktree path (the critical path that was buggy)
    main_root = get_main_repo_root(wt)
    assert main_root.resolve() == git_repo.resolve()

    # Merge from main repo root (not from worktree)
    assert merge_branch(main_root, "tcd/e2e-merge")

    # Verify file appeared in main repo
    assert (git_repo / "from_worktree.txt").read_text(encoding="utf-8") == "created in worktree\n"


def test_e2e_worktree_squash_merge_and_force_delete(git_repo: Path):
    """Squash merge from worktree, then force-delete the branch."""
    wt = create_worktree(git_repo, "e2e-squash")

    _write_file(wt / "squash_file.txt", "squash content\n")
    _commit_all(wt, "squash commit 1")
    _write_file(wt / "squash_file2.txt", "squash content 2\n")
    _commit_all(wt, "squash commit 2")

    main_root = get_main_repo_root(wt)

    # Remove worktree first (can't remove while on that branch)
    remove_worktree(wt)
    assert not wt.exists()

    # Squash merge
    assert merge_branch(main_root, "tcd/e2e-squash", strategy="squash")

    # Commit the squash (git merge --squash stages but doesn't commit)
    _commit_all(git_repo, "squash merge e2e")

    # Force delete (required after squash since no merge ancestry)
    delete_branch(git_repo, "tcd/e2e-squash", force=True)
    listed = _run_git(git_repo, "branch", "--list", "tcd/e2e-squash").stdout.strip()
    assert listed == ""

    # Verify files
    assert (git_repo / "squash_file.txt").read_text(encoding="utf-8") == "squash content\n"
    assert (git_repo / "squash_file2.txt").read_text(encoding="utf-8") == "squash content 2\n"


# ---------------------------------------------------------------------------
# Auto-stash identity
#
# The stash stack is repo-wide, so parallel worktree jobs interleave on it.
# Popping "the top" hands one job's changes to another job's merge.
# ---------------------------------------------------------------------------

def test_stash_pop_by_ref_targets_the_right_stash(git_repo: Path):
    _write_file(git_repo / "job_a.txt", "job A work\n")
    ref_a = auto_stash(git_repo, "tcd: auto-stash before worktree")
    assert ref_a is not None

    _write_file(git_repo / "job_b.txt", "job B work\n")
    ref_b = auto_stash(git_repo, "tcd: auto-stash before worktree")
    assert ref_b is not None and ref_b != ref_a

    # Job A finishes first; its stash is no longer at the top of the stack.
    assert stash_pop(git_repo, ref_a)
    assert (git_repo / "job_a.txt").exists()
    assert not (git_repo / "job_b.txt").exists()

    # Job B's stash is untouched and still restorable.
    assert stash_pop(git_repo, ref_b)
    assert (git_repo / "job_b.txt").exists()


def test_stash_pop_by_ref_reports_missing_stash(git_repo: Path):
    _write_file(git_repo / "gone.txt", "x\n")
    ref = auto_stash(git_repo)
    assert ref is not None
    assert stash_pop(git_repo, ref)

    # Popping the same ref twice must fail loudly, not silently pop whatever
    # else happens to be on the stack.
    _write_file(git_repo / "someone_elses.txt", "y\n")
    auto_stash(git_repo)
    assert not stash_pop(git_repo, ref)
    assert not (git_repo / "someone_elses.txt").exists()


def test_find_stash_selector_resolves_current_position(git_repo: Path):
    _write_file(git_repo / "first.txt", "1\n")
    ref = auto_stash(git_repo)
    assert find_stash_selector(git_repo, ref) == "stash@{0}"

    _write_file(git_repo / "second.txt", "2\n")
    auto_stash(git_repo)
    # The first stash has been pushed down the stack.
    assert find_stash_selector(git_repo, ref) == "stash@{1}"
    assert find_stash_selector(git_repo, "0" * 40) is None


def test_worktree_is_dirty_detects_uncommitted_work(git_repo: Path):
    wt = create_worktree(git_repo, "dirty-check")
    assert not worktree_is_dirty(wt)

    _write_file(wt / "agent_output.txt", "work the agent has not committed\n")
    assert worktree_is_dirty(wt)

    remove_worktree(wt)
    assert not worktree_is_dirty(wt)


def test_auto_stash_tags_the_entry_with_the_job_id(git_repo: Path):
    _write_file(git_repo / "work.txt", "x\n")
    ref = auto_stash(git_repo, "job42")
    assert ref is not None

    messages = [m for _, _, m in _stash_entries(git_repo)]
    assert any("job42" in m for m in messages)


def test_auto_stash_returns_none_when_nothing_was_stashed(git_repo: Path):
    """A concurrent job can stash the same changes first.

    `git stash push` then succeeds without creating an entry. Returning the
    previous top here is how one job used to claim another job's stash.
    """
    _write_file(git_repo / "shared_work.txt", "x\n")
    first = auto_stash(git_repo, "job_a")
    assert first is not None

    # Tree is clean now, exactly as job B would find it after job A's push.
    assert auto_stash(git_repo, "job_b") is None
    assert len(_stash_entries(git_repo)) == 1


def test_find_stash_selector_honours_legacy_positional_refs(git_repo: Path):
    """Pre-0.4.0 jobs persisted the literal 'stash@{0}'."""
    _write_file(git_repo / "legacy.txt", "x\n")
    auto_stash(git_repo, "old-job")

    assert find_stash_selector(git_repo, "stash@{0}") == "stash@{0}"
    assert find_stash_selector(git_repo, "stash@{7}") is None


def test_stash_exists_tracks_manual_removal(git_repo: Path):
    _write_file(git_repo / "manual.txt", "x\n")
    ref = auto_stash(git_repo, "job1")
    assert stash_exists(git_repo, ref)

    _run_git(git_repo, "stash", "drop")
    assert not stash_exists(git_repo, ref)


def test_repo_lock_is_exclusive(git_repo: Path):
    import threading

    order: list[str] = []
    second_entered = threading.Event()

    def second():
        with repo_lock(git_repo, timeout=5):
            order.append("second")
            second_entered.set()

    with repo_lock(git_repo):
        t = threading.Thread(target=second)
        t.start()
        # The second thread must not get in while the first holds the lock.
        assert not second_entered.wait(timeout=0.5)
        order.append("first")
    t.join(timeout=5)

    assert order == ["first", "second"]


def test_repo_lock_is_shared_between_main_checkout_and_linked_worktree(git_repo: Path):
    """A worktree job locks from inside the worktree; the stash stack is shared.

    Hashing the caller's path handed those two callers different locks, which
    meant the repo-level lock was not repo-level exactly where the parallel
    worktree jobs it exists for are running.
    """
    from tcd.config import repo_lock_path

    wt = create_worktree(git_repo, "lock-identity")
    try:
        assert repo_lock_path(git_repo) == repo_lock_path(wt)
    finally:
        remove_worktree(wt)


def test_repo_lock_differs_between_unrelated_repos(git_repo: Path, tmp_path: Path):
    from tcd.config import repo_lock_path

    other = tmp_path / "unrelated"
    other.mkdir()
    _run_git(other, "init", "-q")

    assert repo_lock_path(git_repo) != repo_lock_path(other)
