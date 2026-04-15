# tcd Workflow Log System Analysis Report

**Date**: 2026-03-05
**Background**: Analysis of a multi-round Claude Code orchestrating Codex for code review + fixes (7 jobs, approximately 1 hour total)

---

## 1. Existing Log Coverage Assessment

### 1.1 Log Sources and Coverage Matrix

| Information Type | workflow-log.md | job.json | .turn-complete | .log (ANSI) |
|----------|:--------------:|:--------:|:--------------:|:-----------:|
| Job ID | Present | Present | - | - |
| Start time | Coarse (minute-level) | Precise ISO timestamp | - | - |
| End time | Absent | Precise ISO timestamp | Has timestamp | - |
| Duration | Estimated ("~5 min") | Computable | - | - |
| provider/model | Present | provider present, model=null | - | - |
| sandbox parameter | Text description | Present (only in latest job) | - | - |
| Prompt content | Summary | Full text | - | - |
| Failure reason | Manual description | "killed by user" (imprecise) | Final message | - |
| Sandbox error details | Present (extracted from last_message) | Truncated message | Truncated message | Full but with ANSI |
| Token consumption | Manual estimate (rough) | Absent | Absent | In NDJSON |
| Codex version | Present (v0.106→v0.110) | Absent | Absent | Possibly in log |
| Auto-update events | Described | Absent | Absent | Present |
| Retry count/reason | Manually recorded | Absent (each retry is a separate job) | - | - |
| Workflow association | Present (manually written) | Absent | Absent | - |
| tcd Skill version changes | Described | Absent | Absent | - |
| M-6 manual fix event | Present | Absent | Absent | - |

**Coverage score**: Machine-readable information covers ~40%; upper-level workflow information is 100% manually maintained.

### 1.2 Actual Quality of Each Log File

**job.json (most reliable machine data)**

Strengths:
- Precise timestamps (ISO 8601 with timezone)
- Full prompt preserved
- cwd and provider accurate

Weaknesses:
- `error` field is always `"killed by user"` regardless of whether the cause was a sandbox failure, timeout, or user-initiated termination — three completely different failure modes collapsed into one
- `model` field is always `null` (Codex actually used gpt-5.3-codex xhigh, but tcd never captured this)
- `sandbox` field only exists in the latest job fc73d94b (older jobs lack it, yet 8e6c6b37 failed because of a sandbox bug)
- `turn_count` is meaningless for Codex (Codex uses notify-hook; turn_count is always 0 or 1)
- `result` field is always `null`

**.turn-complete (highest information density)**

Strengths:
- Contains the final message from Codex (longest and most detailed)
- Has an ISO timestamp
- Has `turnId` (Codex internal turn UUID)

Weaknesses:
- Message is truncated (JSON field hard-truncated, incomplete) — all three signal files exhibit this
- No token counts
- No files_modified information
- Only present for jobs that completed a full turn (9fc1e82d, 82705ea4, 7f9e6ede have no signal files)

**workflow-log.md (most complete but fully manual)**

Strengths:
- Records information completely absent from machine logs: Codex version, auto-update events, M-6 manual fix, workflow decision rationale, token estimates

Weaknesses:
- Time granularity is minutes (cannot reconstruct precise event sequence)
- Requires manual maintenance, very easy to miss entries
- Does not cover the last 3 jobs (82705ea4, 7f9e6ede, fc73d94b) — workflow log was abandoned as pace increased

---

## 2. Missing Information Inventory

### P0-Level Missing (severely impacts post-mortem analysis and issue reproduction)

1. **Failure reason classification is inaccurate**: All jobs killed by `tcd kill` are recorded as `"error": "killed by user"`, making it impossible to distinguish:
   - Sandbox read-only preventing task completion, forcing a user kill
   - Codex auto-update interrupting the process
   - Timeout kill
   - User voluntarily abandoning the task

2. **No machine record of token consumption**: `event_msg.token_count` is present in the Codex NDJSON event stream; `parse_codex_ndjson()` can already parse it, but it is never written to job.json. workflow-log.md relies on manual estimates ("~30k tokens"), which have high error margins and cannot be verified.

3. **.turn-complete messages are truncated**: The `lastAgentMessage` in all three signal files is truncated at critical points (e.g., the specific file path in a sandbox error). The truncation is not a JSON limitation — it comes from the Codex notify-hook itself.

