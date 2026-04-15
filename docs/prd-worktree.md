# PRD: tcd Git Worktree Support

**Version**: v0.3.0
**Date**: 2026-03-05
**Status**: IMPLEMENTED
**Prerequisite**: v0.2.0 (event log + diagnostics system complete, 191 tests pass)
**Completion date**: 2026-03-05 (222 tests pass)

---

## 1. Problem

In Mode D (parallel Codex + Worktree) of the closed-loop ecosystem, multiple Codex instances need to work on the same project in parallel. tcd currently supports multiple concurrent jobs (each in its own independent tmux session), but lacks git worktree lifecycle management. Callers must manually:

1. `git worktree add` to create an isolated working directory
2. Pass the worktree path to `tcd start -d`
3. Manually `git merge` + `git worktree remove` after the job completes

These steps are mechanical, error-prone, and not bound to the job lifecycle.

**Core requirement**: tcd provides worktree primitives (create/cleanup/merge); callers decide when to use them.

---

## 2. Design Principles

- **tcd provides tools, not policy** — tcd does not decide "whether to use worktrees"
- **worktree=False is the default** — existing usage is unaffected, fully transparent
- **Non-git projects fail immediately** — simple and explicit, no fallback
- **Job lifecycle binding** — worktrees are automatically cleaned up on kill/clean
- **Merge is an explicit operation** — no automatic merge; callers decide timing and strategy

---

## 3. Design

### 3.1 New `src/tcd/worktree.py` — Git Worktree Primitives

Pure git operation wrapper; no dependency on other tcd modules:

```python
"""Git worktree primitives for parallel job isolation."""

import subprocess
from pathlib import Path


class WorktreeError(Exception):
    """Git worktree operation failed."""


def is_git_repo(path: str | Path) -> bool:
    """Check if path is inside a git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(path), capture_output=True, text=True,
    )
    return result.returncode == 0


def get_repo_root(path: str | Path) -> Path:
    """Get the root of the git repository containing path."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(path), capture_output=True, text=True,
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
        cwd=str(repo), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git worktree add failed: {result.stderr.strip()}")
    return wt_path


def remove_worktree(worktree_path: str | Path) -> None:
    """Remove a worktree and prune."""
    wt = Path(worktree_path)
    if not wt.exists():
        return
    # Find the main repo to run git commands
    result = subprocess.run(
        ["git", "worktree", "remove", str(wt), "--force"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git worktree remove failed: {result.stderr.strip()}")


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
    cmd = ["git", "merge", branch]
    if strategy == "squash":
        cmd = ["git", "merge", "--squash", branch]

    result = subprocess.run(
        cmd, cwd=str(repo_path), capture_output=True, text=True,
    )
    return result.returncode == 0


def delete_branch(repo_path: str | Path, branch: str) -> None:
    """Delete a local branch after merge."""
    subprocess.run(
        ["git", "branch", "-d", branch],
        cwd=str(repo_path), capture_output=True, text=True,
    )
```

### 3.2 Extend Job Data Structure

Add two optional fields to the `Job` dataclass:

```python
@dataclass
class Job:
    # ... existing fields ...
    worktree_path: str | None = None
    worktree_branch: str | None = None
```

These two fields are automatically compatible in `from_dict()` (unknown keys are filtered out; missing keys use default values).

### 3.3 SDK Integration

#### `start()` adds `worktree` parameter

```python
def start(
    self,
    provider: str,
    prompt: str,
    cwd: str = ".",
    *,
    model: str | None = None,
    timeout: int = 60,
    sandbox: str | None = None,
    worktree: bool = False,       # new
    worktree_name: str | None = None,  # new, defaults to job_id
) -> Job:
```

When `worktree=True`:
1. Check whether `cwd` is a git repo (raise `TCDError` if not)
2. Check for uncommitted changes (`git status --porcelain`); raise if present
3. `create_worktree(cwd, name)` → get the worktree path
4. Replace `cwd` with the worktree path
5. Record `worktree_path` and `worktree_branch` in the Job

#### `kill()` / `clean()` automatic cleanup

```python
def kill(self, job_id: str) -> None:
    # ... existing kill logic ...
    if job.worktree_path:
        try:
            remove_worktree(job.worktree_path)
        except WorktreeError:
            pass  # best-effort cleanup
```

#### New `merge_worktree()`

