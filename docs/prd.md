# PRD: tmux-codingagent-driver (tcd)

**Version**: v0.1.0
**Date**: 2026-03-02
**Status**: IMPLEMENTED (Phases 1–4 fully complete, 119 tests pass)
**Author**: Michael

---

## 1. Overview

tmux-codingagent-driver (abbreviated **tcd**) is a programming task dispatcher that drives multiple AI CLI tools through tmux. It lets an upstream agent (Claude Code, OpenClaw, or a shell script) launch, monitor, and collect results from downstream AI CLIs (Codex, Claude Code, Gemini CLI), enabling parallel multi-AI programming.

**One sentence**: tmux is the bus, AI CLIs are the workers, tcd is the scheduler.

---

## 2. Background and Motivation

### 2.1 The Problem

Modern AI programming assistants (Claude Code, Codex, Gemini CLI) each have their strengths, but they are all standalone CLI tools with no standard cross-tool orchestration solution. Developers face:

- **Single-agent bottleneck**: one Claude Code session can only do one thing at a time; multiple AIs cannot be used in parallel
- **Manual switching cost**: manually copy-pasting context between different AI tools, switching terminal windows
- **High API costs**: API-based orchestration requires sending the full context every time (thousands of tokens), while CLI tools maintain their own sessions and only need new instructions (50–200 tokens)

### 2.2 Limitations of Existing Solutions

| Solution | Limitations |
|----------|------------|
| **claude_code_bridge (CCB)** | Architecture too heavy (daemon/TCP/worker pool), tightly coupled, hard to use independently |
| **codex-orchestrator** | Codex-only, TypeScript implementation, cannot drive Claude/Gemini |
| **Direct API usage** | Full context re-sent every call, severe token waste; cannot leverage CLI's file operation capabilities |
| **Manual multi-terminal** | Not programmable, cannot be automated, high manual cost |

### 2.3 Core Insight

AI CLI tools (codex, claude, gemini) self-maintain complete conversation history when running in a terminal. Injecting text into the terminal via tmux = sending a request; reading terminal output/logs = receiving a response. **Each call only needs to send new instructions (50–200 tokens); the AI CLI manages context itself**.

---

## 3. Target Users and Environment

### 3.1 User Persona

- Individual developers using Claude Code / Codex / Gemini CLI for daily programming
- Familiar with tmux
- Want one AI agent to orchestrate multiple AI tools working in parallel

### 3.2 Environment Constraints

| Item | Requirement |
|------|-------------|
| OS | macOS (primary), Linux (compatible) |
| Terminal multiplexer | tmux (must be installed) |
| Python | 3.10+ |
| AI CLI | At least one installed: `codex` / `claude` / `gemini` |
| Network | Each AI CLI requires a valid API key / authenticated session |

---

## 4. Goals and Non-Goals

### 4.1 Goals

- **G1**: Provide a unified CLI interface to drive Codex, Claude Code, and Gemini CLI
- **G2**: Support parallel launching of multiple AI tasks without interference
- **G3**: Reliably detect task completion and collect results
- **G4**: Upstream agents (Claude Code / OpenClaw) can call via bash commands or Python SDK
- **G5**: Support sending follow-up instructions to running AIs (multi-turn conversation)
- **G6**: Personal-tool-level documentation, testing, and CI

### 4.2 Non-Goals

- **NG1**: Not an API proxy — tcd only drives CLIs, not a replacement for API calls
- **NG2**: No GUI — pure CLI + SDK
- **NG3**: No cross-machine distribution — local tmux only
- **NG4**: No AI decision-making — tcd only handles scheduling execution, not deciding "which AI to use"
- **NG5**: No Windows support (MVP phase)
- **NG6**: No community open-source operations (no contribution guidelines)

---

## 5. Core Concepts

