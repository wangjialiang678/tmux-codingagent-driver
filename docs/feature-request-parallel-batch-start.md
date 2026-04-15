# Feature Request: Batch Parallel Worktree Job Launch (`tcd batch`)

**Status**: Proposed
**Target version**: v0.4.0
**Filed by**: Power user (feishu-cli MCP server project)
**Date**: 2026-03-10

---

## Problem Statement

When building complex projects that can be decomposed into independent subtasks (e.g., an MCP server with 13 tools split into 3 functional groups), the ideal workflow is to launch all subtasks in parallel worktrees, wait for all of them to complete, then merge and verify. tcd already handles the individual worktree job lifecycle well — but the **orchestration layer is missing**.

Today, launching N parallel worktree jobs requires N separate CLI invocations:

```bash
tcd start --worktree -p codex --wt-name group-a -m "Implement doc-read, doc-search, doc-upload tools" -d /project
tcd start --worktree -p codex --wt-name group-b -m "Implement bitable-search, bitable-write, bitable-meta tools" -d /project
tcd start --worktree -p codex --wt-name group-c -m "Implement permission, delete, folder, scope, im tools" -d /project
```

This is error-prone in several ways:

1. **No atomic semantics**: If the second `tcd start` fails (e.g., git error, dirty repo), the first job is already running. There is no rollback.
2. **No unified tracking**: The caller must maintain a separate list of job IDs to poll and merge. There is no concept of a "batch" that groups related jobs.
3. **No unified status view**: Checking the progress of all N jobs requires N separate `tcd check` calls and manual collation.
4. **Sequential merge ceremony**: After all jobs complete, the caller must merge each one in sequence, handling conflicts manually, with no coordination from tcd.
5. **Rate limit exposure**: There is no built-in way to launch jobs in waves (e.g., max 2 at a time) without writing shell logic.

### Real-world impact

In the feishu-cli project (building 13 MCP tools), the manual workaround was: run 3 `tcd start` commands, write the 3 job IDs to a text file, poll each one in a loop, and merge sequentially. If one `tcd start` call had failed mid-way, there was no recovery path.

---

## Proposed Solution: `tcd batch` Command

Introduce a first-class `tcd batch` command that accepts N task definitions, creates all worktrees atomically, launches all agents, assigns a batch ID, and provides batch-level status and merge commands.

### Interface options

**Option A — JSON task file (recommended for CI/scripting)**

```bash
tcd batch --file tasks.json --provider codex --worktree -d /path/to/project
```

**Option B — Inline `--task` flags (recommended for interactive use)**

```bash
tcd batch --provider codex --worktree -d /path/to/project \
  --task "Implement doc-read, doc-search, doc-upload tools" \
  --task "Implement bitable-search, bitable-write, bitable-meta tools" \
  --task "Implement permission, delete, folder, scope, im tools"
```

**Option C — JSON array on stdin (recommended for pipeline use)**

```bash
echo '["task1", "task2", "task3"]' | tcd batch --provider codex --worktree -d /path/to/project
```

All three options produce the same outcome: a batch with a `batch_id`, N worktree jobs, and a unified tracking record.

### Output on launch

```
Batch launched: batch_20260310_abc123
  [1/3] job_a1b2c3  wt: group-a  state: started
  [2/3] job_d4e5f6  wt: group-b  state: started
  [3/3] job_g7h8i9  wt: group-c  state: started

Poll status:   tcd batch-status batch_20260310_abc123
Merge all:     tcd batch-merge  batch_20260310_abc123
```

---

## Key Requirements

### 1. Atomic launch with rollback

All N worktrees must be created before any tmux session is started. If any `create_worktree()` call fails, all previously created worktrees are removed and no sessions are started.

**Rationale**: A partial batch (some jobs running, some never started) is worse than a clean failure. The caller should get a clear error and be able to retry with no cleanup burden.

Pseudo-logic:

```
worktrees = []
for task in tasks:
    try:
        wt = create_worktree(repo, task.worktree_name)
        worktrees.append(wt)
    except WorktreeError:
        for wt in worktrees:
            remove_worktree(wt)   # rollback
        raise BatchError("Worktree creation failed, batch aborted")

# All worktrees created — now start sessions
for wt, task in zip(worktrees, tasks):
    start_session(wt, task)
```

### 2. Shared context file + per-task prompt

Each job should accept a shared base context file alongside its task-specific prompt:

```bash
tcd batch --file tasks.json --context context.md --provider codex --worktree -d /project
```

The context file is injected at the top of each job's prompt (similar to how `tcd start --file` works today). This is the primary mechanism for sharing architecture docs, coding standards, or shared constraints across all parallel jobs.

In `tasks.json`, each task can optionally override or extend the context:

