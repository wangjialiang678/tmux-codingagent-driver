# tcd Worktree Closed-Loop Test Plan

**Version**: v0.3.0
**Date**: 2026-03-05
**Status**: COMPLETED (Phase 1–3 all verified, 222 tests pass)
**Baseline**: 191 tests pass (v0.2.0)

---

## P0: Basic Build Checks

All items must pass after every logical unit is complete.

- [x] **P0-1: Dependency install**
  Pass criteria: `uv sync` exits with code 0
  Suggested command: `uv sync`

- [x] **P0-2: Module import**
  Pass criteria: `from tcd.worktree import create_worktree` raises no ImportError
  Suggested command: `uv run python -c "from tcd.worktree import create_worktree, remove_worktree, merge_branch; print('OK')"`

- [x] **P0-3: Full unit tests (regression)**
  Pass criteria: `pytest` exits with code 0, test count >= 191 (baseline must not regress)
  Suggested command: `uv run pytest tests/ -q`
  Result: 222 passed

- [x] **P0-4: CLI entry point**
  Pass criteria: `tcd --help` exits with code 0, output includes "merge" subcommand
  Suggested command: `uv run tcd --help`

---

## P1: Phase 1 — Worktree Primitives

### Feature 1: Git Detection

- [x] **P1-1a: is_git_repo correctly identifies git directory**
  Pass criteria: returns True inside a git repo, False in /tmp
  Verification: unit test test_worktree.py

- [x] **P1-1b: get_repo_root returns correct root directory**
  Pass criteria: called from a repo subdirectory returns repo root; raises WorktreeError in a non-git directory
  Verification: unit test

### Feature 2: Worktree Creation and Cleanup

- [x] **P1-2a: create_worktree creates isolated directory**
  Pass criteria: returned path exists, is an independent git worktree, branch name starts with `tcd/`
  Verification: integration test (real git repo)

- [x] **P1-2b: create_worktree errors on duplicate name**
  Pass criteria: creating a worktree with a duplicate name raises WorktreeError
  Verification: unit test

- [x] **P1-2c: remove_worktree cleans up completely**
  Pass criteria: after removal the directory does not exist, `git worktree list` does not contain the path
  Verification: integration test

- [x] **P1-2d: remove_worktree on non-existent path does not error**
  Pass criteria: calling on a non-existent path does not raise
  Verification: unit test

### Feature 3: Merge and Branch Deletion

- [x] **P1-3a: merge_branch successful merge returns True**
  Pass criteria: merge succeeds and returns True; files from the target branch are visible in the main branch
  Verification: integration test (create worktree → commit file → merge)

- [x] **P1-3b: merge_branch squash mode**
  Pass criteria: `strategy="squash"` results in a single squash commit in commit history after merge
  Verification: integration test

- [x] **P1-3c: merge_branch conflict returns False**
  Pass criteria: when main branch and worktree branch modify the same line in the same file, merge returns False
  Verification: integration test

- [x] **P1-3d: delete_branch deletes branch**
  Pass criteria: merged branch is deleted, `git branch` no longer lists it
  Verification: integration test

---

## P1: Phase 2 — Job + SDK Integration

### Feature 4: Job Data Structure

- [x] **P1-4a: Job adds worktree fields**
  Pass criteria: `Job(worktree_path="/tmp/x", worktree_branch="tcd/x")` serializes/deserializes correctly
  Verification: unit test

- [x] **P1-4b: Old Job JSON backward compatibility**
  Pass criteria: deserializing JSON without worktree fields does not error; fields are None
  Verification: unit test

### Feature 5: SDK start() worktree support

- [x] **P1-5a: start(worktree=True) creates worktree**
  Pass criteria: Job.worktree_path is non-empty, Job.cwd points to the worktree directory
  Verification: unit test (mock git operations)

- [x] **P1-5b: start(worktree=True) errors in non-git directory**
  Pass criteria: raises TCDError containing "not a git repository"
  Verification: unit test

- [x] **P1-5c: start(worktree=True) errors with uncommitted changes**
  Pass criteria: raises TCDError containing "uncommitted changes"
  Verification: unit test

### Feature 6: SDK merge_worktree()

- [x] **P1-6a: merge_worktree successful merge**
  Pass criteria: returns True, worktree is cleaned up, branch is deleted
  Verification: unit test (mock)

- [x] **P1-6b: merge_worktree conflict**
  Pass criteria: returns False, worktree and branch are preserved (for manual resolution)
  Verification: unit test

- [x] **P1-6c: merge_worktree on Job with no worktree errors**
  Pass criteria: raises TCDError containing "no worktree"
  Verification: unit test

### Feature 7: kill/clean Automatic Cleanup

- [x] **P1-7a: kill automatically cleans up worktree**
  Pass criteria: worktree directory is removed after kill
  Verification: unit test (mock remove_worktree, verify it was called)

- [x] **P1-7b: clean automatically cleans up worktree**
  Pass criteria: related worktree is removed after clean
  Verification: unit test (note: covered by kill path in practice; clean calls kill)

### Feature 8: Event Log

- [x] **P1-8a: worktree_created event**
  Pass criteria: after start(worktree=True), event log contains `job.worktree_created`
  Verification: unit test

- [x] **P1-8b: worktree_merged event**
  Pass criteria: after merge_worktree, event log contains `job.worktree_merged`
  Verification: unit test

- [x] **P1-8c: worktree_removed event**
  Pass criteria: after killing a Job with a worktree, event log contains `job.worktree_removed`
  Verification: unit test

---

## P1: Phase 3 — CLI Integration

### Feature 9: tcd start --worktree

- [x] **P1-9a: --worktree flag parsing**
  Pass criteria: `tcd start --worktree -p codex -m "test" -d .` passes worktree=True
  Verification: unit test (mock)

- [x] **P1-9b: --wt-name custom name**
  Pass criteria: `tcd start --worktree --wt-name auth -p codex -m "test"` passes worktree_name="auth"
  Verification: unit test

### Feature 10: tcd merge Command

- [x] **P1-10a: tcd merge normal merge**
  Pass criteria: `tcd merge <id>` exits with code 0, output contains "Merged"
  Verification: unit test (mock)

- [x] **P1-10b: tcd merge --squash**
  Pass criteria: `tcd merge <id> --squash` passes strategy="squash"
  Verification: unit test

- [x] **P1-10c: tcd merge conflict**
  Pass criteria: on merge conflict, exit code is non-zero, output contains "conflict"
  Verification: unit test

- [x] **P1-10d: tcd merge --no-cleanup**
  Pass criteria: passes cleanup=False (does not call remove_worktree)
  Verification: unit test

---

## Verification Discipline

- **Pass criteria are locked**: all pass criteria above cannot be modified after user confirmation
- **Baseline protection**: test count must be >= 191 after each verification round (deleting existing tests is not allowed)
- **Each fix changes only business code**: lowering pass criteria to make tests pass is not allowed
- **Stop conditions**: 5 fixes on the same item / 2 rounds of oscillation / 15 total fixes / 3 consecutive rounds of P0 failure → stop and escalate to human