| Concept | Definition |
|---------|-----------|
| **Provider** | An adapter for one AI CLI (e.g. CodexProvider, ClaudeProvider, GeminiProvider), encapsulating differences in launch parameters, completion detection, and response parsing |
| **Job** | The full lifecycle record of a programming task, from creation to completion, persisted as a JSON file |
| **Turn** | One execution round of the AI CLI. A job may include multiple turns (user appends instructions) |
| **Signal File** | A signal file (`.turn-complete`) indicating one turn has ended, used for low-cost completion detection |
| **Marker** | A completion marker injected into the prompt (e.g. `TCD_DONE:{req_id}`), requiring the AI to output it at the end of its reply |
| **Notify Hook** | A callback mechanism natively supported by some AI CLIs (currently only Codex), automatically executing an external script when a turn ends |
| **Session** | The conversation context maintained by the AI CLI, stored in each tool's own log directory |
| **tmux Session** | A terminal session created by tmux; each job corresponds to one independent tmux session |

---

## 6. System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Upstream Callers                           │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐   │
│  │Claude    │  │OpenClaw  │  │Shell     │  │Python Script  │   │
│  │Code      │  │Agent     │  │Script    │  │(SDK import)   │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬────────┘   │
│       │              │             │                │             │
│       └──────────────┼─────────────┼────────────────┘             │
│                      │  tcd CLI / Python SDK                      │
└──────────────────────┼────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                   tmux-codingagent-driver (tcd)                   │
│                                                                  │
│  ┌────────────┐                                                  │
│  │  CLI       │  tcd start / send / status / output / check     │
│  │  (cli.py)  │  / wait / jobs / attach / kill / clean          │
│  └─────┬──────┘                                                  │
│        │                                                         │
│  ┌─────▼──────┐  ┌──────────────┐  ┌────────────────┐           │
│  │ Job Manager│  │Provider      │  │Response        │           │
│  │ (job.py)   │  │Registry      │  │Collector       │           │
│  │            │  │(provider.py) │  │(collector.py)  │           │
│  │ - create   │  │              │  │                │           │
│  │ - status   │  │ - codex      │  │ - session file │           │
│  │ - update   │  │ - claude     │  │ - capture-pane │           │
│  │ - cleanup  │  │ - gemini     │  │ - log file     │           │
│  └─────┬──────┘  └──────┬───────┘  └───────┬────────┘           │
│        │                │                   │                    │
│  ┌─────▼────────────────▼───────────────────▼──────┐             │
│  │              tmux Adapter (tmux_adapter.py)      │             │
│  │                                                  │             │
│  │  create_session()   send_keys()                  │             │
│  │  session_exists()   send_long_text()             │             │
│  │  capture_pane()     kill_session()               │             │
│  └─────────────────────┬────────────────────────────┘             │
└────────────────────────┼─────────────────────────────────────────┘
                         │
           ┌─────────────┼─────────────┐
           ▼             ▼             ▼
      ┌─────────┐  ┌─────────┐  ┌─────────┐
      │ tmux    │  │ tmux    │  │ tmux    │
      │ session │  │ session │  │ session │
      │         │  │         │  │         │
      │ codex   │  │ claude  │  │ gemini  │
      │ CLI     │  │ CLI     │  │ CLI     │
      └─────────┘  └─────────┘  └─────────┘
```

### Data Flow

```
Start:   tcd start → Job Manager creates Job JSON → Provider builds command
         → tmux Adapter creates session → injects prompt → AI starts execution

Detect:  tcd check → check signal file (priority)
         → Provider.detect_completion() (marker/idle) → return state

Collect: tcd output → Response Collector → session file / capture-pane / log file
         → clean ANSI → return clean text