```json
{
  "context_file": "context.md",
  "tasks": [
    { "name": "group-a", "prompt": "Implement doc tools", "extra_context": "group-a-notes.md" },
    { "name": "group-b", "prompt": "Implement bitable tools" }
  ]
}
```

### 3. Batch status: `tcd batch-status <batch_id>`

Show all jobs in a batch in a single view:

```
$ tcd batch-status batch_20260310_abc123

Batch: batch_20260310_abc123  (3 jobs, started 2026-03-10 14:00:12)

  job_a1b2c3  group-a  idle     elapsed: 8m12s  turns: 4
  job_d4e5f6  group-b  working  elapsed: 8m12s  turns: 2
  job_g7h8i9  group-c  idle     elapsed: 8m12s  turns: 3

Done: 2/3   Working: 1/3   Failed: 0/3
```

With `--json`, returns structured data for machine consumption:

```json
{
  "batch_id": "batch_20260310_abc123",
  "total": 3,
  "done": 2,
  "working": 1,
  "failed": 0,
  "jobs": [
    { "job_id": "job_a1b2c3", "worktree_name": "group-a", "state": "idle", "elapsed_s": 492, "turn_count": 4 },
    { "job_id": "job_d4e5f6", "worktree_name": "group-b", "state": "working", "elapsed_s": 492, "turn_count": 2 },
    { "job_id": "job_g7h8i9", "worktree_name": "group-c", "state": "idle", "elapsed_s": 492, "turn_count": 3 }
  ]
}
```

Exit codes mirror `tcd check`: `0` = all idle, `1` = any still working, `2` = any context_limit, `3` = batch not found.

### 4. Batch merge: `tcd batch-merge <batch_id>`

Merge all completed worktree branches back to the main branch, sequentially:

```bash
tcd batch-merge batch_20260310_abc123
tcd batch-merge batch_20260310_abc123 --squash
tcd batch-merge batch_20260310_abc123 --no-cleanup     # merge but keep worktrees
tcd batch-merge batch_20260310_abc123 --only-done      # skip jobs still working
```

Merge order follows the order tasks were defined in the batch. On conflict, the command stops and reports which branch conflicted:

```
Merging group-a... OK
Merging group-b... CONFLICT
  Branch tcd/group-b has conflicts. Resolve manually, then run:
  tcd batch-merge batch_20260310_abc123 --skip group-b --resume
```

### 5. Concurrency limit: `--max-concurrent N`

Limit how many jobs run simultaneously. Useful when the AI provider has API rate limits or when the machine cannot sustain N tmux sessions.

```bash
tcd batch --file tasks.json --provider codex --worktree --max-concurrent 2 -d /project
```

When `--max-concurrent 2` is set with 5 tasks: jobs 1 and 2 start immediately; when one finishes, job 3 starts; and so on. tcd polls job states internally to manage the queue.

This requires a lightweight background coordinator (or polling at `tcd batch-status` time). The simplest implementation is eager: start all, but only the first N sessions are actually running — the rest are queued in the batch record and started via a `tcd batch-tick` mechanism (see Implementation Notes).

---

## Batch Record Format

A batch is stored as a JSON file at `~/.tcd/batches/<batch_id>.json`:

```json
{
  "batch_id": "batch_20260310_abc123",
  "created_at": "2026-03-10T14:00:12Z",
  "provider": "codex",
  "cwd": "/path/to/project",
  "context_file": "context.md",
  "max_concurrent": null,
  "jobs": [
    {
      "job_id": "job_a1b2c3",
      "worktree_name": "group-a",
      "prompt": "Implement doc tools",
      "state": "idle",
      "merge_state": "pending"
    },
    {
      "job_id": "job_d4e5f6",
      "worktree_name": "group-b",
      "prompt": "Implement bitable tools",
      "state": "working",
      "merge_state": "pending"
    },
    {
      "job_id": "job_g7h8i9",
      "worktree_name": "group-c",
      "prompt": "Implement permission tools",
      "state": "idle",
      "merge_state": "pending"
    }
  ]
}
```

`merge_state` values: `pending`, `merged`, `conflict`, `skipped`.

---

## New CLI Commands Summary

| Command | Description |
|---------|-------------|
| `tcd batch [--file F] [--task T]... [--provider P] [--worktree] [--context C] [--max-concurrent N] [-d DIR]` | Launch N parallel jobs as a named batch |
| `tcd batch-status <batch_id> [--json]` | Show status of all jobs in a batch |
| `tcd batch-merge <batch_id> [--squash] [--no-cleanup] [--only-done] [--skip NAME] [--resume]` | Merge all completed worktree branches |
| `tcd batches [--json]` | List all batches |
| `tcd batch-kill <batch_id>` | Kill all running jobs in a batch and remove worktrees |

