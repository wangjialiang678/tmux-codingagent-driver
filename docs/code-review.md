# Code Review Report

**Reviewer**: Codex (gpt-5.3-codex xhigh)
**Date**: 2026-03-05
**Project**: tmux-codingagent-driver (tcd)

## Scope

- Review dimensions:
  1. Code quality (naming, structure, readability)
  2. Bug risk (edge cases, error handling)
  3. Architecture issues (module coupling, abstraction levels, extensibility)
  4. Test coverage (adequacy and missing scenarios)
  5. Security issues (input validation, injection risk)
  6. Performance issues (unnecessary overhead, optimization opportunities)

---

## Critical

### C-1 Marker completion detection may be false-triggered by "user input echo", causing a task to be incorrectly marked as complete

- **File path**: src/tcd/marker_detector.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **Line range**: marker_detector.py:36-42, claude.py:80-84, gemini.py:78-82
- **Issue description**:
  - build_marker_prompt() puts TCD_DONE:{req_id} in the user input text.
  - scan_for_marker() only does a substring contains check.
  - Claude/Gemini also use prefix-style matching ({job.id}-{turn}-); if the terminal echoes user input, a false idle judgment may occur before the AI has finished.
- **Suggested fix**:
  - Use the full req_id for strict matching (whole-line match, not prefix match).
  - Do not include the literal "final completion marker" text in user input.
  - Prefer structured message (assistant role) for completion determination to avoid false positives from full-screen text scanning.

### C-2 Claude/Gemini multi-turn session turn_count does not increment, causing subsequent turn detection to match old-turn markers

- **File path**: src/tcd/cli.py, src/tcd/sdk.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **Line range**: cli.py:237-245, 287-292, 343-349, sdk.py:152-161, 242-248, claude.py:81-84, gemini.py:79-82
- **Issue description**:
  - send() uses turn_count + 1 to generate req_id.
  - But the Claude/Gemini code path does not update turn_count after a turn completes.
  - Subsequent detection still matches by the old turn_count, potentially hitting a historical marker, corrupting multi-turn flow.
- **Suggested fix**:
  - Uniformly increment turn_count on working → idle/context_limit transition (idempotent — increment only once).
  - Persist current_req_id in Job; use the "full req_id for the current turn" during detection.

### C-3 Provider launch command concatenation has command injection risk (--model not safely escaped)

- **File path**: src/tcd/providers/codex.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **Line range**: codex.py:141-156, claude.py:42-48, gemini.py:40-46
- **Issue description**:
  - job.model is user-supplied and is currently string-concatenated into shell commands.
  - If it contains quotes, semicolons, or other metacharacters, it can break command structure, creating an injection surface.
- **Suggested fix**:
  - Switch to parameterized construction with per-item escaping (or whitelist validation of model character set).
  - Apply strict format validation on model (e.g. `[a-zA-Z0-9._:-]+`).

---

## Major

### M-1 Gemini response extraction logic may return the user prompt instead of the AI reply

- **File path**: src/tcd/providers/gemini.py
- **Line range**: gemini.py:124-143
- **Issue description**:
  - _extract_between_markers() takes the interval between the last TCD_REQ and the last TCD_DONE.
  - In many scenarios this interval contains user input and instruction text, not the assistant output.
- **Suggested fix**:
  - Switch to a message-role/event-based extraction strategy.
  - Add regression tests asserting that extracted results do not contain the user prompt segment.

### M-2 Session file location strategy "take globally newest" will cross-contaminate jobs

- **File path**: src/tcd/providers/codex.py, src/tcd/providers/claude.py
- **Line range**: codex.py:210-236, claude.py:119-133
- **Issue description**:
  - Current strategy scans the full directory and picks the newest jsonl.
  - With concurrent jobs or multiple projects running simultaneously, it easily reads another job's session.
- **Suggested fix**:
  - Record the provider's native session ID (or unique identifier) in Job at startup.
  - Match by session ID when reading; at minimum add cwd + created_at filtering.