```

### Filesystem Layout

```
~/.tcd/                           # tcd runtime data root
├── jobs/
│   ├── {id}.json                 # Job metadata
│   ├── {id}.log                  # script-recorded terminal log
│   ├── {id}.prompt               # raw prompt file
│   └── {id}.turn-complete        # turn completion signal file
└── config.toml                   # optional global config
```

---

## 7. Functional Requirements

### FR-1: Provider System

**Description**: Pluggable AI CLI adapters. Each provider encapsulates one AI CLI's launch, communication, and result parsing logic.

**Unified Interface**:

| Method | Responsibility |
|--------|---------------|
| `build_launch_command(job)` | Build the shell command string to launch the AI CLI |
| `build_prompt_wrapper(message, req_id)` | Wrap the prompt (add markers, etc.) |
| `detect_completion(job)` | Detect whether the turn is complete |
| `parse_response(job)` | Parse the AI response from session file/logs |
| `get_session_log_path(job)` | Return the AI's native session file path |

**Acceptance Criteria**:
- [ ] Adding a new provider only requires implementing the above 5 methods, < 150 lines of code
- [ ] Providers are registered and looked up by name string (`get_provider("codex")`)

---

### FR-2: tmux Adapter

**Description**: Python wrapper for tmux operation primitives, isolating all tmux command calls.

**Core Operations**:

| Operation | tmux Command | Description |
|-----------|-------------|-------------|
| `create_session(name, cmd, cwd)` | `tmux new-session -d -s {name} -c {cwd} '{cmd}'` | Create detached session |
| `session_exists(name)` | `tmux has-session -t {name}` | Check if session exists |
| `send_keys(session, text)` | `tmux send-keys -t {session} '{text}' Enter` | Short text injection (< 5000 chars) |
| `send_long_text(session, text)` | `tmux load-buffer {file}` + `tmux paste-buffer -t {session}` + `send-keys Enter` | Long text injection (≥ 5000 chars) |
| `capture_pane(session, lines)` | `tmux capture-pane -t {session} -p [-S -]` | Read terminal content |
| `kill_session(session)` | `tmux kill-session -t {session}` | Destroy session |

**Key Design Decisions**:
- Short text threshold: 5000 characters (from codex-orchestrator empirical value)
- Single-quote escaping: `text.replace("'", "'\\''")`
- All subprocess calls timeout = 10s

**Acceptance Criteria**:
- [ ] After creating a session, `session_exists()` returns True
- [ ] Text over 5000 characters is correctly injected via `send_long_text`
- [ ] After `kill_session`, `session_exists()` returns False
- [ ] Clear error message when tmux is not installed

---

### FR-3: Job Management

**Description**: Full lifecycle management for tasks, persisted as JSON files.

**Job State Machine**:

```
                 ┌──────────┐
                 │ pending  │
                 └────┬─────┘
                      │ create_session success
                      ▼
                 ┌──────────┐
          ┌─────►│ running  │◄────┐
          │      └──┬───┬───┘     │
          │         │   │         │ send (append instruction)
          │         │   └─────────┘
          │         │
          │    ┌────┴────┐
          │    ▼         ▼
     ┌─────────┐   ┌─────────┐
     │completed│   │ failed  │
     └─────────┘   └─────────┘
```

**Job Data Structure**:

```python
@dataclass
class Job:
    id: str                           # 8-byte hex (e.g. "a3f2b1c9")
    provider: str                     # "codex" | "claude" | "gemini"
    status: Literal["pending", "running", "completed", "failed"]
    prompt: str                       # raw prompt
    cwd: str                          # working directory
    tmux_session: str                 # "tcd-{provider}-{id}"
    model: str | None                 # optional model override
    created_at: str                   # ISO 8601
    started_at: str | None
    completed_at: str | None
    result: str | None                # final output
    error: str | None                 # error message
    turn_count: int                   # turn counter
    turn_state: Literal["working", "idle", "context_limit"] | None
    last_agent_message: str | None    # most recent AI message (truncated to 500 chars)
    timeout_minutes: int              # timeout in minutes, default 60
```

**Acceptance Criteria**:
- [ ] Job JSON written atomically (write temp file → rename)
- [ ] `tcd jobs` lists all jobs with id / provider / status / age
- [ ] `tcd clean` removes completed/failed jobs and associated files (.log / .prompt / .turn-complete)
- [ ] Timed-out jobs automatically marked as failed

---

### FR-4: Completion Detection

**Description**: Three-layer strategy to detect whether an AI turn is complete, with priority-based fallback.

**Strategy Priority**:

```
1. Signal File  →  check whether ~/.tcd/jobs/{id}.turn-complete exists
                   (written by notify-hook or marker scanner)
   │
   │ not found
   ▼