---

## Implementation Notes

### What already exists (reuse these)

- `worktree.py`: `create_worktree()`, `remove_worktree()`, `merge_branch()`, `delete_branch()` — all the git primitives needed.
- `sdk.py` `start()` with `worktree=True` — the per-job launch path already works.
- `sdk.py` `merge_worktree()` — single-job merge already implemented.
- Job state tracking in `~/.tcd/jobs/` — batch records can sit alongside as `~/.tcd/batches/`.

### What needs to be built

1. **`src/tcd/batch.py`** — `BatchManager`: create, load, save, list batch records; atomic worktree creation with rollback; queue management for `--max-concurrent`.
2. **`tcd batch` CLI command** — parse `--file`, `--task`, stdin; delegate to `BatchManager`.
3. **`tcd batch-status` CLI command** — load batch, check each job's current state via existing `check()` logic, render table or JSON.
4. **`tcd batch-merge` CLI command** — iterate jobs in order, call `merge_worktree()` per job, handle conflict stop-and-report.
5. **`tcd batches` / `tcd batch-kill` CLI commands** — thin wrappers over `BatchManager`.
6. **Tests** — `tests/test_batch.py` unit tests with mocked git and session; integration test for the atomic rollback path.

### Concurrency limit implementation

The simplest approach that avoids a daemon process: record a `queue` list in the batch JSON. On `tcd batch`, only start the first N jobs. On `tcd batch-status`, detect completed jobs and start queued ones (side-effect on status check). This "lazy advancement" model means the queue advances whenever someone polls status, which is acceptable for the target use case (an upstream agent polling in a loop).

---

## Non-Goals

- No built-in conflict auto-resolution on merge — stop and report.
- No cross-batch dependency tracking — batches are independent units.
- No distributed execution across machines.
- No persistent daemon or background scheduler — all state is driven by CLI invocations.
- No changes to existing `tcd start`, `tcd merge`, or `tcd check` behavior.

---

## Use Case: feishu-cli MCP Server (13 Tools, 3 Parallel Groups)

This feature was motivated by the following real workflow:

**Project**: feishu-cli — an MCP server with 13 tools covering Feishu Docs, Bitable, Drive, IM, and permissions APIs.

**Task decomposition**:
- Group A (independent): `feishu_read_doc`, `feishu_search_docs`, `feishu_upload_markdown`, `feishu_export_doc`, `feishu_beautify_doc`
- Group B (independent): `feishu_bitable_search`, `feishu_bitable_write`, `feishu_bitable_get_meta`, `feishu_list_folder`
- Group C (independent): `feishu_set_permission`, `feishu_delete_doc`, `feishu_check_scopes`, `feishu_read_message`

All three groups touch different files and have no shared mutable state, making them safe to develop in parallel worktrees.

**Desired workflow**:

```bash
# Step 1: Launch all 3 groups in parallel (one command)
tcd batch --file tasks.json --provider codex --worktree \
          --context docs/design/mcp-server-technical-design.md \
          -d /path/to/feishu-cli

# Step 2: Poll until all done
watch tcd batch-status batch_20260310_abc123

# Step 3: Merge all back and run tests
tcd batch-merge batch_20260310_abc123 --squash
npm test
```

**Current workaround** (without this feature): 3 manual `tcd start` calls, job IDs written to a text file, manual polling loop, manual sequential merges. No atomic rollback if step 1 partially fails.

---

## Acceptance Criteria

- [ ] `tcd batch --file tasks.json --provider codex --worktree -d .` creates all worktrees atomically and starts all sessions.
- [ ] If any worktree creation fails, all previously created worktrees are removed and no sessions are started.
- [ ] A batch JSON record is created at `~/.tcd/batches/<batch_id>.json` containing all job IDs.
- [ ] `tcd batch-status <batch_id>` shows per-job state and a summary line.
- [ ] `tcd batch-status <batch_id> --json` returns machine-readable JSON with exit code semantics matching `tcd check`.
- [ ] `tcd batch-merge <batch_id>` merges jobs in definition order, stops on first conflict with a clear error message.
- [ ] `tcd batch-kill <batch_id>` kills all running jobs and removes all worktrees.
- [ ] `--max-concurrent 2` with 5 tasks starts only 2 initially; subsequent calls to `tcd batch-status` advance the queue.
- [ ] All existing `tcd start`, `tcd merge`, `tcd check` behavior is unchanged.
- [ ] Unit tests for atomic rollback (simulated `create_worktree` failure on the Nth task).
- [ ] Integration test: batch launch 2 jobs, wait, merge both, verify merged commits in git log.
