# Workflow Issue Analysis Report

**Date**: 2026-03-05
**Source**: Codex Code Review & Fix Workflow (2026-03-05 11:10–11:50)
**Jobs involved**: ece8b9e3, 9fc1e82d, 8d45037f, 8e6c6b37

---

## Issue Overview (by priority)

| Priority | Issue | Status |
|----------|-------|--------|
| P0 | M-6: `--sandbox` parameter is dead code, not passed to provider | Fixed (codex.py + start output shows sandbox) |
| P0 | Codex auto-update interrupts running tasks | Unresolved (external issue) |
| P1 | `tcd wait` blocks Claude Code process with no progress feedback | Skill updated (pending verification) |
| P1 | Missing pre-flight write permission check, fix tasks waste tokens | Unresolved (Skill-layer improvement) |
| P2 | Review tasks vs fix tasks sandbox mode indistinguishable | Unresolved |
| P2 | Non-git repos cannot use git diff to inspect Codex changes | Unresolved |
| P2 | Codex path-with-spaces causes shell command failures | Codex self-mitigated |
| P3 | Skill poll mode actual effectiveness unverified | To be observed |
| **P0** | **`tcd merge` FileNotFoundError + false success when worktree missing (#2)** | **Fixed (2026-03-09)** |
| P1 | `worktree_repo_root` field not persisted to job JSON (#2) | Confirmed as version issue; code is correct |
| P2 | Job status remains "running" after completion (#2) | **Fixed (auto-update status after merge)** |
| P2 | `tcd merge` success message inconsistent with actual result (#2) | **Fixed (MergeResult + noop detection)** |
| **P0** | **AI doesn't commit in worktree, merge is noop (#3)** | **Fixed (Skill prompt + merge pre-check, 2026-03-09)** |
| P1 | Bootstrapping: fixing tcd with old tcd (#3) | **Mitigated (Skill bootstrap warning, 2026-03-09)** |
| P2 | STALL/TURN0_STUCK false positives (AI generating long text) (#3) | **Fixed (pane_hash detection, 2026-03-09)** |
| P2 | `tcd merge` cannot distinguish "no commits" from "already merged" (#3) | **Fixed (branch_has_new_commits pre-check, 2026-03-09)** |

---

## Detailed Analysis

---

### P0-1: M-6 — `--sandbox` Parameter Is Dead Code

**Issue Description**
`tcd start --sandbox workspace-write` accepted the `--sandbox` argument, but the parameter was never passed to the Codex provider's launch command, causing Codex to always run in the default sandbox mode (read-only).

**Root Cause Analysis**
Code inspection confirms this is a **previously fixed historical bug**. See current `src/tcd/providers/codex.py` lines 153–155:

```python
# sandbox mode (default: workspace-write)
sandbox = job.sandbox or "workspace-write"
parts.append(f"-s {sandbox}")
```

`job.sandbox` is correctly read and passed as the `-s` parameter.

However, during workflow execution (2026-03-05 11:30), job `8e6c6b37` still reported "read-only sandbox". Possible reasons:
1. The code was not yet fixed at workflow execution time (M-6 was one of the bugs this review was fixing)
2. Or the Codex CLI's `-s workspace-write` parameter had no actual effect (needs verification against codex version behavior)

**Impact Assessment**
- Severity: **Blocking** (fix tasks completely cannot execute)
- Frequency: 100% (every fix task triggers this)
- Affected: 4 fix attempts all failed, consuming approximately 80k tokens

**Existing Fix**
Current code (codex.py L154) already contains sandbox pass-through logic. Needs verification:
1. Whether the fix has taken effect (reinstall with `uv tool install .`)
2. Whether the `codex -s workspace-write` parameter format is correct (codex v0.110.0 may have changed the parameter format)

**Recommended Improvements**
1. Add logging in `build_launch_command`: `logger.info("sandbox=%s, cmd=%s", sandbox, inner_cmd)`
2. Output the actual launch command after `tcd start` completes (debug mode) for parameter verification
3. Write test: `test_codex_provider.py::test_sandbox_flag_included_in_command`
4. Display sandbox mode in `tcd start` output: `Sandbox: workspace-write`

**Priority**: P0

---

### P0-2: Codex Auto-Update Interrupts Tasks

**Issue Description**
After job `9fc1e82d` started, Codex CLI auto-updated from v0.106.0 to v0.110.0, the process restarted, and the task was interrupted. `tcd wait` timed out (15 minutes); the task failed with `killed by user` (`turn_state: working`, `turn_count: 0`).

**Root Cause Analysis**
Codex CLI displays an update prompt in the TUI when a new version is detected and auto-executes the update. tcd's `_wait_for_tui()` only handles the trust dialog, not the update-restart scenario. After the update, the process restarts; the original Codex instance in the tmux session may be in an inconsistent state, the notify-hook is not triggered, the signal file is never written, and completion detection is broken.

**Impact Assessment**
- Severity: **High** (causes complete task failure with no output)
- Frequency: Unpredictable (depends on Codex release frequency; may trigger weekly during active development)
- Affected: ~5k tokens wasted (this instance); may compound retry costs

**Existing Fix**
None.

**Recommended Solutions**

Option A (recommended): Pin Codex version, disable auto-update
```bash
# Install a specific version with npm
npm install -g @openai/codex@0.110.0
```
Or add an environment variable to disable update checks at launch (research whether Codex supports `CODEX_DISABLE_UPDATE_CHECK` or similar).

Option B: Detect update prompts in `_wait_for_tui()`
Add update prompt detection in `sdk.py:_wait_for_tui()` and `cli.py:start()`:
```python
update_phrases = ["A new version", "Updating", "Restarting after update"]
if any(phrase in pane for phrase in update_phrases):
    time.sleep(5)  # wait for update to complete
    trust_handled = True  # reset, wait for TUI after restart
    continue
```

Option C: Add "update detection" state to `tcd check`
When a Codex update restart is detected, automatically re-inject the prompt.

**Priority**: P0

---

### P1-1: `tcd wait` Blocks with No User Feedback

**Issue Description**
After calling `tcd wait <job_id>`, the Claude Code process blocks inside a single Bash call; the user sees no progress output during the wait (up to 15 minutes).

**Root Cause Analysis**
`tcd wait` is a blocking while loop (`cli.py:280-312`):
```python
while time.time() < deadline:
    ...
    time.sleep(poll_interval)
```
When called as a subprocess inside Claude Code's Bash tool, the entire Bash call is blocked and Claude Code cannot output anything to the user while waiting.

**Impact Assessment**
- Severity: **Medium** (poor user experience, does not block functionality)
- Frequency: every time the old Skill is used
- Affected: 2 tasks (ece8b9e3 5 minutes, 9fc1e82d 15 minutes)

**Existing Fix**
The codex-worker Skill has been updated to **poll mode**: `tcd wait` is prohibited; instead, each poll is an independent Bash call (`tcd check` + `tcd output | tail -30`) every 15 seconds, with progress summaries output to the user between calls.

**Recommended Improvements**
1. Skill update already covers this scenario (see SKILL.md Step 2)
2. Add a warning to `tcd wait`: `"Warning: tcd wait blocks the caller. Use tcd check in a loop for interactive use."`
3. Optional: add a `--progress` flag to `tcd wait` to periodically output progress to stderr (`elapsed: 30s, state: working`)

**Priority**: P1

---

### P1-2: Missing Pre-flight Write Permission Check, Repeated Token Waste

**Issue Description**
Fix tasks were started without verifying whether Codex had write permission, causing Codex to read the code and complete analysis before discovering it couldn't write files — 4 retries wasted approximately 80k tokens.

**Root Cause Analysis**
Current workflow: launch → inject prompt → Codex reads code (20-40k tokens) → prepares to write file → discovers permission denied → errors out.

Missing a pre-flight check step: before injecting the fix task prompt, verify whether Codex can write files in the target directory.

**Impact Assessment**
- Severity: **High** (financial loss from token costs)
- Frequency: triggered every time sandbox is misconfigured
- Affected: 3 fix tasks × ~25k tokens = ~75k tokens wasted

**Existing Fix**
None.

**Recommended Solutions**

Option A (recommended): Add pre-flight check step in codex-worker Skill
Insert a verification step before Step 1 (start task):
```bash
# Verify write permission (send probe prompt)
tcd start -p codex -m "Execute: touch .tcd-write-probe && echo OK || echo READONLY" -d <dir>
# Check output, confirm it contains OK before proceeding with actual task
```

Option B: Add `--verify-write` flag to `tcd start`
After the provider starts but before injecting the real prompt, inject a write-permission probe command; proceed only after verification passes.

Option C: Declare pre-condition in the prompt (lightest approach)
Add to the beginning of fix-type prompts:
```
First, execute: touch .tcd-probe to verify write permission. If it fails (operation not permitted), stop immediately and report — do not continue reading code.
```

**Priority**: P1

---

### P2-1: Review Tasks vs Fix Tasks Sandbox Mode Confusion

**Issue Description**
Code review tasks only need read permission (read-only sandbox is fine), but fix tasks require write permission (`workspace-write`). tcd has no concept of task type; the orchestrator must manually specify `--sandbox workspace-write` for fix tasks, which is easy to forget.

**Root Cause Analysis**
`tcd start`'s `--sandbox` is optional, with a default determined by the provider (codex.py L154: `job.sandbox or "workspace-write"`). The current default is already `workspace-write`, but before the M-6 fix, this default was ineffective. Even after the fix, the orchestrator still needs to manually distinguish task types.

**Impact Assessment**
- Severity: **Medium** (misconfiguration causes task failure)
- Frequency: Low (only triggers when workflow design is flawed)

**Existing Fix**
The codex-worker Skill's "notes" section mentions `workspace-write` sandbox mode but does not explicitly distinguish between two task types.

**Recommended Solutions**
Explicitly distinguish two task modes in the codex-worker Skill:
- **Review mode**: prompt includes "do not modify code", `--sandbox read-only` (if Codex supports it)
- **Fix mode**: default `workspace-write`, emphasized in the Skill

Display actual sandbox mode in `tcd start` output:
```
Job started: 8e6c6b37
Provider: codex
Sandbox: workspace-write
tmux session: tcd-codex-8e6c6b37
```

**Priority**: P2

---

### P2-2: Non-git Repos Cannot Use git diff to Inspect Codex Changes

**Issue Description**
The orchestrator wanted to use `git diff` to inspect what Codex modified, but the project directory is not a git repository, so the command fails.

**Root Cause Analysis**
The project directory `/Users/michael/projects/AI 工作流/tmux-codingagent-driver` has no `.git` directory (or is not within git tracking scope). Codex's `parse_response_structured()` returns a `files_modified` list (via NDJSON `apply_patch` events), but this information is not exposed to the orchestrator.

**Impact Assessment**
- Severity: **Low** (does not affect task execution, only verification)
- Frequency: Medium (any non-git project will encounter this)

**Recommended Solutions**
1. Use tcd's structured output to get the changed file list:
   ```bash
   # Via Python SDK
   from tcd.providers.codex import CodexProvider
   output = prov.parse_response_structured(job)
   print(output.files_modified)
   ```
2. Add `--files-modified` flag to `tcd output`, showing the list of files Codex modified
3. Compare content hashes of changed files (before and after) as a substitute for git diff
4. In the codex-worker Skill, advise: when verifying changes, prefer `tcd output --files-modified`; use git diff as a supplement

**Priority**: P2

---

### P2-3: Codex Path with Spaces Causes Shell Command Failures

**Issue Description**
When Codex executes shell commands, the project path (`/Users/michael/projects/AI 工作流/tmux-codingagent-driver`) contains Chinese characters and a space, causing command failures.

**Root Cause Analysis**
Unquoted paths in shell commands containing spaces get split into multiple arguments. This is a typical issue when Codex generates shell commands.

**Impact Assessment**
- Severity: **Low** (Codex sometimes self-corrects)
- Frequency: Medium (any project with spaces in the path may trigger this)

**Existing Fix**
In this workflow run, Codex discovered and mitigated this issue itself by quoting the path.

**Recommended Solutions**
1. Add path quoting hints to the codex-worker Skill prompt template
2. Quote the path before passing it to the `-d` parameter of `tcd start` (`shlex.quote(cwd)`)
3. Long-term: avoid spaces in project directory names (the most fundamental solution)

**Priority**: P2

---

### P3: Skill Poll Mode Actual Effectiveness Unverified

**Issue Description**
The updated codex-worker Skill requires each poll to be an independent Bash call. Whether a separately running Claude Code process will strictly follow the new Skill has not been verified.

**Root Cause Analysis**
After Claude Code reads the Skill, execution strategy is determined by the model. The key constraint in the Skill ("each poll must be an independent Bash call") relies on the model following text instructions and is not a hard constraint. Under high load or with long context, the model may "regress" to using a while loop.

**Impact Assessment**
- Severity: **Low** (only affects user experience)
- Frequency: uncertain

**Recommended Solutions**
1. Observe whether Claude Code follows poll mode in the next codex-worker Skill use
2. If regression is found, add stronger constraint language to the Skill: `"CRITICAL: Never use while loops or tcd wait."`
3. Long-term: consider adding a `--watch` mode to `tcd check` that automatically outputs progress to stdout (working around the Bash blocking issue)

**Priority**: P3

---

## Root Cause Chain

```
M-6 (sandbox parameter not passed)
    → Codex runs in read-only sandbox
    → review tasks cannot write (acceptable)
    → fix tasks cannot write (blocker)
        → 3 retries (~25k tokens each)
            → 80k tokens wasted
                → missing pre-flight check is an amplifying factor
```

---

## Action Items

### Immediate (P0)

- [ ] Verify M-6 fix is effective: `tcd start --sandbox workspace-write`, confirm Codex runs in `workspace-write` mode
- [ ] Verify `codex -s workspace-write` parameter format (codex v0.110.0 changelog)
- [ ] Research Codex auto-update disable method (environment variable / npm pin version)

### Short-term (P1, this week)

- [ ] Add write permission probe at beginning of fix-type prompts: `touch .tcd-probe && echo OK || echo READONLY && exit`
- [ ] Display actual sandbox mode in `tcd start` output
- [ ] Add launch command logging for codex provider (debug level)

### Medium-term (P2, next iteration)

- [ ] `tcd output --files-modified`: display list of files Codex modified
- [ ] codex-worker Skill: explicitly distinguish review mode and fix mode
- [ ] Write test: `test_sandbox_flag_in_command`

---

## References

- Related job records: `~/.tcd/jobs/ece8b9e3.json` (review), `8e6c6b37.json` (third fix attempt)
- Workflow log: `docs/workflow-log.md`
- Codex provider source: `src/tcd/providers/codex.py`
- Codex Worker Skill: `~/.claude/skills/codex-worker/SKILL.md`

---
---

# Workflow Issue Analysis Report #2

**Date**: 2026-03-08
**Source**: feishu-cli auto-dev workflow (2026-03-08 22:40–23:30)
**Scenario**: Using tcd parallel worktree mode to develop 8 new CLI features for the feishu-cli project
**Jobs involved**: 3b18e3c1 (Group A: export+import), e76f3bcb (Group B: copy+move+folder), 16370815 (Group C: bitable-search+bitable-delete), 4af58644 (Group D: wiki-spaces)

---

## Issue Overview (by priority)

| Priority | Issue | Status |
|----------|-------|--------|
| P0 | `tcd merge` throws FileNotFoundError when worktree directory doesn't exist, but reports merge success | Unresolved |
| P1 | `tcd merge` cleanup phase `remove_worktree` can't find disappeared worktree directory | Unresolved (sub-problem of P0) |
| P1 | `worktree_repo_root` field not persisted to job JSON | Pending confirmation |
| P2 | Job status remains "running" after completion, `completed_at` is null | Unresolved |
| P2 | `tcd merge` success message inconsistent with actual result (user misled) | Unresolved |

---

## Detailed Analysis

---

### P0-3: `tcd merge` FileNotFoundError + False Success

**Issue Description**

In the feishu-cli project (path `/Users/michael/projects/组件模块/feishu-cli`), 4 parallel Codex tasks were launched with `tcd start --worktree`. All tasks completed coding and committed to their respective `tcd/<job_id>` branches. When `tcd merge <job_id>` was executed:

1. The command output `"Merged tcd/3b18e3c1 (merge)."` success message
2. Immediately followed by `FileNotFoundError: No such file or directory` pointing to the worktree path
3. **Actual inspection of the main branch showed: code was not merged**
4. Manually executing `git merge --no-ff tcd/3b18e3c1` successfully completed the merge

All 4 jobs reproduced this issue; 100% trigger rate.

**Reproduction Steps**

```bash
# 1. Start a worktree task in a repo with a non-ASCII path
cd /Users/michael/projects/组件模块/feishu-cli
tcd start -p codex -m "..." --worktree
# Job: 3b18e3c1, worktree: /Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1

# 2. Wait for Codex to finish coding (status check shows turn_state: idle)

# 3. Execute merge
tcd merge 3b18e3c1
# Output: "Merged tcd/3b18e3c1 (merge)."
# Then: FileNotFoundError: [Errno 2] No such file or directory: '/Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1'

# 4. Check main branch — code not merged
git log --oneline -3  # no merge commit visible

# 5. Manual merge succeeds
git merge --no-ff tcd/3b18e3c1  # merges cleanly, no conflicts
```

**Root Cause Analysis**

The problem is in the `merge()` function at `cli.py:693-737`, on two levels:

**Level 1: `repo_root` calculation may point to the wrong directory**

```python
# cli.py L708
repo_root = Path(job.worktree_repo_root) if job.worktree_repo_root else get_main_repo_root(job.cwd)
```

- `job.worktree_repo_root`: **missing** from the job JSON (see P1-3 below), causing a fallback
- `get_main_repo_root(job.cwd)` where `job.cwd` = worktree path `/Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1`
- If the worktree directory has been cleaned up, `subprocess.run(cwd=str(path))` throws `FileNotFoundError`
- If the worktree directory still exists but git state is abnormal, `get_main_repo_root` may return the wrong repo root
- `merge_branch()` executes `git merge` under the wrong repo root, which may be a no-op (already up to date), returncode=0, falsely reporting success

**Level 2: cleanup phase lacks defense**

```python
# cli.py L723-725
if not no_cleanup and job.worktree_path:
    try:
        remove_worktree(job.worktree_path)  # FileNotFoundError thrown here
```

`remove_worktree()` in `worktree.py:124-127`:
```python
def remove_worktree(worktree_path):
    wt = Path(worktree_path)
    if not wt.exists():
        return  # this line should guard, but FileNotFoundError still thrown
```

This indicates `wt.exists()` returns True (directory exists) but the subsequent `subprocess.run(cwd=str(wt))` is triggered after the directory is concurrently deleted, or the `common_dir_result`'s `subprocess.run`'s `cwd` resolution fails.

**Actual Job Data Evidence**

From `~/.tcd/jobs/3b18e3c1.json`:

```json
{
  "cwd": "/Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1",
  "worktree_path": "/Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1",
  "worktree_branch": "tcd/3b18e3c1",
  "status": "running",          // ← should be completed
  "completed_at": null           // ← should have a timestamp
  // Note: no worktree_repo_root field!
}
```

**Impact Assessment**
- Severity: **Blocking** (false-success merge leads user to believe code is merged; it's not)
- Frequency: 100% (all 4 jobs triggered)
- Affected: requires manual `git merge --no-ff` to recover, adding ~10 minutes of manual work. If not caught, may result in code loss

**Recommended Fix**

Option A (recommended, 3 steps):

1. **Fix `repo_root` calculation** (`cli.py:708`):

```python
# Prefer worktree_repo_root (original main repo path)
if job.worktree_repo_root:
    repo_root = Path(job.worktree_repo_root)
elif job.worktree_path and Path(job.worktree_path).exists():
    repo_root = get_main_repo_root(job.worktree_path)
else:
    # worktree no longer exists; try to infer original repo from branch name
    # or simply error out with a manual merge instruction
    click.echo(f"Error: worktree at {job.worktree_path} no longer exists.", err=True)
    click.echo(f"Manual merge: git merge --no-ff {job.worktree_branch}", err=True)
    sys.exit(1)
```

2. **Post-merge verification** (add after `cli.py:719`):

```python
# Verify merge actually took effect: check that branch HEAD is an ancestor of current HEAD
verify = subprocess.run(
    ["git", "merge-base", "--is-ancestor", job.worktree_branch, "HEAD"],
    cwd=str(repo_root), capture_output=True
)
if verify.returncode != 0:
    click.echo(f"Warning: merge may not have taken effect. Verify with: git log --oneline -5", err=True)
```

3. **Strengthen cleanup defense** (`worktree.py:remove_worktree`):

```python
def remove_worktree(worktree_path):
    wt = Path(worktree_path)
    if not wt.exists():
        return  # already gone, silent return
    try:
        # ... existing logic ...
    except (FileNotFoundError, WorktreeError):
        # worktree disappeared after check (concurrent deletion), ignore
        logger.warning("Worktree %s disappeared during cleanup", wt)
```

Option B (minimal change emergency):

Compute and validate `repo_root` at the beginning of the `merge()` function; if it cannot be determined, print the manual command and exit:

```python
def merge(job_id, squash, no_cleanup):
    # ... load job ...

    # Compute repo_root, prefer persisted original path
    repo_root = None
    if job.worktree_repo_root:
        repo_root = Path(job.worktree_repo_root)
    else:
        # Fallback: derive from worktree_path
        wt = Path(job.worktree_path) if job.worktree_path else None
        if wt and wt.exists():
            repo_root = get_main_repo_root(str(wt))
        elif job.cwd and Path(job.cwd).exists():
            repo_root = get_main_repo_root(job.cwd)

    if repo_root is None or not repo_root.exists():
        click.echo(f"Error: cannot determine repo root. Worktree may be deleted.", err=True)
        click.echo(f"Run manually: git merge --no-ff {job.worktree_branch}", err=True)
        sys.exit(1)
```

**Priority**: P0

---

### P1-3: `worktree_repo_root` Field Not Persisted to Job JSON

**Issue Description**

The `Job` dataclass defines `worktree_repo_root: str | None = None` at `job.py:57`, and `cli.py:142` correctly sets `job.worktree_repo_root = cwd` after creating the worktree. But the actually saved job JSON **does not have this field**.

**Evidence**

None of the 4 job JSON files contain the `worktree_repo_root` field:

```bash
# Check all 4 related jobs
grep -l "worktree_repo_root" ~/.tcd/jobs/{3b18e3c1,e76f3bcb,16370815,4af58644}.json
# No output — field does not exist
```

But `Job.to_dict()` uses `dataclasses.asdict()` which should serialize all fields including those with None values.

**Root Cause Analysis**

Two possibilities:

1. **Version mismatch**: user installed tcd via `uv tool install .`, but the installed version may predate the commit that added `worktree_repo_root`. The source already has this field, but the running `tcd` executable is an older version. This means `cli.py:142`'s `job.worktree_repo_root = cwd` only sets a non-dataclass attribute on the runtime object; `asdict()` won't serialize it.

2. **`save_job` timing issue**: `worktree_repo_root` is set before `save_job` (`cli.py:142-149`), so it should theoretically be saved. If it's a version problem, then the `to_dict()` called by `save_job` doesn't include this field.

**Verification Method**

```bash
# Check if the installed tcd version includes worktree_repo_root
python3 -c "from tcd.job import Job; print('worktree_repo_root' in Job.__dataclass_fields__)"

# Check source version
grep worktree_repo_root src/tcd/job.py
```

**Impact Assessment**
- Severity: **High** (directly causes P0-3; merge cannot find the correct repo root)
- Frequency: 100% (if confirmed as a version issue, all worktree jobs are affected)

**Recommended Fix**

1. Confirm installed vs source version: `tcd --version` vs `git log --oneline -1 src/tcd/job.py`
2. If versions differ: reinstall `uv tool install . --force`
3. Add version validation: after saving the job in `tcd start --worktree`, print the saved field list (debug level) to confirm `worktree_repo_root` is serialized
4. Long-term: add an explicit warning for `worktree_repo_root is None` in the `merge()` function

**Priority**: P1

---

### P2-4: Job Status Remains "running" After Completion

**Issue Description**

All 4 Codex jobs finished coding and committed, but the job JSON `status` is still `"running"` and `completed_at` is `null`.

**Evidence**

```json
// ~/.tcd/jobs/3b18e3c1.json
{
  "status": "running",
  "completed_at": null,
  "turn_count": 1,
  "turn_state": "idle",
  "last_agent_message": "Implemented scripts/export.js and scripts/import.js..."
}
```

`turn_state: "idle"` and a valid `last_agent_message` confirm Codex did complete the task, but the job status was not updated to `"completed"`.

**Root Cause Analysis**

The wait loop in the `tcd start` command (`cli.py`'s start function) is responsible for detecting completion and updating status. Possible causes:

1. The `tcd start` command's wait phase was externally interrupted (caller Ctrl-C or timeout) before detecting completion
2. The signal file (`.tcd/jobs/<id>.turn-complete`) detection logic doesn't match Codex's actual completion signal
3. The background `tcd start` process was lost after Claude Code's context compaction

**Impact Assessment**
- Severity: **Medium** (doesn't affect merging, but causes `tcd status` to show inaccurate info, and auto-cleanup logic won't trigger)
- Frequency: needs further investigation (may be related to tcd being called as a background process)

**Recommended Fix**

1. In `tcd merge` and `tcd output`, when `turn_state == "idle"` and `last_agent_message` is non-empty, automatically update status to `"completed"`
2. `tcd check` adds detection for "actually complete but status not updated" (infer from tmux session state)
3. Add `tcd fix-status <job_id>` sub-command to manually trigger status correction

**Priority**: P2

---

### P2-5: `tcd merge` Success Message Inconsistent with Actual Result

**Issue Description**

`tcd merge` output `"Merged tcd/3b18e3c1 (merge)."` but the code was not actually merged to the main branch. The user assumed the merge was complete after seeing the success message, only discovering the missing code during subsequent operations.

**Root Cause Analysis**

`cli.py:711`'s `merge_branch()` returns `True` (`git merge` returncode=0), but possible scenarios:
- `git merge` was executed in the wrong `repo_root`, result is "Already up to date" (returncode=0 but no actual merge)
- Or the merge did succeed in some directory, but not in the user's expected main branch

`merge_branch()` only checks returncode, not whether a merge commit was actually created:

```python
# worktree.py:181-187
result = subprocess.run(cmd, cwd=str(repo_path), capture_output=True, text=True)
return result.returncode == 0  # "Already up to date" also returns 0!
```

**Recommended Fix**

1. `merge_branch()` checks if stdout contains "Already up to date"; if so, returns a special status
2. Post-merge verification: check if `git log -1 --format=%H` changed
3. Output more detailed info: `"Merged tcd/xxx (merge): 3 files changed, 204 insertions(+)"`

**Priority**: P2

---

## Root Cause Chain

```
worktree_repo_root not persisted (P1-3, possibly a version issue)
    → merge() cannot get original repo path
    → fallback to get_main_repo_root(job.cwd)
    → job.cwd points to worktree path (may not exist or be in abnormal state)
        → Case A: directory doesn't exist → FileNotFoundError
        → Case B: directory exists but repo_root computed wrongly → git merge runs in wrong dir
            → "Already up to date" → returncode=0 → false success reported
                → user thinks merge is done; code not actually merged
    → cleanup phase remove_worktree triggers FileNotFoundError again
```

Contributing factors:
- Job status not correctly updated to completed (P2-4), so auto-cleanup didn't trigger
- Merge success message lacks verification (P2-5), user misled

---

## Action Items

### Immediate (P0)

- [ ] Verify installed vs source version: `python3 -c "from tcd.job import Job; print(Job.__dataclass_fields__.keys())"` and compare to source
- [ ] If versions differ, reinstall: `cd ~/projects/AI\ 工作流/tmux-codingagent-driver && uv tool install . --force`
- [ ] In `merge()` function: when fallback to `worktree_repo_root` fails, print the manual merge command instead of falsely reporting success
- [ ] After `merge_branch()` returns, verify the merge actually took effect (use `git merge-base --is-ancestor` or check HEAD change)

### Short-term (P1, this week)

- [ ] Add warning log for `worktree_repo_root is None` in `merge()`
- [ ] Add try/except defense to `remove_worktree()`; don't throw exception when directory disappears
- [ ] `merge_branch()` distinguishes "Already up to date" from a true successful merge
- [ ] Output `git diff --stat` summary after successful merge

### Medium-term (P2, next iteration)

- [ ] `tcd check` adds detection and auto-correction for "actually complete but status not updated"
- [ ] `tcd merge` adds `--dry-run` mode, showing what would be done without executing
- [ ] Add integration test: complete worktree lifecycle in a repo with non-ASCII path (e.g. Chinese)

---

## Reproduction Environment

- macOS Darwin 24.6.0
- tcd source version: v0.3.0 (`~/projects/AI 工作流/tmux-codingagent-driver`)
- Project path: `/Users/michael/projects/组件模块/feishu-cli` (note Chinese `组件模块`)
- Codex provider
- 4 parallel worktree jobs triggered simultaneously

## References

- Related job records: `~/.tcd/jobs/3b18e3c1.json`, `e76f3bcb.json`, `16370815.json`, `4af58644.json`
- Source: `src/tcd/cli.py:693-737` (merge function), `src/tcd/worktree.py:100-161` (worktree operations), `src/tcd/job.py:36-74` (Job data model)
- Manual fix record: feishu-cli project git log (`git merge --no-ff tcd/3b18e3c1` and 3 other similar commits)

---
---

# Workflow Issue Analysis Report #3

**Date**: 2026-03-09
**Source**: tcd self bug-fix + Codex Code Review + parallel worktree fix workflow
**Scenario**: Using tcd to drive Codex to review and fix tcd's own worktree merge code

---

## Issue Overview

| Priority | Issue | Status |
|----------|-------|--------|
| P0 | AI doesn't commit in worktree, merge results in "Already up to date" | Fixed (Skill prompt instruction + merge pre-check) |
| P1 | Bootstrapping: fixing tcd with old tcd that contains the bug being fixed | Mitigated (Skill bootstrap warning) |
| P2 | STALL/TURN0_STUCK false positives (AI generating long text) | Fixed (pane_hash change detection) |
| P2 | merge cannot distinguish "no commits" from "already merged" | Fixed (branch_has_new_commits pre-check) |

---

## Detailed Analysis

### P0-4: AI Doesn't Commit in Worktree

**Issue Description**

`tcd start --worktree` was used to dispatch tasks to Codex. Codex completed code modifications and passed tests, but did not execute `git commit`. The worktree branch had no new commits. When `tcd merge` ran, `git merge` returned "Already up to date" (returncode=0); the old tcd version reported merge success with no actual change. The cleanup phase deleted the worktree directory and the AI's modifications were permanently lost.

**Root Cause Analysis**

tcd's worktree feature assumes the AI will self-commit changes, but Codex in full-auto mode does not commit by default. This is a **missing contract** between tcd (transport layer) and Skill (orchestration layer):
- tcd provides `create → merge → cleanup` lifecycle primitives
- Skill is responsible for guiding AI behavior in the prompt (including committing)
- But the codex-worker Skill previously did not include commit instructions

**Fix** (implemented)

1. **Skill layer** (`codex-worker/skill.md`): in worktree scenarios, prompt must append commit instructions
2. **tcd layer** (`worktree.py`): added `branch_has_new_commits()` function, pre-checks before merge
3. **tcd layer** (`cli.py` + `sdk.py`): calls pre-check before merge; outputs clear diagnostics and exits when no new commits

### P1-4: Bootstrapping — Fixing tcd with Old tcd

**Issue Description**

The development workflow used `tcd merge` to merge Codex-fixed code, but the globally installed tcd was the old version (containing the merge false-success bug). Result:
- Group B worktree was deleted by old `tcd merge`'s cleanup phase; code was lost
- All Group B modifications had to be manually re-implemented

**Mitigation** (implemented)

Added a bootstrapping warning to the codex-worker Skill notes: when the modification target is tcd itself, recommend manual merge before updating the global version.

### P2-6: STALL False Positive

**Issue Description**

When Codex was generating long text (such as a code review report), `tcd check` detected `state=working` 4 consecutive times with elapsed > 60s, triggering a STALL warning. But the AI was actually working normally; the output just took a long time.

**Root Cause Analysis**

The original STALL rule only checked whether the `state` field in `job.checked` events had changed; it did not check whether pane content was updating. Long `state=working` duration does not equal being stuck.

**Fix** (implemented)

1. In `cli.py` and `sdk.py`'s check flow, when `state=working`, compute an md5 hash of the pane content and write it to the `job.checked` event
2. `diagnostics.py` R2 rule checks `pane_hash`: if the hash changes between consecutive checks, the AI is actively outputting; STALL is not triggered
3. Backward compatible: when no hash data is present (old events), original logic still applies

### P2-7: merge Cannot Distinguish "No Commits" from "Already Merged"

**Issue Description**

`git merge` returns "Already up to date" (returncode=0) for both "branch has no new commits" and "branch was already merged". The previous noop detection could only discover this after the merge, not distinguish the two cases before merging.

**Fix** (implemented)

Added `branch_has_new_commits(repo_path, branch)` function using `git log HEAD..branch` for pre-checking. Called before merge; errors out with user guidance when commit count is 0.

---

## Responsibility Boundary Analysis

This fix round clarified the responsibility boundary between tcd and Skill:

| Responsibility | tcd (transport layer) | Skill (orchestration layer) |
|----------------|-----------------------|-----------------------------|
| AI must commit | Provide pre-check and clear error messages | Include commit instructions in prompt |
| Bootstrap detection | Not applicable | Remind in notes |
| Stuck detection | Use pane_hash to distinguish truly stuck from working | Decide whether to intervene based on STALL warning |
| Branch checking | Provide `branch_has_new_commits()` | Not applicable |

**Core principle**: tcd provides tools and signals; Skill provides strategy and decisions.

---

## References

- Fix commits: see `git log --oneline -5` on main
- Affected files: `src/tcd/worktree.py`, `src/tcd/cli.py`, `src/tcd/sdk.py`, `src/tcd/diagnostics.py`
- New tests: `test_r2_stall_suppressed_when_pane_hash_changes`, `test_r2_stall_triggers_with_same_pane_hash`, `test_merge_command_no_new_commits`
- Skill update: `~/.claude/skills/codex-worker/skill.md`

---

# 2026-08 Production Review (June–August usage)

**Date**: 2026-08-03
**Source**: audit of two months of orchestration transcripts (19 sessions that
actually drove tcd) cross-checked against the code
**Trigger**: a routine "is the repo up to date?" question

Everything below was reproduced or evidenced before being filed. The prior
report (2026-03) is a record of issues found *while* building tcd; this one is
the first review driven by how tcd behaved in sustained production use.

## Issue Overview

| Priority | Issue | Status |
|----------|-------|--------|
| **P0** | Auto-stash popped by stack position, not by ref — parallel jobs swap each other's changes | **Fixed (2026-08-03)** |
| **P0** | Auto-stash restored only by `tcd merge`; `tcd kill` stranded it | **Fixed (2026-08-03)** |
| **P0** | `tcd kill` ran `git worktree remove --force`, discarding uncommitted agent work | **Fixed (2026-08-03)** |
| P1 | `tcd clean` deleted the job record that is the only pointer to a worktree / stash | **Fixed (2026-08-03)** |
| P1 | Job records never reconciled: phantom `running` jobs, elapsed measured against "now" | **Fixed (2026-08-03)** |
| P1 | No detection of fatal provider-side errors (bad model id, upstream 5xx) | **Fixed (2026-08-03)** |
| P1 | v0.3.2 launch hardening applied to Codex only; base-class defaults left other providers exposed | **Fixed (2026-08-03)** |
| P2 | `--sandbox` accepted and echoed for providers that ignore it (same class as 2026-03 M-6) | **Fixed (2026-08-03)** |
| P2 | Claude session lookup returned the globally newest transcript — usually the orchestrator's own | **Fixed (2026-08-03)** |
| P2 | `--provider` choices hardcoded despite an existing registry | **Fixed (2026-08-03)** |
| P2 | Activity extraction filtered chrome with Codex-only prefixes for every provider | **Fixed (2026-08-03)** |
| P3 | Detection strings are coupled to upstream CLI wording with no drift alarm | **Fixed (2026-08-03, `tcd doctor`)** |

---

## P0-1: Auto-stash Popped by Position

**Evidence**

Six stashes named `tcd: auto-stash before worktree`, dated 2026-07-24 to
07-26, stranded across two repos driven by tcd (four in one, two in another).
The largest held 7 files, +513/-95 lines of the user's uncommitted work.

**Root Cause**

`auto_stash()` captured the stash commit SHA and persisted it as
`job.worktree_stash_ref`, but `stash_pop()` ran a bare `git stash pop` — the
*top* of the stack. The ref was only ever read as a boolean
(`if job.worktree_stash_ref:`). Since the stash stack is repo-wide and tcd's
headline feature is parallel worktree jobs, job A's merge restored whichever
stash happened to be on top — often job B's.

**Fix**

`find_stash_selector()` re-resolves the SHA to its current `stash@{n}`
position immediately before popping (positions shift on every push/pop). A ref
that is no longer on the stack now returns False loudly instead of popping
something else.

**Generalisable rule**: if you record an identifier, operate with it. Any
"top of the stack / most recent / the default one" operation is a race as soon
as the system supports concurrency.

---

## P0-2: Cleanup Wired to One Exit Path

**Root Cause**

`tcd start --worktree` mutates the caller's environment (stash, worktree,
branch, tmux session). Restoration lived only in `tcd merge`. But a job has
five exits: merge, kill, clean, timeout, crash. The observed July workflow —
`git merge --ff-only tcd/<id>` with raw git, then `tcd kill` — never touched
the restore path.

**Fix**

`_restore_auto_stash()` runs on kill as well as merge, guarded by
`job.worktree_stash_restored` so the two paths cannot double-pop.

**Generalisable rule**: a side effect applied at start needs a defined
disposition on *every* terminal path, not just the successful one.

---

## P0-3: `tcd kill` Destroyed Uncommitted Agent Work

**Root Cause**

`remove_worktree()` uses `git worktree remove --force`, and `_kill_job()`
called it unconditionally. "The AI forgets to commit" is a failure mode this
very document recorded in 2026-03 (P0-3 of the previous report) — but that was
mitigated only at the prompt layer, while the destructive path was untouched.

**Fix**

Kill now checks `worktree_is_dirty()` and `branch_has_new_commits()` first; a
worktree holding either is kept, with its path and branch printed. `--force`
restores the old behaviour.

**Generalisable rule**: mitigating a failure at the input layer does not make
the destructive path safe. Fix the class, not the instance.

---

## P1-1: Job Records Never Reconciled

**Evidence**

`tcd jobs` at audit time: 81 job records, 54 marked `running`, against 9 live
tmux sessions — 45 phantoms. The oldest reported 30 days of elapsed time.

**Root Cause**

`JobManager.list_jobs()` reads JSON without checking tmux liveness
(`_refresh_status()` existed but no listing path called it), and `_elapsed()`
computed `now - started_at` regardless of terminal state.

**Fix**

`tcd jobs` reconciles against a single `tmux list-sessions` call
(`--no-reconcile` opts out), and `_elapsed()` stops at `completed_at`.

**Generalisable rule**: state that mirrors an external process must be
reconciled on read; a write-time snapshot always drifts.

---

## P1-2: Fatal Provider Errors Read as "working"

**Evidence**

- 2026-07-10: model id typo `gpt-5.6-luna` → Codex printed
  `{"type":"invalid_request_error","status":400}` in the pane; tcd reported
  `state: working` until timeout.
- 2026-07-25: upstream 503 `biscuit_baker_service_me_circuit_open` with
  `Reconnecting... 5/5`; again `state: working`, with `TURN0_STUCK` as the only
  hint.

**Fix**

`Provider.detect_provider_error()` on the base class, surfaced as a
`PROVIDER_ERROR` warning from `tcd check --json`.

---

## P1-3: Hardening Applied to One Provider

**Root Cause**

The v0.3.2 launch fixes were set as class attributes on `CodexProvider` only.
The base class kept `tui_stable_secs = 0.0` and `verify_prompt_delivery =
False`, so `claude` and `gemini` still had the exact prompt-drop bug that
v0.3.2 spent a release fixing — and any new provider would inherit it too.

**Fix**

Safe values are now the base-class defaults; providers opt out rather than
opt in.

**Generalisable rule**: defaults should be the safe side. A default that
reproduces a known bug guarantees the next implementer rediscovers it.

---

## Provider Parity (as of 2026-08-03)

The architecture is provider-neutral (ABC + registry, almost no vendor leakage
outside `providers/`), but coverage is not. Only the Codex path has sustained
production use.

| Capability | codex | claude | gemini |
|---|---|---|---|
| TUI stability gating | 2.5s | 2.0s (inherited) | 2.0s (inherited) |
| Prompt-delivery verification | yes | inherited | inherited |
| Startup auto-update suppressed | yes | — | — |
| Trust dialog handled | yes | — | — |
| MCP startup blocking avoided | yes | — | — |
| `--sandbox` honoured | yes | rejected | rejected |
| Queued-follow-up recovery | — | yes | — |
| Session file located | yes | scoped to job | none (pane only) |

The dashes are not bugs; they are untested territory. Anyone driving
`claude` or `gemini` for real work should expect to find the equivalent of the
v0.3.2 issues.

---

## P3 (Open): Detection Strings Have No Drift Alarm

Every completion and readiness decision is a substring match against upstream
TUI wording: `›` / `❯` / `Working (` / `esc to interrupt` /
`Press up to edit queued messages` / exactly two `TCD_DONE` markers /
a 50-line marker scan window. When an upstream CLI changes its wording these
fail *silently* — the job reports working forever or idle immediately, and the
caller only finds out at timeout.

**Fix**

`tcd doctor` (implemented by Codex, driven through tcd itself). Static mode
reports the detection constants each provider is assuming and flags job
records whose tmux session is gone; `--live` starts a throwaway session per
provider and verifies the readiness indicator and prompt delivery still hold.
Run it after every upstream CLI upgrade.

Two defects were found reviewing it on merge, both instances of patterns this
report already names: the version probe only tried `--version` (tmux needs
`-V`, so the dependency tcd relies on most was reported as unknown), and the
live probe verified delivery with the default markers rather than the
provider's own — the same duplicate-submission trap that per-provider markers
exist to prevent.

---

## References

- Fix commit: `afdea4d`
- Affected files: `src/tcd/worktree.py`, `src/tcd/cli.py`, `src/tcd/sdk.py`,
  `src/tcd/job.py`, `src/tcd/provider.py`, `src/tcd/providers/claude.py`,
  `src/tcd/providers/codex.py`, `src/tcd/tmux_adapter.py`
- Tests: 290 passing (was 253)

---

# 2026-08-04 重构评审附带发现

**触发**: 把 A–G 七条重构提案交给 Codex（gpt-5.6-sol, xhigh）做对抗性评审
**结论**: 大部分提案不值得现在做；评审在代码里找到 5 个真实缺陷，优先级高于全部提案

| 缺陷 | 性质 | 状态 |
|---|---|---|
| `--require-commit` 要求 tcd 自建分支，而 auto-dev 自有 worktree → **auto-dev 默认派发被 tcd 直接拒绝** | 当天引入，跨层契约断裂 | v0.6.0 修复 |
| start 失败回滚不杀 tmux → agent 在被删掉的目录里继续跑 | 一直存在 | v0.6.0 修复 |
| 仓库锁按调用路径 hash → 主目录与 linked worktree 拿到**不同的锁** | v0.4.0 的锁在 worktree 场景下没生效 | v0.6.0 修复 |
| `merge --squash` 只暂存却报完成、删 worktree、强删分支 | 一直存在，会丢工作 | v0.6.0 修复 |
| merge 不杀会话 + `held_resources()` 不算会话 → clean 可删掉唯一记录 | v0.4.0 补漏时漏的 | v0.6.0 修复 |
| `tcd check` 对 failed 也返回 0 | 一直存在 | v0.6.0 修复 |

## 值得记住的三件事

**1. 同一轮里改两层，从没一起测过。** `--require-commit` 的守卫加在 tcd，调用方改在
auto-dev，两处都"看起来对"，合起来直接报错。**跨层契约必须端到端跑一次**，单元测试
各自绿不能说明问题。

**2. 修复本身会制造新的漂移面。** v0.4.0 加仓库锁修了 stash 串号，但锁的身份用的是
调用者路径——**恰好在 worktree 场景下失效，而那正是它存在的理由**。同理 v0.4.0 补齐
了五条终止路径的资源释放，却没把 tmux 会话算进资源。**每次"补漏"之后要问：这个补丁
自己有没有引入同类漏洞。**

**3. 对抗性评审的价值在于否决。** 七条提案里评审明确说"不做"的有三条（C/F/G 原案），
"推翻重来"的两条（A/D），真正确认要做的只有一条（B 删 SDK）。而它顺手找到的 5 个缺陷
比任何一条提案都重要。**列出来的债不等于该还的债。**