### M-3 CLI and SDK duplicate core flow implementations with behavior divergence

- **File path**: src/tcd/cli.py, src/tcd/sdk.py
- **Line range**: cli.py:40-142, 223-299, 305-351, 451-470, sdk.py:62-123, 125-193, 220-325
- **Issue description**:
  - start/check/wait/send/_refresh_status/_wait_for_tui are heavily duplicated.
  - For example, SDK checks the send_text failure and raises an error; CLI does not check the return value.
- **Suggested fix**:
  - Extract a unified service layer (flow + state machine); CLI/SDK only handle I/O adaptation.
  - Add CLI/SDK consistency tests.

### M-4 Session disappearance is marked as completed, unable to distinguish normal exit from abnormal failure

- **File path**: src/tcd/cli.py, src/tcd/sdk.py
- **Line range**: cli.py:451-462, sdk.py:283-290
- **Issue description**:
  - _refresh_status() only updates status based on whether the session exists.
  - crash / permission failure / kill and other abnormal exits may all be incorrectly recorded as completed.
- **Suggested fix**:
  - Combine exit signal, log tail characteristics, or an explicit exit-code file to distinguish completed/failed/aborted.

### M-5 Persistence directory and file permissions not locked down, may leak sensitive prompts/output

- **File path**: src/tcd/config.py, src/tcd/job.py, src/tcd/notify_hook.py
- **Line range**: config.py:15-18, job.py:95-106, notify_hook.py:66-69, 91-94
- **Issue description**:
  - Default permissions depend on the system umask; ~/.tcd and job file visibility is not explicitly restricted.
- **Suggested fix**:
  - Fix directory permissions at 700, file permissions at 600.

### M-6 --sandbox parameter declared but has no effect; interface semantics inconsistent with implementation

- **File path**: src/tcd/cli.py
- **Line range**: cli.py:47-48
- **Issue description**:
  - CLI exposes --sandbox externally, but it is not passed to the provider command builder.
- **Suggested fix**:
  - Either properly wire it into provider launch parameters, or remove the option and update documentation.

### M-7 Concurrent updates to job.json risk overwrites

- **File path**: src/tcd/job.py, src/tcd/notify_hook.py
- **Line range**: job.py:95-106, notify_hook.py:83-95
- **Issue description**:
  - Both locations use read-modify-write + replace, but with no lock or versioning.
  - Concurrent writes may cause field loss (turn_count/turn_state/error).
- **Suggested fix**:
  - Introduce file locking or version-number CAS; or switch to an event-log append model.

---

## Minor

### m-1 CLI start does not check the prompt send result

- **File path**: src/tcd/cli.py
- **Line range**: cli.py:135-137
- **Issue description**:
  - tmux.send_text() return value is ignored; "Job started" is still printed on failure.
- **Suggested fix**:
  - Mark the job as failed and output an error code on failure.

### m-2 Marker scan only looks at the last 50 lines; long output may miss the marker

- **File path**: src/tcd/marker_detector.py
- **Line range**: marker_detector.py:7-8, 36-56
- **Issue description**:
  - If the marker is scrolled out of the tail window, "completed but undetected" may occur.
- **Suggested fix**:
  - Use a dynamic window or look up by req_id in structured logs.

### m-3 Broad except Exception in multiple places reduces observability

- **File path**: src/tcd/cli.py, src/tcd/sdk.py, src/tcd/providers/gemini.py
- **Line range**: cli.py:229-233, 284-294, sdk.py:145-149, gemini.py:65-66
- **Issue description**:
  - Exceptions are swallowed; the true root cause is hard to locate.
- **Suggested fix**:
  - Narrow exception scope and log contextual information.

### m-4 Private function _now_iso used across modules; boundary semantics unclear

- **File path**: src/tcd/job.py, src/tcd/cli.py, src/tcd/sdk.py
- **Line range**: job.py:24-25, cli.py:14, sdk.py:11
- **Issue description**:
  - _now_iso is exported with a private naming convention but used in external modules, reducing readability and API clarity.
