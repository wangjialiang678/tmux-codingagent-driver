# tmux-codingagent-driver Design Document

**Date**: 2026-03-02
**Status**: IMPLEMENTED (Phases 1–4 fully complete)

---

## 1. Project Positioning

A general-purpose **tmux-based AI CLI driver** that lets upstream agents (Claude Code, OpenClaw, or any orchestrator) drive downstream AI CLI tools (Codex, Claude Code, Gemini CLI) through tmux to execute programming tasks.

**Core value**: no API round-trips; leverages the AI CLI's native session management to achieve extremely low token overhead (50–200 tokens/call) for multi-AI task dispatch.

```
┌─────────────────────────────────────────────────────────┐
│  Upstream Agent (Claude Code / OpenClaw / custom script) │
│  Invoked via CLI commands or Python/TS SDK               │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│             tmux-codingagent-driver                      │
│                                                         │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐               │
│  │ Job Mgr │  │ Provider │  │ Response │               │
│  │         │  │ Registry │  │ Collector│               │
│  └────┬────┘  └────┬─────┘  └────┬─────┘               │
│       │            │              │                      │
│       ▼            ▼              ▼                      │
│  ┌─────────────────────────────────────┐                │
│  │         tmux Adapter Layer          │                │
│  │  send-keys / capture-pane /         │                │
│  │  load-buffer / new-session          │                │
│  └──────────────────┬──────────────────┘                │
└─────────────────────┼───────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   ┌─────────┐  ┌─────────┐  ┌─────────┐
   │ Codex   │  │ Claude  │  │ Gemini  │
   │ CLI     │  │ Code    │  │ CLI     │
   │ (tmux)  │  │ (tmux)  │  │ (tmux)  │
   └─────────┘  └─────────┘  └─────────┘
```

---

## 2. Design Principles

1. **CLI-first**: the primary entry point is a command-line tool, callable by any agent via `bash`
2. **Pluggable providers**: each AI CLI is a provider with a unified interface and independent implementation
3. **No daemon**: no TCP server; state is managed via job files + signal files (inspired by codex-orchestrator)
4. **Dual completion detection**: notify-hook (when the CLI supports it) + marker protocol (universal fallback)
5. **Minimal dependencies**: Python 3.10+, only requires tmux

---

## 3. Core Module Design

### 3.1 Provider Abstraction

```python
# provider.py
class Provider(ABC):
    """Adapter for a specific AI CLI"""

    name: str                    # "codex" | "claude" | "gemini"
    cli_command: str             # "codex" | "claude" | "gemini"

    @abstractmethod
    def build_launch_command(self, job: Job) -> str:
        """Build the shell command to launch the AI CLI"""

    @abstractmethod
    def build_prompt_wrapper(self, message: str, req_id: str) -> str:
        """Wrap the prompt (add completion markers, etc.)"""

    @abstractmethod
    def detect_completion(self, job: Job) -> CompletionResult | None:
        """Detect whether the task is complete; return result or None"""

    @abstractmethod
    def parse_response(self, job: Job) -> str:
        """Parse the AI response from logs / session file"""

    @abstractmethod
    def get_session_log_path(self, job: Job) -> Path | None:
        """Return the path to the AI's native session file"""
```

### 3.2 Known Provider Implementation Strategies

| Provider | Launch Command | Completion Detection | Response Parsing |
|----------|----------------|---------------------|-----------------|
| **Codex** | `codex -a never -c notify=[hook] ...` | notify-hook signal file (native) | `~/.codex/sessions/*.jsonl` |
| **Claude Code** | `claude --dangerously-skip-permissions` | CCB_DONE marker (prompt injection) | `~/.claude/projects/` JSONL |
| **Gemini CLI** | `gemini ...` | CCB_DONE marker + 15s idle detection | capture-pane full text |

### 3.3 Job Management

```python
# job.py
@dataclass
class Job:
    id: str                     # 8-byte hex
    provider: str               # "codex" | "claude" | "gemini"
    status: str                 # "pending" | "running" | "completed" | "failed"
    prompt: str
    cwd: str
    tmux_session: str           # "tcd-{provider}-{id}"
    created_at: str
    started_at: str | None
    completed_at: str | None
    result: str | None
    error: str | None
    turn_count: int
    turn_state: str             # "working" | "idle" | "context_limit"
    last_agent_message: str | None

# Storage:  ~/.tcd/jobs/{id}.json
# Signal:   ~/.tcd/jobs/{id}.turn-complete
# Log:      ~/.tcd/jobs/{id}.log
```