4. **No workflow context association**: The 7 jobs are independent JSON files with no field indicating they belong to the same workflow, which is a retry, or which has a predecessor dependency.

### P1-Level Missing (impacts debugging efficiency)

5. **Codex version not recorded**: `model` field is null; the Codex CLI version (auto-updated from v0.106 to v0.110) is completely unrecorded. Auto-update was one of the most disruptive events in this session.

6. **Sandbox parameter history missing**: Job 8e6c6b37 failed due to a sandbox bug, but its JSON has no `sandbox` field (the field was added later). It is impossible to reconstruct from logs that `--sandbox workspace-write` was once attempted but did not take effect.

7. **Workflow log abandoned after the 5th job**: Jobs 82705ea4, 7f9e6ede, and fc73d94b have no manual records — the workflow pace accelerated and there was no time to write entries.

8. **Codex internal session files not linked**: `~/.codex/sessions/` contains Codex's full NDJSON event streams, but job.json does not record the corresponding session file path (relying on a heuristic "latest file" match, which is unreliable).

### P2-Level Missing (impacts efficiency analysis)

9. **TUI initialization time not recorded**: Every `tcd start` waits for the Codex TUI to become ready; this time (sometimes up to 10s due to a trust dialog) is not recorded.

10. **`tcd check` poll count not recorded**: After the Skill switched to polling mode, the number of polls before completion was detected is not recorded.

11. **No diff comparison of prompt versions**: Jobs 9fc1e82d and 8d45037f used nearly identical prompts, but the latter produced actual output (Codex analysis results), likely because the Codex restart cleared its context. This detail cannot be reconstructed from logs.

---

## 3. Improvement Recommendations

### 3.1 tcd Layer (highest priority — small changes, large gains)

**Recommendation T-1: Refine error reason classification**

Current: all kills write `"killed by user"`

Improved: distinguish in `kill()` and `_refresh_status()`:
- `"killed_by_user"` — user-initiated kill
- `"session_disappeared"` — tmux session vanished (Codex crash / auto-update)
- `"timeout"` — tcd wait timed out
- `"task_blocked"` — task cannot proceed but agent has responded (requires Skill-layer tagging)

Implementation locations: `cli.py:_kill_job()`, `sdk.py:kill()`, `cli.py:_refresh_status()`

**Recommendation T-2: Write token consumption to job.json**

`parse_codex_ndjson()` can already parse `tokens`; when `detect_completion()` or notify-hook fires, write the token counts to job.json.

Add fields to the Job data class:
```python
tokens_input: int | None = None
tokens_output: int | None = None
```

Implementation locations: `providers/codex.py:detect_completion()` or `notify_hook.py`

**Recommendation T-3: Record Codex session file path**

When `detect_completion()` succeeds, write the path found by `_find_session_file()` to job.json:

```python
session_file: str | None = None
```

Avoids re-guessing in subsequent `parse_response_structured()` calls.

**Recommendation T-4: Prevent .turn-complete message truncation**

The message written by the current Codex notify-hook is truncated. Recommend writing the full message in `notify_hook.py`, or at minimum recording the truncation (`"truncated": true`).

### 3.2 Skill Layer (medium priority)

**Recommendation S-1: Pass workflow ID to tcd**

When the Skill starts a job, pass the workflow ID via prompt metadata or a future `--tag` parameter, so multiple jobs in the same workflow can be correlated.

Short-term approach: prepend a structured comment to the prompt:
```
<!-- workflow:codex-review-fix-20260305 retry:3 -->
```

**Recommendation S-2: Skill auto-appends structured events to workflow log**

The codex-worker Skill automatically appends one line to `docs/workflow-log.md` at these moments:

```
[11:20:15] START job=8d45037f provider=codex sandbox=none retry=2
[11:41:19] DONE  job=8d45037f status=idle tokens=40k elapsed=6m33s
[11:41:19] FAIL  job=8d45037f reason=sandbox_readonly
```

Format: machine-parseable single line, suitable for later summarization by a `tcd log` command.

**Recommendation S-3: Pre-flight environment check**

Before a fix task starts, the Skill runs a probe job (`touch .tcd_probe && rm .tcd_probe`) to verify write permission; if it fails, report an error rather than wasting a full job.

### 3.3 `tcd log` Command (low priority, long-term direction)