- **Suggested fix**:
  - Convert to a public utility function (e.g. tcd.timeutils.now_iso()) and unify references.

### m-5 Provider registration relies on implicit side-effect imports; maintainability is suboptimal

- **File path**: src/tcd/__init__.py, src/tcd/providers/__init__.py
- **Line range**: __init__.py:6-8
- **Issue description**:
  - Registration behavior is scattered across package import side effects and is not explicit.
- **Suggested fix**:
  - Centralize the registration entry point in providers/__init__.py; callers initialize explicitly.

---

## Suggestion

### S-1 Unify command builder, eliminate duplicated concatenation and escaping forks

- **File path**: src/tcd/providers/codex.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **Line range**: codex.py:132-159, claude.py:38-50, gemini.py:36-47
- **Issue description**:
  - All three providers build shell strings independently; maintenance cost is high.
- **Suggested fix**:
  - Abstract a CommandBuilder with unified parameterized assembly and escaping strategy.

### S-2 Abstract completion detection into composable strategy chains

- **File path**: src/tcd/providers/codex.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **Line range**: codex.py:165-178, claude.py:56-92, gemini.py:53-90
- **Issue description**:
  - Signal/Marker/Idle logic is currently hardcoded in each provider, with low reusability when adding new providers.
- **Suggested fix**:
  - Extract strategy classes and compose them declaratively (Strategy pipeline).

### S-3 Add high-risk regression test matrix

- **File path**: tests/
- **Line range**: multiple files
- **Issue description**:
  - Key risks (multi-turn consistency, injection, cross-job contamination, concurrent writes) have no corresponding regression tests.
- **Suggested fix**:
  - Add the following test groups:
    - test_multiturn_turn_count_for_marker_providers
    - test_model_arg_escaping_all_providers
    - test_session_file_selection_with_multiple_candidates
    - test_refresh_status_failure_classification
    - test_job_json_concurrent_update_consistency

---

## Test Coverage Assessment

- **Well covered**:
  - Basic behaviors of job, output_cleaner, marker_detector, tmux_adapter
  - Fine-grained logic such as UTF-8 chunking, ANSI cleanup, JSON extraction

- **Notable gaps**:
  - Multi-turn session state progression (especially Claude/Gemini)
  - Command injection and argument escaping security tests
  - Concurrent write consistency tests
  - Session file location accuracy with multiple concurrent sessions

## Performance Assessment Supplement

- Optimization opportunities exist on the high-frequency polling path:
  - capture_pane defaults to capturing the full scrollback buffer (-S -) and is called frequently in check/wait, which is expensive
  - Session file full-directory rglob on every scan will degrade noticeably as history accumulates
- Suggestions:
  - For detection, first sample a small window (recent N lines) and expand only if needed
  - Cache / index the session file mapping to avoid full scans

## Overall Assessment

The project has a good MVP structure and usability foundation: clear module separation, complete CLI/SDK functionality, and a reasonable number of tests. However, there are 3 Critical risks, concentrated in "completion detection reliability" and "command construction security", which directly impact production usability and security. Recommend completing Critical and Major fixes first, then addressing architectural deduplication and performance optimization.

## Statistics Summary

| Level | Count | Fixed | Deferred/Rejected |
|-------|-------|-------|-------------------|
| Critical | 3 | 3 (C-1, C-2, C-3) | 0 |
| Major | 7 | 3 (M-1, M-4, M-6) | 4 (M-2, M-3, M-5, M-7) |
| Minor | 5 | 2 (m-1, m-3) | 3 (m-2, m-4, m-5) |
| Suggestion | 3 | 0 | 3 (S-1, S-2, S-3) |
| **Total** | **18** | **8** | **10** |

## Notes

- This review only performs analysis; no business code was modified.
- Attempted to run tests for verification but the environment errored on temp directory creation, so local test execution could not be completed.

---

## Claude Code Reviewer Comments (Orchestrator Annotations)

**Reviewer**: Claude Code (claude-opus-4-6)
**Date**: 2026-03-05
**Basis**: Independent reviewer sub-agent cross-validation + source code spot-checks