```python
def merge_worktree(
    self,
    job_id: str,
    *,
    strategy: str = "merge",
    cleanup: bool = True,
) -> bool:
    """Merge a worktree job's branch back and clean up.

    Returns True if merge succeeded, False if there were conflicts.
    """
    job = self._mgr.load_job(job_id)
    if not job or not job.worktree_branch:
        raise TCDError(f"Job {job_id} has no worktree")

    repo_root = get_repo_root(job.cwd)  # cwd is the worktree, get main repo
    success = merge_branch(repo_root, job.worktree_branch, strategy=strategy)

    if success and cleanup:
        remove_worktree(job.worktree_path)
        delete_branch(repo_root, job.worktree_branch)
        job.worktree_path = None
        job.worktree_branch = None
        self._mgr.save_job(job)

    emit(job.id, "job.worktree_merged", success=success, strategy=strategy)
    return success
```

### 3.4 CLI Integration

#### `tcd start` adds `--worktree` flag

```bash
# Start Codex in a worktree
tcd start -p codex --worktree -m "Implement user authentication" -d /path/to/project

# Custom worktree name
tcd start -p codex --worktree --wt-name auth -m "Implement user authentication" -d /path/to/project
```

#### New `tcd merge` command

```bash
# Merge worktree branch back to main branch
tcd merge <job_id>

# Squash merge
tcd merge <job_id> --squash

# Merge without cleanup
tcd merge <job_id> --no-cleanup
```

### 3.5 Event Log Integration

New event types:

| Event | Trigger | Key Fields |
|------|--------|---------|
| `job.worktree_created` | worktree successfully created | worktree_path, branch |
| `job.worktree_merged` | merge operation complete | success, strategy |
| `job.worktree_removed` | worktree cleanup complete | worktree_path |

---

## 4. Implementation Plan

### Phase 1: Worktree Primitives

- [ ] Add `src/tcd/worktree.py` (~80 lines)
- [ ] Functions: `is_git_repo`, `get_repo_root`, `create_worktree`, `remove_worktree`, `merge_branch`, `delete_branch`
- [ ] Add `tests/test_worktree.py` (requires a real git repo; integration test style similar to test_tmux_adapter.py)

### Phase 2: Job + SDK Integration

- [ ] Add `worktree_path`, `worktree_branch` fields to `Job` dataclass
- [ ] Add `worktree` / `worktree_name` parameters to `sdk.py` `start()`
- [ ] Add `merge_worktree()` method to `sdk.py`
- [ ] Automatic worktree cleanup in `kill()` / `clean()`
- [ ] Event log instrumentation (3 events)
- [ ] Add `tests/test_worktree_sdk.py` (unit tests that mock git operations)

### Phase 3: CLI Integration

- [ ] Add `--worktree` / `--wt-name` options to `tcd start`
- [ ] Add `tcd merge` command
- [ ] Tests: CLI argument parsing

---

## 5. Impact Scope

| File | Change Type |
|------|---------|
| `src/tcd/worktree.py` | **New** |
| `src/tcd/job.py` | Add 2 fields |
| `src/tcd/sdk.py` | Extend `start()` + `merge_worktree()` |
| `src/tcd/cli.py` | `--worktree` flag + `tcd merge` command |
| `tests/test_worktree.py` | **New** (integration tests) |
| `tests/test_worktree_sdk.py` | **New** (unit tests) |

**Not changed**: provider code, tmux_adapter, collector, event_log, diagnostics

---

## 6. Non-Goals

- Do not decide "whether to use worktrees" inside tcd — caller's decision
- Do not auto-resolve merge conflicts — return False, caller handles it
- Do not support `git stash` auto-stashing — error immediately when there are uncommitted changes
- Do not manage dependency installs (node_modules, etc.) — installation inside worktree is handled by caller/Codex
- Do not implement cross-worktree file locking — avoid conflicts through task decomposition (avoid modifying the same file)

---

## 7. Caller Usage Examples

### SDK Parallel Codex

```python
from tcd import TCD

tcd = TCD()

# Start 3 Codex jobs in parallel, each in its own worktree
jobs = []
for task in ["auth", "articles", "dashboard"]:
    job = tcd.start(
        "codex",
        f"Implement the {task} module; place code in src/features/{task}/",
        cwd="/path/to/project",
        worktree=True,
        worktree_name=task,
    )
    jobs.append(job)

# Wait for all to complete
for job in jobs:
    tcd.wait(job.id, timeout=600)

# Merge one by one
for job in jobs:
    success = tcd.merge_worktree(job.id)
    if not success:
        print(f"Merge conflict on {job.worktree_branch}, manual resolution needed")
```

### codex-worker Skill Integration

```
Caller (Claude Code) decides:
1. len(tasks) >= 2 and features are independent → suggest parallel worktrees
2. User confirms → create worktrees + parallel Codex
3. All complete → merge one by one + integration verification
```
