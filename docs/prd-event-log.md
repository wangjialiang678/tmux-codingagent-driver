# PRD: tcd Event Log and Diagnostics System

**Version**: v0.2.0
**Date**: 2026-03-05
**Status**: DONE
**Prerequisite**: v0.1.1 (Codex code review fixes merged, 160 tests pass)

---

## 1. Problem

A 7-round Codex workflow (see docs/workflow-log.md) exposed serious observability gaps:

| Problem | Impact |
|------|------|
| All killed jobs have error "killed by user" | Cannot distinguish sandbox failure, auto-update, or timeout |
| Token consumption not recorded | Code can already parse it but never persists |
| model field is always null | Codex actually used gpt-5.3-codex; tcd never captured it |
| No association between 7 jobs | Cannot tell which is a retry, which depends on which |
| Caller waited 10 minutes with no feedback | `tcd wait` blocks; no intermediate state visible |
| Problem detection requires human eyes | Patterns like sandbox mismatch and stall require manual identification |

**Core insight**: tcd records machine facts (job.json); callers record narrative logs (workflow-log.md); **the middle layer is completely blank** — no structured event stream, no rule-based diagnostics.

---

## 2. Design Principles

- **tcd records facts, makes no decisions** — event log + rule diagnostics, no LLM
- **Callers do semantic understanding and decision-making** — guided by Skills
- **Append-only writes, no modification** — event log is append-only JSONL
- **Zero-config, enabled by default** — no `--verbose` needed; basic events always recorded
- **Backward compatible** — job.json structure unchanged; events go in a new file

---

## 3. Design

### 3.1 Layer 1: Event Log (recorded automatically by tcd)

One `~/.tcd/jobs/<id>.events.jsonl` per job, appended to:

```jsonl
{"ts":"2026-03-05T04:00:00Z","event":"job.created","provider":"codex","sandbox":"workspace-write","cwd":"/path"}
{"ts":"2026-03-05T04:00:01Z","event":"job.tui_ready","elapsed_ms":1200}
{"ts":"2026-03-05T04:00:02Z","event":"job.prompt_sent","bytes":1234,"req_id":"fc73d94b-0-1741147202"}
{"ts":"2026-03-05T04:01:00Z","event":"job.checked","state":"working","pane_lines":45}
{"ts":"2026-03-05T04:03:00Z","event":"job.checked","state":"idle","turn_count":1}
{"ts":"2026-03-05T04:03:01Z","event":"job.turn_complete","turn":0,"method":"signal_file","tokens":{"in":5000,"out":3000}}
{"ts":"2026-03-05T04:05:00Z","event":"job.killed","reason":"user"}
```

**Event types**:

| Event | Trigger | Key Fields |
|------|--------|---------|
| `job.created` | `JobManager.create_job()` | provider, sandbox, cwd, model |
| `job.tui_ready` | `_wait_for_tui()` completes | elapsed_ms, trust_handled |
| `job.tui_timeout` | `_wait_for_tui()` times out | elapsed_ms |
| `job.prompt_sent` | `send_text()` succeeds | bytes, req_id |
| `job.prompt_failed` | `send_text()` fails | error |
| `job.checked` | each poll by `check()` / `wait()` | state, pane_lines |
| `job.turn_complete` | idle/context_limit detected | turn, method, tokens |
| `job.message_sent` | `send()` | bytes, req_id, turn |
| `job.completed` | normal completion | elapsed_s |
| `job.failed` | abnormal termination | error, reason |
| `job.killed` | user kill | reason |

**Implementation**: new `src/tcd/event_log.py`

```python
"""Append-only event log for job lifecycle tracking."""

import json
import time
from pathlib import Path
from tcd.config import JOBS_DIR
from tcd.job import _now_iso


def job_events_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.events.jsonl"


def emit(job_id: str, event: str, **data) -> None:
    """Append a single event to the job's event log."""
    entry = {"ts": _now_iso(), "event": event, **data}
    path = job_events_path(job_id)
    with open(path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

**Instrumentation points** (add `emit()` calls in existing code):

| File | Location | Event |
|------|------|------|
| `cli.py:84` | after `create_job` | `job.created` |
| `cli.py:134` | after TUI ready | `job.tui_ready` / `job.tui_timeout` |
| `cli.py:141` | `send_text` success/failure | `job.prompt_sent` / `job.prompt_failed` |
| `cli.py:240-258` | each `check()` call | `job.checked` |
| `cli.py:360` | `send()` success | `job.message_sent` |
| `cli.py:499` | `_kill_job()` | `job.killed` |
| `sdk.py` | mirrored locations | same events |

### 3.2 Layer 2: Diagnostics Engine (rule-based detection by tcd)

New `src/tcd/diagnostics.py` — pure rule engine, no LLM required:

```python
"""Rule-based diagnostics for job health."""

@dataclass
class Warning:
    code: str
    message: str
    severity: Literal["info", "warn", "error"]


