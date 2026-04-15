# Workflow Log: Codex Code Review & Fix

## Metadata

- **Date**: 2026-03-05
- **Orchestrator**: Claude Code (claude-opus-4-6)
- **Sub-agent**: Codex CLI (gpt-5.3-codex xhigh, v0.106.0 → v0.110.0)
- **Verification agent**: Reviewer sub-agent
- **Goal**: Use Codex to do code review → review findings → apply fixes → update documentation

## Timeline

### 11:10 - Step 1: Dispatch Codex for Code Review

- **Job ID**: ece8b9e3
- **Method**: `tcd start -p codex` + `tcd wait` (old Skill)
- **Duration**: ~5 minutes
- **Result**: Codex completed a comprehensive review (18 issues), but could not write to docs/code-review.md due to read-only sandbox
- **Handling**: Extracted content from `tcd output`; orchestrator manually created docs/code-review.md
- **Problem**: Using `tcd wait` blocked the entire process; user saw no progress during the wait

### 11:15 - Step 2: Orchestrator Review + Reviewer Cross-Validation

- **Method**: Started a Reviewer sub-agent to validate all findings in parallel
- **Duration**: ~1 minute
- **Key findings**:
  - C-1 was overestimated (actually uses full req_id matching, not prefix) → downgraded
  - C-2 was more severe than described (Claude/Gemini turn_count never increments)
  - M-5 was a false positive (mkstemp defaults to 0o600)
- **Result**: Added detailed comments at the bottom of docs/code-review.md, annotating 7 Accept and 12 Defer/Reject items

### 11:20 - Step 3: First Attempt to Have Codex Apply Fixes

- **Job ID**: 9fc1e82d
- **Method**: `tcd start` + `tcd wait`
- **Result**: Codex auto-updated to v0.110.0 and required a restart
- **Problem**: `tcd wait` timed out (10 minutes); user had no visible progress

### 11:25 - Step 4: Second Attempt to Have Codex Apply Fixes

- **Job ID**: 8d45037f
- **Method**: `tcd start` + `tcd wait`
- **Result**: Codex reviewed the orchestrator's comments and **agreed with the Accept list, with a disagreement on C-1** (argued it should be fixed)
- **Problem**: Still in a read-only sandbox; could not write files or run tests
- **Codex feedback summary**:
  - Confirmed final fix list: P0(C-2, C-3) + P1(M-1, M-4, M-6, m-1, m-3) + C-1 (contested)
  - On C-1: "the provider side still uses req_id prefix matching; the risk of false positives has not truly been eliminated"

### 11:30 - Step 5: Third Attempt (with --sandbox workspace-write)

- **Job ID**: 8e6c6b37
- **Method**: `tcd start --sandbox workspace-write` + `tcd wait`
- **Result**: Still failed — the `--sandbox` parameter was dead code (M-6); it was never passed to the provider command
- **Ironic discovery**: The bug M-6 we wanted to fix was exactly what prevented Codex from executing the fixes

## Problem Summary

### Workflow Level

| Problem | Impact | Improvement Direction |
|------|------|---------|
| `tcd wait` blocking | User sees no progress during the wait | Skill updated to polling mode (improved in Step 2) |
| Codex sandbox read-only | Cannot write files or run tests | Fix M-6 (sandbox parameter passing) and retry |
| Codex auto-update | Interrupted a running task | Consider pinning version or disabling auto-update |
| Non-git repository | Cannot use git diff to check changes | Compare by file timestamp or content hash |

### Communication Efficiency

| Round | Task | Result | Token Cost |
|------|------|------|-----------|
| 1 | Review | Complete but cannot write file | ~30k |
| 2 | Fix | Codex auto-updated, wasted | ~5k |
| 3 | Fix | Review complete but cannot write file | ~40k |
| 4 | Fix | Still cannot write file | ~35k |

**Total waste**: ~80k tokens in a repetitive "read code → discover can't write" loop.

### Root Cause Analysis

1. **M-6 is the blocker**: `--sandbox` parameter not passed to provider; Codex always runs with default sandbox mode
2. **Sandbox mode does not match task**: Code review needs no write access; fix tasks require it
3. **No pre-flight check**: Write permission should be verified before starting a fix task

