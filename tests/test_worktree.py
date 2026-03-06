"""Integration tests for git worktree primitives."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tcd.worktree import (
    WorktreeError,
    create_worktree,
    delete_branch,
    get_main_repo_root,
    get_repo_root,
    is_git_repo,
    merge_branch,
    remove_worktree,
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