def diagnose(job: Job, pane_tail: str | None = None) -> list[Warning]:
    """Run diagnostic rules against a job's current state."""
    warnings = []

    # R1: Sandbox mismatch
    if job.sandbox in (None, "workspace-write"):
        prompt_lower = job.prompt.lower()
        write_keywords = ["修改", "修复", "fix", "edit", "write", "create", "save"]
        if any(kw in prompt_lower for kw in write_keywords):
            warnings.append(Warning(
                code="SANDBOX_MISMATCH",
                message=f"Prompt contains write intent but sandbox={job.sandbox or 'workspace-write'}",
                severity="warn",
            ))

    # R2: Stall detection
    events = load_events(job.id)
    check_events = [e for e in events if e["event"] == "job.checked"]
    if len(check_events) >= 4:
        recent = check_events[-4:]
        if all(e.get("state") == "working" for e in recent):
            span = _time_diff(recent[0]["ts"], recent[-1]["ts"])
            if span > 60:
                warnings.append(Warning(
                    code="STALL",
                    message=f"No state change in {span:.0f}s ({len(recent)} checks)",
                    severity="warn",
                ))

    # R3: Permission error in pane
    if pane_tail:
        permission_phrases = ["Operation not permitted", "Permission denied", "read-only"]
        for phrase in permission_phrases:
            if phrase in pane_tail:
                warnings.append(Warning(
                    code="PERMISSION_ERROR",
                    message=f"Found '{phrase}' in pane output",
                    severity="error",
                ))
                break

    # R4: Turn-0 stuck
    if job.turn_count == 0 and job.turn_state == "working":
        elapsed = _elapsed_seconds(job)
        if elapsed > 120:
            warnings.append(Warning(
                code="TURN0_STUCK",
                message=f"Still on turn 0 after {elapsed}s",
                severity="warn",
            ))

    return warnings
```

### 3.3 CLI Integration

**Enhanced `tcd check`**:

```bash
# Existing behavior unchanged (exit codes 0/1/2/3)
tcd check <job_id>

# New --json output (includes diagnostics)
tcd check <job_id> --json
```

Output:
```json
{
  "state": "working",
  "elapsed_s": 120,
  "turn_count": 0,
  "warnings": [
    {"code": "SANDBOX_MISMATCH", "severity": "warn", "message": "..."},
    {"code": "TURN0_STUCK", "severity": "warn", "message": "..."}
  ],
  "pane_tail": "... last 5 lines ..."
}
```

**New `tcd log`**:

```bash
# View event log
tcd log <job_id>                    # all events
tcd log <job_id> --tail 10          # last 10 events
tcd log <job_id> --event job.checked # filter by type
```

### 3.4 Skill Integration

Update the `codex-worker` Skill's polling strategy:

```
Each poll uses tcd check <job_id> --json, not bare tcd check.
Parse warnings from JSON:
- SANDBOX_MISMATCH → auto kill + restart with full-auto
- PERMISSION_ERROR → tell user Codex is hitting a permission problem
- STALL → capture pane_tail for semantic analysis, report to user
- TURN0_STUCK → TUI may not have started successfully; suggest attach to inspect
```

---

## 4. Implementation Plan

### Phase 1: Event Log (core)

- [x] Add `src/tcd/event_log.py` (emit + load_events + path)
- [x] Add `job_events_path()` to `config.py`
- [x] Instrument key paths in `cli.py` (8 events)
- [x] Mirror instrumentation in `sdk.py`
- [x] `JobManager._remove_job_files()` cleans up events file
- [x] Add `tcd log` CLI command
- [x] Tests: event write/read/cleanup

### Phase 2: Diagnostics Engine

- [x] Add `src/tcd/diagnostics.py` (4 rules)
- [x] Integrate diagnostics output into `tcd check --json`
- [x] Attach pane_tail (last 5 lines) to `tcd check --json`
- [x] Tests: trigger/no-trigger for each rule

### Phase 3: Skill Update

- [x] Update `codex-worker` Skill to use `tcd check --json`
- [x] Add warnings handling strategy
- [x] Validate in real use

### Phase 4: Token Recording (Codex-specific)

- [x] Parse token_count from Codex NDJSON in `detect_completion()`
- [x] Write to tokens field of `job.turn_complete` event
- [x] Display cumulative tokens in `tcd status --json`

---

## 5. Impact Scope

| File | Change Type |
|------|---------|
| `src/tcd/event_log.py` | **New** |
| `src/tcd/diagnostics.py` | **New** |
| `src/tcd/config.py` | Add `job_events_path()` |
| `src/tcd/cli.py` | Instrumentation + `tcd log` + `tcd check --json` |
| `src/tcd/sdk.py` | Instrumentation |
| `src/tcd/job.py` | Add events cleanup to `_remove_job_files` |
| `~/.claude/skills/codex-worker/SKILL.md` | Update polling strategy |
| `tests/test_event_log.py` | **New** |
| `tests/test_diagnostics.py` | **New** |

**Not changed**: provider code, tmux_adapter, collector, output_cleaner

---

## 6. Non-Goals

- Workflow association (workflow_id) — left to the upper orchestration system
- LLM semantic analysis — handled by the caller's Skill
- Real-time push / WebSocket — tcd is a CLI tool; polling is sufficient
- Log rotation / compression — `tcd clean` already covers the lifecycle
- Modifying job.json structure — event log is stored independently