Add a `tcd log [--workflow <id>] [--since <date>]` command to automatically summarize workflows:

```
$ tcd log --since today

WORKFLOW: codex-review-fix-20260305
  11:09:21  ece8b9e3  codex  failed(6m3s)   killed_by_user  tokens=?  [sandbox:readonly]
  11:19:25  9fc1e82d  codex  failed(15m21s) session_disappeared      [codex_autoupdate]
  11:34:46  8d45037f  codex  failed(6m32s)  killed_by_user  tokens=?  [sandbox:readonly]
  11:41:19  8e6c6b37  codex  failed(8m20s)  killed_by_user           [sandbox_bug_M6]
  11:49:39  82705ea4  codex  failed(5m4s)   session_disappeared
  11:54:56  7f9e6ede  codex  failed(4m45s)  session_disappeared
  11:59:58  fc73d94b  codex  running(?)

SUMMARY: 6/7 failed, total ~46min, ~110k tokens (est.)
```

Implementation depends on: T-1 (error classification) + S-2 (event appending).

---

## 4. Automation Plan Draft

### Plan A: Minimal Changes (2–3 days)

Changes to tcd layer only, no Skill changes:

1. T-1: Refine error classification (1h, change 3 kill/refresh call sites)
2. T-2: Write tokens to job.json (2h, change notify_hook.py + job.py)
3. T-3: Write session file path (1h, change codex provider)

Effect: job.json goes from "incomplete metadata" to "trustworthy structured log", without requiring manual supplementation of core metrics.

### Plan B: Skill-Layer Auto Logging (1 week)

Builds on Plan A:

4. S-2: codex-worker Skill auto-appends event lines
5. S-3: Pre-flight write-permission probe

Effect: the timeline section of workflow-log.md can be auto-generated; humans only need to write the decision and analysis sections.

### Plan C: `tcd log` Command (2 weeks)

Builds on Plan B:

6. Add `tcd log` command (requires workflow correlation mechanism, i.e., T-4 or --tag parameter)
7. Add `--tag` parameter to `tcd start` for workflow grouping

Effect: complete workflow visualization; post-mortem analysis requires a single command.

---

## 5. Key Data Reconstruction (Based on Existing Logs)

### Precise Timeline of 7 Jobs

| Job ID | Start Time (UTC) | End Time (UTC) | Duration | Final State | Actual Failure Reason |
|--------|--------------|--------------|------|----------|------------|
| ece8b9e3 | 03:09:21 | 03:19:25 | 10m4s | failed | Sandbox read-only, user kill |
| 9fc1e82d | 03:19:25 | 03:34:46 | 15m21s | failed | Codex auto-update/restart, turn_count=0 |
| 8d45037f | 03:34:46 | 03:41:19 | 6m33s | failed | Sandbox read-only, user kill |
| 8e6c6b37 | 03:41:19 | 03:49:39 | 8m20s | failed | M-6 bug caused sandbox parameter not passed, still read-only |
| 82705ea4 | 03:49:39 | 03:54:43 | 5m4s | failed | turn_count=0, reason unknown (possibly still updating) |
| 7f9e6ede | 03:54:56 | 03:59:41 | 4m45s | failed | turn_count=0, M-6 fixed but still failing |
| fc73d94b | 03:59:58 | running | - | running | First job that successfully began modifying code |

**Note**: There is a 17-second gap from 7f9e6ede to fc73d94b (03:54:41 → 03:59:58), presumably the time taken to regenerate the prompt.

### Diagnostic Significance of turn_count=0

Jobs 9fc1e82d, 82705ea4, and 7f9e6ede all have `turn_count=0` and no `.turn-complete` file, meaning Codex never completed a full turn. This is a more accurate diagnosis than `"killed by user"`: these jobs very likely failed during TUI initialization (Codex auto-update restart, or the sandbox rejected all operations at initialization time).

---

## 6. Conclusion

The logging issues exposed by this workflow can be summarized as a single core tension: **tcd records the job lifecycle, but debugging requires the workflow narrative**.

The bridge between these two currently relies entirely on manual effort (workflow-log.md) and breaks down when the workflow accelerates.

The highest-value changes are T-1 (error classification) and T-2 (token recording) — together about 3–4 hours of work, capable of improving post-mortem analysis quality by over 50% without changing any external interfaces.