### 11:35 - Step 6: Orchestrator Manually Fixes M-6

- **Change**: Wired the `--sandbox` parameter through Job → create_job → codex provider's `build_launch_command`
- **Files**: job.py (added sandbox field), cli.py (passes parameter), sdk.py (passes parameter), codex.py (`-s {sandbox}`)
- **Tests**: 147 passed, 0 failed
- **Problem**: `uv tool install . --force` did not actually rebuild; `--reinstall` was needed

### 11:40 - Step 7: Fourth/Fifth Attempts (Old Binary)

- **Job IDs**: 82705ea4, 7f9e6ede
- **Method**: `tcd start` + polling (new Skill)
- **Result**: Both failed because the installed tcd binary was still the old version (no `-s` parameter)
- **Discovery**: `uv tool install . --force` used the cache; `--reinstall` is needed to force a rebuild
- **New Skill experience**: Polling mode worked correctly; Codex progress was visible in real time

### 11:55 - Step 8: Orchestrator Fixes Install Issue

- **Action**: `uv tool install . --force --reinstall` to force a rebuild
- **Verification**: `ps aux` confirmed `-s workspace-write` appeared in process arguments

### 11:59 - Step 9: Codex Successfully Applies Fixes (Final)

- **Job ID**: fc73d94b
- **Method**: `tcd start` + polling (new Skill)
- **Duration**: ~13 minutes
- **Progress timeline**:
  - 0–35s: Read review document and source code
  - 35s–3m: Located all fix points
  - 3m–5m: **C-2 fix** (turn_count increment + _advance_turn_if_needed)
  - 5m–6m: **C-3 fix** (MODEL_RE whitelist + shlex.quote)
  - 6m–7m: **M-1 fix** (Gemini response extraction skips prompt)
  - 7m–8m: **M-4 fix** (session disappearance distinguishes completed/failed)
  - 8m–9m: **m-1 fix** (send_text return value check)
  - 9m–10m: **m-3 fix** (narrow except Exception)
  - 10m–11m: **C-1 fix** (strict marker matching; Codex decided autonomously)
  - 11m–13m: Fix tests + generate fix-report.md
- **Test results**: 156 passed, 4 failed (tmux integration tests limited by sandbox)
- **Local verification**: 160 passed, 0 failed

## Final Fix Statistics

| Issue ID | Severity | Fixed By | Status |
|---------|--------|--------|------|
| C-1 | Critical→Minor | Codex (autonomous decision) | Fixed |
| C-2 | Critical | Codex | Fixed |
| C-3 | Critical | Codex | Fixed |
| M-1 | Major | Codex | Fixed |
| M-4 | Major | Codex | Fixed |
| M-6 | Major | Claude Code (orchestrator) | Fixed |
| m-1 | Minor | Codex | Fixed |
| m-3 | Minor | Codex | Fixed |

**Total: 8 issues fixed** (Codex: 7, orchestrator: 1); 13 new test cases added.

## Workflow Lessons Learned

### What Worked Well

1. **Polling mode is significantly better than tcd wait**: user can see Codex progress in real time; orchestrator can do other work while waiting
2. **Cross-validation is valuable**: Reviewer sub-agent identified Codex overestimates/underestimates and false positives
3. **Nudge mechanism is effective**: after `tcd send` sent a nudge message, Codex accelerated
4. **Codex autonomous judgment**: although the orchestrator suggested deferring C-1, Codex fixed it autonomously — and it turned out to be the right call

### Areas for Improvement

1. **Pre-flight sandbox check**: before starting a fix task, verify Codex has write permission (touch a test file)
2. **Install verification**: after `uv tool install`, verify the installed code was actually updated (grep for a key change)
3. **Task decomposition**: large fix tasks should be split into independent small tasks to avoid exhausting context in one shot
4. **Polling interval**: every 15 seconds for the first minute, then every 30–60 seconds is more appropriate (reduces wasteful polls)
5. **Codex reads too much**: Codex tends to re-read all files repeatedly before acting; prompts should more strongly emphasize "write code directly"
