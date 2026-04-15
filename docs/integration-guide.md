# tcd Integration Guide

tcd supports two usage modes: **full orchestration** (CLI/SDK) and **lightweight driving** (direct import of low-level modules). Choose based on your use case.

---

## Scenario 1: Use Inside Claude Code

Configure via CLAUDE.md and invoke the tcd CLI through the bash tool.

### Configuration

Add the following to your project's CLAUDE.md:

```markdown
## tcd: AI Task Dispatcher

Available commands:
- `tcd start -p <provider> -m "<prompt>" -d <cwd>` — start a task, returns job_id
- `tcd check <job_id>` — non-blocking check (exit 0=done, 1=running)
- `tcd wait <job_id> --timeout <seconds>` — blocking wait for completion
- `tcd output <job_id>` — retrieve results
- `tcd send <job_id> "<message>"` — send a follow-up instruction
- `tcd jobs` — list all tasks
- `tcd kill <job_id>` — terminate a task
- `tcd clean` — clean up completed tasks

Provider: codex / claude / gemini
```

### Basic Usage

```bash
# Start a Codex job
tcd start -p codex -m "Implement user registration API" -d /path/to/project
# → Job started: a3f2b1c9

# Check completion
tcd check a3f2b1c9  # exit 0 = done

# Get results
tcd output a3f2b1c9
```

### Polling with Progress (Recommended for Orchestrators)

**Do NOT use `tcd wait` in orchestration** — it blocks the process and hides intermediate output. Use a polling loop instead:

```bash
# Each poll cycle (every 15s):
tcd check <job_id> --json     # state + activity + warnings
tcd output <job_id> --since-line <last_line> 2>/dev/null  # incremental output
# Read __lines_total=N from stderr to update last_line
```

`tcd check --json` returns structured status with `activity` (extracted meaningful operations from scrollback) and `warnings` (health diagnostics like `SANDBOX_MISMATCH`, `STALL`, `TURN0_STUCK`).

For Claude Code orchestrators: each poll must be an independent Bash call so you can show progress between calls. See `.claude/skills/codex-worker/SKILL.md` for the full polling protocol.

### Parallel Dispatch

```bash
tcd start -p codex -m "Implement backend API" -d /project
tcd start -p gemini -m "Implement frontend page" -d /project
tcd start -p claude -m "Write technical docs" -d /project

# Monitor
tcd jobs --json
```

### Worktree Parallel Execution

Run conflict-free parallel jobs in isolated git worktrees:

```bash
tcd start -p codex --worktree --wt-name auth -m "Implement auth" -d /project
tcd start -p codex --worktree --wt-name api -m "Implement API" -d /project

# After completion, merge back
tcd merge <job_id_auth>
tcd merge <job_id_api>  # --squash for single commit
```

---

## Scenario 2: OpenClaw Plugin

OpenClaw is a TypeScript project that calls the tcd CLI (JSON output) via `child_process`.

### Invocation Pattern

```typescript
import { execSync } from "child_process";

// Start a task
const startResult = JSON.parse(
  execSync(`tcd start -p codex -m "Fix the bug" -d /project --json`).toString()
);
const jobId = startResult.id;

// Check completion
const status = JSON.parse(
  execSync(`tcd status ${jobId} --json`).toString()
);

// Get output
const output = execSync(`tcd output ${jobId}`).toString();
```

### OpenClaw Tool Definition Template

```typescript
import { Type } from "@sinclair/typebox";

export function createTcdTool(): AnyAgentTool {
  return {
    name: "tcd_dispatch",
    description: "Dispatch a coding task to an AI CLI agent via tcd",
    parameters: Type.Object({
      provider: Type.Union([
        Type.Literal("codex"),
        Type.Literal("claude"),
        Type.Literal("gemini"),
      ]),
      prompt: Type.String({ description: "Task description" }),
      cwd: Type.Optional(Type.String()),
      timeout: Type.Optional(Type.Number({ minimum: 0 })),
    }),
    execute: async (_id, params) => {
      const { provider, prompt, cwd, timeout } = params as any;
      const args = [`-p`, provider, `-m`, prompt];
      if (cwd) args.push(`-d`, cwd);
      if (timeout) args.push(`--timeout`, String(timeout));

      const result = execSync(`tcd start ${args.join(" ")} --json`).toString();
      return { content: [{ type: "text", text: result }] };
    },
  };
}
```

---

## Scenario 3: Nanobot / Python Orchestration

Import the Python SDK or low-level modules directly.

### Method A: Full SDK (recommended)

```python
from tcd import TCD

tcd = TCD()
job = tcd.start("codex", "Implement CRUD API", cwd="/project")
result = tcd.wait(job.id, timeout=300)
output = tcd.output(job.id)
tcd.clean()
```

### Method B: Lightweight Driving (tmux interaction layer only)

Bypasses Job management and Provider registry; operates tmux sessions directly:

```python
from tcd.tmux_adapter import TmuxAdapter, CaptureDepth
from tcd.output_cleaner import clean_output, extract_json_payloads

adapter = TmuxAdapter()

# Create a session
adapter.create_session(
    name="my-codex-job",
    cmd="codex -a never --prompt 'Fix the login bug'",
    cwd="/path/to/project",
)

# Send text (automatically selects send-keys or load-buffer)
adapter.send_text("my-codex-job", "Add unit tests")

# Capture output (semantic depth)
raw = adapter.capture_pane("my-codex-job", depth=CaptureDepth.CONTEXT)
clean = clean_output(raw)

# Extract JSON (4-layer strategy)
payloads = extract_json_payloads(raw)

# Cleanup
adapter.kill_session("my-codex-job")
```

### Method C: Nanobot Tool Adapter Template

```python
from tcd import TCD


class CodexTmuxTool:
    """Nanobot Tool ABC adapter for tcd."""

    name = "codex_dispatch"
    description = "Dispatch a coding task to Codex via tmux"

    def __init__(self, timeout: int = 3600):
        self.tcd = TCD()
        self.timeout = timeout

    def run(self, params: dict, context=None) -> str:
        job = self.tcd.start(
            provider="codex",
            prompt=params["task"],
            cwd=params.get("cwd", "."),
        )
        self.tcd.wait(job.id, timeout=self.timeout)
        output = self.tcd.output(job.id) or ""
        self.tcd.clean()
        return output
```

---

## Scenario 4: Shell Script Batch Orchestration

### Parallel Batch

```bash
#!/bin/bash
JOBS=()
for task in "write fibonacci" "write HTTP server" "write CLI parser"; do
    JOB_ID=$(tcd start -p codex -m "$task" -d /tmp/batch | grep "Job started:" | awk '{print $NF}')
    JOBS+=("$JOB_ID")
done

# Wait for all to complete
for job_id in "${JOBS[@]}"; do
    tcd wait "$job_id" --timeout 300
    echo "=== $job_id ==="
    tcd output "$job_id"
done

tcd clean
```

### Serial Pipeline

```bash
#!/bin/bash
# Codex writes code → Claude reviews → Gemini writes tests
IMPL=$(tcd start -p codex -m "Implement a date handling library" -d /project | awk '/Job started:/{print $NF}')
tcd wait "$IMPL"

REVIEW=$(tcd start -p claude -m "Review src/date-utils.ts" -d /project | awk '/Job started:/{print $NF}')
tcd wait "$REVIEW"

TEST=$(tcd start -p gemini -m "Write tests for date-utils" -d /project | awk '/Job started:/{print $NF}')
tcd wait "$TEST"

tcd clean
```

---

## Scenario 5: Structured Codex Output

When you need to extract the Codex thread ID, list of modified files, or token usage:

```python
from tcd import TCD
from tcd.providers.codex import CodexProvider

tcd = TCD()
job = tcd.start("codex", "Refactor the auth module", cwd="/project")
tcd.wait(job.id)

# Structured parsing
provider = CodexProvider()
result = provider.parse_response_structured(job)
if result:
    print(f"Thread: {result.thread_id}")
    print(f"Files modified: {result.files_modified}")
    print(f"Tokens: {result.tokens}")
    print(f"Summary: {result.summary}")
```

---

## API Reference

### Full SDK (`from tcd import TCD`)

| Method | Returns | Description |
|------|--------|------|
| `start(provider, prompt, cwd, model, timeout)` | `Job` | Start a task |
| `check(job_id)` | `CheckResult` | Non-blocking status check |
| `wait(job_id, timeout)` | `CheckResult` | Blocking wait |
| `output(job_id)` | `str \| None` | Get cleaned output |
| `send(job_id, message)` | `bool` | Send follow-up instruction |
| `status(job_id)` | `Job` | Get full Job status |
| `jobs(status)` | `list[Job]` | List tasks |
| `kill(job_id)` | `bool` | Terminate a task |
| `clean()` | `int` | Clean up completed tasks |

### Low-Level Modules (lightweight import)

| Module | Core Function/Class | Description |
|------|------------|------|
| `tcd.tmux_adapter` | `TmuxAdapter` | tmux operation primitives |
| `tcd.tmux_adapter` | `CaptureDepth` | Semantic capture depth |
| `tcd.output_cleaner` | `clean_output()` | ANSI + TUI noise cleaning |
| `tcd.output_cleaner` | `strip_ansi()` | ANSI-only cleaning |
| `tcd.output_cleaner` | `extract_json_payloads()` | 4-layer JSON extraction |
| `tcd.providers.codex` | `CodexOutput` | Structured Codex output |
| `tcd.providers.codex` | `parse_codex_ndjson()` | Parse Codex NDJSON |

### CLI Commands

| Command | Description |
|------|------|
| `tcd start -p <provider> -m <prompt>` | Start a task |
| `tcd check <id>` | Non-blocking check (exit: 0/1/2/3) |
| `tcd wait <id> [--timeout N]` | Blocking wait |
| `tcd output <id> [--full] [--raw]` | Get output |
| `tcd send <id> <message>` | Send follow-up instruction |
| `tcd status <id> [--json]` | View status |
| `tcd jobs [--status S] [--json]` | List tasks |
| `tcd attach <id>` | Connect to tmux session |
| `tcd kill <id> [--all]` | Terminate a task |
| `tcd clean [--all] [--before 7d]` | Cleanup |