### 3.4 tmux Adapter

```python
# tmux_adapter.py
class TmuxAdapter:
    """Encapsulates tmux operation primitives"""

    LONG_PROMPT_THRESHOLD = 5000  # characters

    def create_session(self, name: str, cmd: str, cwd: str) -> bool
    def session_exists(self, name: str) -> bool
    def send_keys(self, session: str, text: str) -> bool
    def send_long_text(self, session: str, text: str) -> bool:
        """Long text: write temp file → load-buffer → paste-buffer"""
    def capture_pane(self, session: str, lines: int = -1) -> str | None
    def kill_session(self, session: str) -> bool
```

### 3.5 Response Collector

```python
# collector.py
class ResponseCollector:
    """Unified response collection with multi-strategy fallback"""

    def collect(self, job: Job) -> str | None:
        # 1. Provider-specific parsing (session file)
        result = provider.parse_response(job)
        if result: return result

        # 2. tmux capture-pane
        result = tmux.capture_pane(job.tmux_session)
        if result: return clean_ansi(result)

        # 3. script log file
        log_path = job_log_path(job.id)
        if log_path.exists(): return clean_ansi(log_path.read_text())

        return None
```

---

## 4. CLI Interface Design

```bash
# Start a job
tcd start --provider codex --prompt "implement user registration" --cwd /path/to/project
tcd start --provider claude --prompt "fix login bug" --cwd /path/to/project
tcd start --provider gemini --prompt "review this code" --cwd /path/to/project

# Send a follow-up instruction to a running job
tcd send <job-id> "add unit tests"

# Check job status
tcd status <job-id>           # single job status
tcd status <job-id> --json    # JSON format (for agent parsing)
tcd jobs                      # list all jobs

# Get output
tcd output <job-id>           # get final output
tcd output <job-id> --full    # full scrollback

# Wait for completion (blocking)
tcd wait <job-id> --timeout 300

# Check turn completion (non-blocking)
tcd check <job-id>            # exit 0 = idle, exit 1 = working, exit 2 = context_limit

# Attach to tmux session (for debugging)
tcd attach <job-id>

# Cleanup
tcd kill <job-id>
tcd clean                     # clean up completed/failed jobs
```

---

## 5. Dual-Strategy Completion Detection

### Strategy A: notify-hook (preferred when natively supported by the provider)

Codex natively supports `notify` configuration. When an agent turn ends, Codex automatically calls our hook script:

```
codex -c 'notify=["python3", "tcd-notify-hook", "{jobId}"]' ...
```

The hook writes a signal file `~/.tcd/jobs/{id}.turn-complete`; the upstream agent simply polls this file.

### Strategy B: Marker Protocol (universal fallback)

For CLIs that do not support notify-hook (e.g. Claude Code, Gemini), inject a marker into the prompt:

```
TCD_REQ:{req_id}
{actual message}
After completing your reply, output on the last line: TCD_DONE:{req_id}
```

The collector scans the log / capture-pane output; finding `TCD_DONE:{req_id}` is treated as completion.

### Strategy C: Idle Detection (last resort)

If the AI does not output the marker (common with Gemini), fall back to idle detection:
- capture-pane every 2 seconds, compare content
- 15 seconds with no change → treat as complete

---

## 6. Upstream Agent Integration

### 6.1 Claude Code Integration (via CLAUDE.md + bash skill)

Define `tcd` command usage in the project CLAUDE.md; Claude Code calls it via the bash tool:

```markdown
# CLAUDE.md
## Available tools: tcd (AI task dispatch)
- `tcd start --provider codex --prompt "..." --cwd .` launch Codex to execute a task
- `tcd check <id>` check for completion (non-blocking)
- `tcd output <id>` get result
- **Important**: after start, poll with check immediately — do not sleep-wait
```

### 6.2 OpenClaw / Custom Agent Integration

Call the CLI via subprocess or import the Python module:

```python
from tcd import start_job, check_job, get_output

job = start_job(provider="codex", prompt="...", cwd="/path")
while not check_job(job.id).is_idle:
    time.sleep(2)
result = get_output(job.id)
```

### 6.3 MCP Server Integration (optional future extension)

Could be wrapped as an MCP server providing `tcd_start`, `tcd_check`, `tcd_output` tools, allowing Claude Code to call tcd directly via MCP rather than bash.

---

## 7. What We Took from Each Reference Project

### From codex-orchestrator

- [x] Core tmux flow (create → send-keys → capture-pane)
- [x] `script -q` log recording
- [x] Long-prompt load-buffer/paste-buffer strategy
- [x] notify-hook signal file mechanism
- [x] `echo marker; read` to prevent session exit
- [x] Job JSON file management
- [x] sleep timing empirical values
- [x] ANSI cleanup logic

### From CCB

- [x] Multi-provider abstraction layer design
- [x] CCB_DONE marker protocol concept (our TCD_DONE)
- [x] Per-session serialization (prevent concurrency issues)
- [x] Async guardrail anti-loop pattern
- [x] Terminal backend abstraction (future WezTerm support)
- [x] Context transfer concept (cross-AI context migration)

### Not taken

- CCB's daemon TCP server architecture (too heavy)
- CCB's Memory-First three-layer storage (premature optimization)
- codex-orchestrator's Codex-only hardcoding (we need generality)

---

## 8. MVP Scope

### Phase 1: Codex Driver (reuses ~90% of codex-orchestrator logic)

- [x] tmux adapter core operations
- [x] Codex provider (launch, notify-hook, session parsing)
- [x] Job management (start/status/output/check/kill)
- [x] CLI entry (tcd command)
- [x] ANSI output cleanup

### Phase 2: Claude Code Driver

- [x] Claude provider (launch, marker protocol, JSONL session parsing)
- [x] Idle detection fallback

### Phase 3: Gemini CLI Driver

- [x] Gemini provider (launch, marker + idle detection)

### Phase 4: Advanced Features

- [x] Python SDK (`from tcd import TCD`)
- [ ] MCP server wrapper (not implemented)
- [ ] Cross-AI context transfer (not implemented)
- [ ] WezTerm backend (not implemented)
- [ ] Parallel multi-job orchestration (not implemented)

---

## 9. Technology Choices

| Option | Decision | Rationale |
|--------|----------|-----------|
| Language | **Python 3.10+** | Consistent with CCB; Claude Code ecosystem friendly |
| Package manager | **uv** | Fast, modern |
| CLI framework | **click** | Lightweight, mature |
| Dependencies | tmux (system) | Minimal dependencies |
| Testing | pytest | Standard |

---

## 10. Directory Structure (expected)

```
tmux-codingagent-driver/
├── src/tcd/
│   ├── __init__.py
│   ├── cli.py              # click CLI entry point
│   ├── tmux_adapter.py     # tmux operation wrapper
│   ├── job.py              # Job data structure + persistence
│   ├── provider.py         # Provider abstract base class
│   ├── providers/
│   │   ├── codex.py        # Codex provider
│   │   ├── claude.py       # Claude Code provider
│   │   └── gemini.py       # Gemini CLI provider
│   ├── collector.py        # Response collection (multi-strategy fallback)
│   ├── notify_hook.py      # notify-hook script
│   ├── output_cleaner.py   # ANSI cleanup
│   └── config.py           # Global configuration
├── bin/
│   └── tcd-notify-hook     # notify hook executable
├── tests/
├── docs/
│   ├── design.md           # this document
│   └── research/           # research reports
├── pyproject.toml
└── README.md
```

---

## 11. Conclusion

**Neither reference project can be used directly**, but both provide key design patterns and experience:

- **codex-orchestrator** provides the most direct implementation reference for driving Codex via tmux (~90% portable), but only supports Codex and is written in TypeScript
- **CCB** provides the architectural blueprint for multi-AI collaboration and extensive edge-case fix experience, but its architecture is too heavy to embed directly

Our approach is: **use codex-orchestrator's implementation patterns + CCB's architectural design thinking** to build a lightweight, general-purpose Python AI CLI driver.