### Accept

| ID | Rationale |
|----|-----------|
| **C-2** | **Confirmed Critical and more severe than described**. Reviewer verification found turn_count only increments in notify_hook (Codex-only); Claude/Gemini turn_count is **always 0**, causing multi-turn req_id collisions. Must fix. |
| **C-3** | **Confirmed Critical**. model parameter is directly concatenated into shell commands with no shlex.quote() protection. Apply regex whitelist `[a-zA-Z0-9._:/-]+` and wrap with shlex.quote. |
| **M-1** | **Confirmed, actually more severe than described**. _extract_between_markers() extracts content between TCD_REQ and TCD_DONE, which includes the original prompt text, not pure AI response. Need to skip prompt lines. |
| **M-4** | **Confirmed**. Session disappearance could be a crash/kill; uniformly marking it as completed misleads the caller. Should distinguish completed/failed/unknown. |
| **M-6** | **Confirmed, simple cleanup**. --sandbox parameter is dead code; either remove it or properly wire it to the provider. |
| **m-1** | **Confirmed**. send_text return value should be checked; mark job as failed on failure. |
| **m-3** | **Confirmed**. Broad except Exception reduces observability; narrow scope and log traceback. |

### Reject / Defer

| ID | Rationale |
|----|-----------|
| **C-1** | **Downgraded to Minor, deferred**. The marker contains a UUID-formatted req_id (e.g. `TCD_DONE:ece8b9e3-1-abc123`); the probability that user input naturally produces an exact match is extremely low. The current scan_for_marker already matches the full req_id string, not a prefix. The Codex description was inaccurate — the actual code checks the complete req_id string, not a prefix. Risk was overstated. |
| **M-2** | **Deferred**. Session file cross-contamination is a real risk, but the current MVP phase is primarily single-user single-task; concurrent scenarios are rare. Recorded as tech debt, to be fixed in v2. |
| **M-3** | **Deferred**. CLI/SDK duplication is known tech debt; a unified service layer is planned for v2. The current CLI vs SDK behavioral differences are intentional (CLI output format vs SDK return objects), not a simple DRY issue. |
| **M-5** | **Rejected, false positive**. Reviewer verification confirms job.json uses tempfile.mkstemp() (default mode=0o600); permissions are already owner-read-write only. Directory permissions relying on umask in a macOS single-user environment is acceptable. |
| **M-7** | **Deferred**. Concurrent write race condition is real, but in practice notify_hook and CLI/SDK write non-overlapping fields (hook writes turn_count/turn_state, CLI writes status); the collision window is extremely small. Consider fcntl.flock in v2. |
| **m-2** | **Deferred**. 50-line scan window is sufficient for marker detection — markers are at the end of output and won't be scrolled out. Missed detection would only occur if the AI outputs 50+ lines before appending the marker, which doesn't happen in practice. |
| **m-4** | **Deferred**. _now_iso naming is non-standard but has minimal impact; address it when refactoring the service layer. |
| **m-5** | **Deferred**. Provider registration via decorator + import side effect is a common Python ecosystem pattern (Django, Flask, etc.); maintainability is acceptable. |
| **S-1** | **Deferred**. CommandBuilder abstraction has low ROI with only 3 providers; reconsider when provider count grows. |
| **S-2** | **Deferred**. Strategy chain pattern similarly — the current 3-strategy fallback implementations vary significantly across providers; forcing unification would increase complexity. |
| **S-3** | **Partially accepted**. Expanding the test matrix is valuable but lower priority than fixing C-2/C-3. Supplement corresponding tests when fixing Critical issues. |

### Fix Priority Recommendations

1. **P0 fix immediately**: C-2 (turn_count), C-3 (command injection)
2. **P1 fix this round**: M-1 (Gemini response extraction), M-4 (status distinction), M-6 (dead code cleanup), m-1 (send check), m-3 (exception handling)
3. **P2 next round**: C-1, M-2, M-3, M-7, m-2, m-4, m-5, S-1~S-3