2. Marker scan  →  capture-pane / read log, scan for TCD_DONE:{req_id}
   │
   │ not found
   ▼
3. Idle detect  →  N seconds of consecutive capture-pane with no change → treat as complete
   │
   │ still changing
   ▼
4. Return "working"
```

**Detection Strategy per Provider**:

| Provider | Strategy 1 (Signal File) | Strategy 2 (Marker) | Strategy 3 (Idle) |
|----------|--------------------------|--------------------|--------------------|
| Codex | notify-hook writes (native) | not needed | not needed |
| Claude Code | marker scanner writes | TCD_DONE scan | 20s idle |
| Gemini CLI | marker scanner writes | TCD_DONE scan | 15s idle |

**Marker Protocol Format**:

```
TCD_REQ:{req_id}
{user's actual message}
After completing your reply, output on the last line: TCD_DONE:{req_id}
```

- `req_id` format: `{job_id}-{turn_count}-{timestamp}`
- Scan backward from end of output (avoid reading the full text)
- Tolerates trailing blank lines and noise

**Acceptance Criteria**:
- [ ] Codex job detected as complete within < 1s after turn ends via notify-hook
- [ ] Claude/Gemini job detected as complete within < 5s after turn ends via marker scan
- [ ] Gemini idle detection falls back successfully after 15s when no marker is output
- [ ] `tcd check {id}` exit codes: 0=idle, 1=working, 2=context_limit

---

### FR-5: Response Collection

**Description**: Collect AI responses from multiple data sources with multi-strategy fallback.

**Collection Priority**:

```
1. Provider-specific session file (structured data, e.g. Codex JSONL)
2. tmux capture-pane (when session is still alive)
3. script log file (fallback when session has exited)
```

**ANSI Cleanup**:
- Remove all ANSI CSI / OSC / DCS / ESC sequences
- Remove AI CLI TUI noise lines (status bars, progress bars, update prompts)
- Remove duplicate lines
- Remove the markers themselves (TCD_REQ / TCD_DONE)

**Acceptance Criteria**:
- [ ] Completed Codex job parses out token usage + modified file list + summary
- [ ] Completed Claude job extracts response text from JSONL
- [ ] Output still readable from .log file after tmux session is killed
- [ ] Output contains no ANSI escape sequences or TUI noise

---

### FR-6: CLI Interface

**Description**: The `tcd` command family — all sub-commands.

#### tcd start

Start a new job.

```bash
tcd start --provider <name> --prompt <text> [options]
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--provider` / `-p` | yes | — | codex / claude / gemini |
| `--prompt` / `-m` | yes | — | task description (also supports reading from stdin) |
| `--cwd` / `-d` | no | `.` | working directory |
| `--model` | no | provider default | model name override |
| `--timeout` | no | 60 | timeout in minutes |
| `--sandbox` | no | provider default | Codex sandbox mode |

**Output**:

```
Job started: a3f2b1c9
Provider: codex
tmux session: tcd-codex-a3f2b1c9
```

#### tcd send

Send a follow-up instruction to a running job.

```bash
tcd send <job-id> <message>
tcd send <job-id> --file <path>    # read long message from file
```

#### tcd status

View job status.

```bash
tcd status <job-id>              # human-readable
tcd status <job-id> --json       # JSON (for agent parsing)
```

**JSON output example**:

```json
{
  "id": "a3f2b1c9",
  "provider": "codex",
  "status": "running",
  "turn_state": "idle",
  "turn_count": 1,
  "last_agent_message": "I've implemented the user registration...",
  "elapsed_seconds": 45
}
```

#### tcd output

Get job output.

```bash
tcd output <job-id>              # final response (cleaned)
tcd output <job-id> --full       # full scrollback (includes TUI output)
tcd output <job-id> --raw        # raw log (includes ANSI)
```

#### tcd check

Non-blocking completion detection (designed for upstream agent polling).

```bash
tcd check <job-id>
# exit 0 → idle (turn complete, can send new instruction or read result)
# exit 1 → working (still executing)
# exit 2 → context_limit (context exhausted)
# exit 3 → not_found (job does not exist)
```

#### tcd wait

Blocking wait for job completion.

```bash
tcd wait <job-id> --timeout 300   # wait up to 300 seconds
# exit 0 → completed
# exit 1 → failed
# exit 2 → timeout
```

#### tcd jobs

List all jobs.

```bash
tcd jobs                         # list all
tcd jobs --status running        # filter by status
tcd jobs --json                  # JSON format
```

#### tcd attach

Attach to the job's tmux session (for debugging).

```bash
tcd attach <job-id>              # tmux attach-session -t tcd-codex-a3f2b1c9
```

#### tcd kill

Terminate a job.

```bash
tcd kill <job-id>                # kill session + mark as failed
tcd kill --all                   # kill all running jobs
```

#### tcd clean

Clean up completed jobs.

```bash
tcd clean                        # clean completed + failed
tcd clean --all                  # clean all (including running)
tcd clean --before 7d            # clean jobs older than 7 days
```

**Acceptance Criteria**:
- [ ] All sub-commands' `--help` output is clear
- [ ] `--json` output is parseable by `jq`
- [ ] Invalid job-id gives a friendly error rather than a traceback
- [ ] Supports stdin prompt: `echo "fix bug" | tcd start -p codex -m -`

---

### FR-7: Python SDK

**Description**: Programmable call interface, supports import usage.

```python
from tcd import TCD

driver = TCD()

# Start a job
job = driver.start(provider="codex", prompt="implement user registration", cwd="/path/to/project")

# Non-blocking detection
result = driver.check(job.id)   # -> CheckResult(state="working" | "idle" | "context_limit")

# Blocking wait
driver.wait(job.id, timeout=300)

# Get output
output = driver.output(job.id)  # -> str

# Send follow-up instruction
driver.send(job.id, "add unit tests")

# List jobs
jobs = driver.jobs(status="running")  # -> list[Job]

# Clean up
driver.kill(job.id)
driver.clean()
```

**Acceptance Criteria**:
- [ ] `from tcd import TCD` imports without error
- [ ] SDK interface has 1:1 correspondence with CLI functionality
- [ ] All methods have type hints

---

### FR-8: Upstream Integration Interface

**Description**: Enable Claude Code / OpenClaw to conveniently use tcd.

#### 8a. CLAUDE.md Skill Integration

Provide a standard CLAUDE.md snippet so Claude Code knows how to use tcd:

```markdown
## Available tools: tcd (AI task dispatch)

Use `tcd` commands to dispatch subtasks to other AI CLI tools.

### Start a task
tcd start -p <provider> -m "<prompt>" -d <cwd>
- provider: codex (strong at code implementation) / claude (strong at analysis and docs) / gemini (strong at frontend and review)
- Returns Job ID

### Check completion
tcd check <job-id>
- exit 0 = complete (can get result)
- exit 1 = in progress (keep waiting)

### Get result
tcd output <job-id>

### Send follow-up instruction
tcd send <job-id> "<message>"

### Important rules
- After start, poll with check at 3–5 second intervals
- Do not use sleep for long waits
- Get output immediately after detecting completion
- Do not start a new job for the same provider before the current job completes (prevent resource contention)
```

#### 8b. MCP Server (reserved for Phase 4)

Future wrapper as MCP server providing tools:
- `tcd_start(provider, prompt, cwd)` → job_id
- `tcd_check(job_id)` → state
- `tcd_output(job_id)` → text
- `tcd_send(job_id, message)` → ok

**Acceptance Criteria**:
- [ ] CLAUDE.md snippet can be used as-is
- [ ] Claude Code successfully calls tcd via the bash tool to complete a task

---

## 8. Provider Detailed Specifications

### 8.1 Codex Provider

| Item | Value |
|------|-------|
| CLI command | `codex` |
| Default model | CLI default (not overridden) |
| Permission mode | `-a never` (auto-approve all operations) |
| Sandbox | Configurable: `read-only` / `workspace-write` / `danger-full-access` |
| Completion detection | notify-hook (native support) |
| Session file | `~/.codex/sessions/*.jsonl` |

**Launch command template**:

```bash
script -q "{log_file}" codex \
  -c 'notify=["python3", "{tcd_notify_hook}", "{job_id}"]' \
  -a never \
  -s {sandbox} \
  [-c 'model="{model}"'] \
  [-c 'model_reasoning_effort="{effort}"'] \
  ; echo "\n\n[tcd: session complete]"; read
```

**Notes**:
- macOS and Linux have different `script` command argument ordering; platform detection needed
- Sleep 1s after launch to wait for TUI initialization
- May need to skip update prompt (send-keys "3" + Enter)
- Sleep 0.3s after send-keys to wait for TUI processing

**Notify Hook Workflow**:

```
Codex agent turn ends
  → calls tcd-notify-hook {job_id} (JSON payload via argv)
  → parses payload, checks type == "agent-turn-complete"
  → writes to ~/.tcd/jobs/{job_id}.turn-complete (JSON: turnId, lastAgentMessage, timestamp)
  → updates job.json: turn_count, turn_state, last_agent_message
```

**Session File Parsing**:

Parse JSONL from `~/.codex/sessions/` to get:
- Token usage (input / output / context_window)
- Modified file list (`apply_patch` tool calls)
- Task summary (last agent_message)

---

### 8.2 Claude Code Provider

| Item | Value |
|------|-------|
| CLI command | `claude` |
| Permission mode | `--dangerously-skip-permissions` |
| Completion detection | TCD_DONE marker + idle detection (20s) |
| Session file | `~/.claude/projects/{key}/*.jsonl` |

**Launch command template**:

```bash
script -q "{log_file}" claude \
  --dangerously-skip-permissions \
  [-m "{model}"] \
  ; echo "\n\n[tcd: session complete]"; read
```

**Prompt wrapping**:

```
TCD_REQ:{req_id}
{user message}
After completing your reply, output on the last line: TCD_DONE:{req_id}
```

**Session file discovery**:

Claude Code's session files are under `~/.claude/projects/` with a project hash in the path. Steps:
1. Get from tmux session environment variable (if available)
2. Scan for the newest session file by mtime
3. Match session files created after job creation time

**Notes**:
- Claude Code's `--dangerously-skip-permissions` skips all permission checks
- Claude Code may spawn sub-agents; need to wait for all sub-agents to complete
- Idle detection threshold set to 20s (Claude takes longer to think)

---

### 8.3 Gemini CLI Provider

| Item | Value |
|------|-------|
| CLI command | `gemini` |
| Completion detection | TCD_DONE marker + idle detection (15s) |
| Session file | No standard location; relies on capture-pane |

**Launch command template**:

```bash
script -q "{log_file}" gemini \
  ; echo "\n\n[tcd: session complete]"; read
```

**Notes**:
- Gemini CLI often doesn't follow the marker output requirement; idle detection is the primary dependency
- Gemini 0.29+ session discovery has a dual hash issue (see CCB experience)
- Idle detection threshold 15s (Gemini typically responds faster)

---

## 9. Non-Functional Requirements

### NFR-1: Performance

| Metric | Target |
|--------|--------|
| Job launch (from command to AI starting to receive prompt) | < 3s |
| Completion detection latency (AI completes to tcd sensing) | < 3s (notify-hook) / < 20s (idle detection) |
| `tcd check` execution time | < 0.5s |
| `tcd status --json` execution time | < 0.5s |
| Parallel jobs | At least 5 without mutual interference |

### NFR-2: Reliability

| Mechanism | Description |
|-----------|-------------|
| Log persistence | `script -q` recording; still readable after tmux session exits |
| Timeout fallback | Default 60 minutes of inactivity → auto kill + mark as failed |
| Atomic write | Job JSON via tmpfile + rename to prevent corruption from interrupted writes |
| Session liveness check | `tmux has-session` check; mark as completed when session exits |
| Process isolation | Each job in an independent tmux session; no mutual interference |

### NFR-3: Maintainability

| Metric | Target |
|--------|--------|
| Add new provider | < 150 lines of code |
| Test coverage | Core modules > 80% |
| Documentation | README + PRD + scenarios doc + CLI help |

### NFR-4: Logging and Debugging

| Feature | Description |
|---------|-------------|
| `tcd attach` | Attach directly to tmux session to see real-time output |
| `tcd output --raw` | View raw log (includes ANSI) |
| Job JSON | Complete state snapshot, viewable directly with `cat` / `jq` |
| tcd own log | `~/.tcd/tcd.log`; DEBUG level can be enabled |

---

## 10. Error Handling and Edge Cases

### 10.1 tmux Session Exits Unexpectedly

**Trigger**: AI CLI crash, OOM, user manually kills
**Detection**: `session_exists()` returns False
**Handling**:
1. Read `.log` file to get last output
2. Mark job as `completed` (if "session complete" marker present) or `failed`
3. Retain `.log` file for post-analysis

### 10.2 AI CLI Timeout / Hang

**Trigger**: AI enters infinite loop, network disconnected, API rate-limited
**Detection**: `.log` file mtime exceeds timeout minutes without update
**Handling**:
1. Kill tmux session
2. Mark job as `failed`, error = "timeout after {N} minutes"

### 10.3 Concurrency Conflict

**Trigger**: Multiple jobs started in the same project directory simultaneously
**Handling**: Allow (each job has an independent tmux session), but emit warning:
```
Warning: Another job (xxx) is already running in the same directory.
```

### 10.4 Long Prompt Handling

**Trigger**: prompt > 5000 characters
**Handling**: Automatically switch to `send_long_text` (load-buffer + paste-buffer)

### 10.5 AI Doesn't Follow Marker

**Trigger**: Claude/Gemini ignores the TCD_DONE output requirement
**Handling**: Idle detection automatically fallbacks (15–20s no new output → treat as complete)

### 10.6 tmux Not Installed

**Trigger**: System has no tmux
**Handling**: `tcd` checks on startup and provides installation guidance:
```
Error: tmux not found. Install with: brew install tmux (macOS) or apt install tmux (Linux)
```

### 10.7 AI CLI Not Installed

**Trigger**: Specified provider CLI not in PATH
**Handling**: `tcd start` checks and provides a message:
```
Error: codex not found in PATH. Install from: https://github.com/openai/codex
```

---

## 11. MVP Phased Plan

### Phase 1: Core Framework + Codex Driver

**Goal**: Be able to launch Codex with `tcd start -p codex` and get results

Deliverables:
- [x] tmux_adapter.py — tmux operation wrapper
- [x] provider.py — Provider abstract base class + registry
- [x] providers/codex.py — Codex Provider implementation
- [x] job.py — Job data structure + persistence
- [x] collector.py — response collection (session file + capture-pane + log fallback)
- [x] output_cleaner.py — ANSI cleanup
- [x] notify_hook.py — Codex notify hook script
- [x] cli.py — tcd CLI entry (start / status / output / check / wait / jobs / attach / kill / clean)
- [x] pyproject.toml — project configuration
- [x] tests/ — core module unit tests
- [x] README.md

### Phase 2: Claude Code Driver

**Goal**: Be able to drive Claude Code with `tcd start -p claude`

Deliverables:
- [x] providers/claude.py — Claude Code Provider
- [x] Marker protocol implementation (TCD_REQ / TCD_DONE injection and scanning)
- [x] Idle detection module
- [x] Claude session file parsing

### Phase 3: Gemini CLI Driver

**Goal**: Be able to drive Gemini CLI with `tcd start -p gemini`

Deliverables:
- [x] providers/gemini.py — Gemini CLI Provider
- [x] Gemini-specific handling (dual hash, idle detection as primary)

### Phase 4: Advanced Features

- [x] Python SDK (`from tcd import TCD`)
- [x] CLAUDE.md skill template
- [ ] MCP server wrapper (optional, not implemented)
- [ ] Cross-AI context transfer (optional, not implemented)
- [ ] `tcd pipe`: pipeline mode (optional, not implemented)

---

## 12. Technical Decision Records

| # | Decision | Options | Choice | Rationale |
|---|----------|---------|--------|-----------|
| TD-1 | Implementation language | Python / TypeScript / Rust | **Python 3.10+** | CCB reference implementation is Python; Claude Code ecosystem friendly; subprocess calls are straightforward |
| TD-2 | Package manager | pip / poetry / uv | **uv** | Fast, modern, good lockfile support |
| TD-3 | CLI framework | argparse / click / typer | **click** | Lightweight, mature, good sub-command support, no need for typer's type hint magic |
| TD-4 | Architecture pattern | daemon (TCP) / CLI + files | **CLI + files** | codex-orchestrator validated this pattern is sufficient; avoids CCB's daemon complexity |
| TD-5 | State storage | SQLite / JSON files | **JSON files** | Simple and direct, debuggable with `cat`/`jq`, no migrations needed |
| TD-6 | Completion detection | pure polling / pure hook / hybrid | **hybrid three strategies** | notify-hook is most reliable but only Codex supports it; marker is universal but not guaranteed; idle detection as fallback |
| TD-7 | Log recording | tmux capture / script / both | **script + capture dual** | script persists (still readable after session exits); capture is real-time (low latency) |

---

## 13. Risks and Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| AI CLI updates change behavior | Provider breaks | Medium | Version detection + log alerts + provider isolation (one broken doesn't affect others) |
| Marker protocol unreliable (AI doesn't output it) | Cannot detect completion | High (Gemini) | Idle detection fallback, 15–20s delay acceptable |
| tmux send-keys special character escaping issues | Command injection / dropped characters | Medium | Long text via load-buffer; strict escaping for short text |
| macOS vs Linux `script` parameter differences | Launch fails | Low | Platform detection, two parameter templates |
| Parallel job resource contention (CPU/memory) | AI slows down or OOM | Low | Documentation recommends max 3–5 parallel jobs |
| Claude Code sub-agent causes marker to be consumed by subprocess | Completion detection fails | Medium | Idle detection fallback + monitor main session state |

---

## 14. Success Criteria

### MVP (when Phase 1 is complete)

- [ ] `tcd start -p codex -m "write a hello world" -d /tmp/test` launches within 3s
- [ ] After Codex completes, `tcd check` returns 0 (idle)
- [ ] `tcd output` returns clean Codex response text
- [ ] `tcd send` can append instructions and trigger a new turn
- [ ] `tcd jobs` correctly shows all jobs

### Full (when Phase 3 is complete)

- [ ] All three providers can start, detect completion, and collect results normally
- [ ] Claude Code can call tcd via bash tool to dispatch tasks to Codex
- [ ] 3 different-provider jobs can run simultaneously without interference
- [ ] Test coverage > 80%
- [ ] README documentation complete

---

## Appendix A: Reference Projects

| Project | Repository | Reuse Value |
|---------|-----------|-------------|
| claude_code_bridge (CCB) | github.com/bfly123/claude_code_bridge | Multi-provider design, marker protocol, anti-loop guardrail |
| codex-orchestrator | github.com/kingbootoshi/codex-orchestrator | tmux operation patterns, notify-hook, Job management, ANSI cleanup |

Detailed analysis in [comparison report](research/comparison-ccb-vs-codex-orchestrator.md).

## Appendix B: Abbreviations

| Abbreviation | Full Form |
|-------------|-----------|
| tcd | tmux-codingagent-driver |
| CCB | claude_code_bridge |
| TUI | Text User Interface |
| ANSI | American National Standards Institute (terminal escape sequences) |
| JSONL | JSON Lines (one JSON object per line) |
