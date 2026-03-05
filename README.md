# tcd — tmux-codingagent-driver

Drive AI CLI tools (Codex, Claude Code, Gemini CLI) programmatically via tmux.

tcd launches AI coding agents in detached tmux sessions, injects prompts, detects turn completion, and collects responses — enabling higher-level orchestration systems to coordinate multiple AI agents.

## Features

- **Multi-provider support**: Codex, Claude Code, Gemini CLI
- **Completion detection**: Signal files, marker protocol, idle detection (3-strategy fallback)
- **Multi-turn conversations**: Send follow-up messages to running jobs
- **Event logging**: Append-only JSONL event log per job for full lifecycle tracing
- **Diagnostics**: Rule-based health checks (sandbox mismatch, stall, permission errors, stuck turns)
- **Token tracking**: Cumulative token usage recording (Codex)
- **Python SDK**: Programmatic access for agent orchestration
- **CLI interface**: Full CLI for interactive use and scripting

## Requirements

- Python 3.10+
- tmux (`brew install tmux` on macOS)
- At least one AI CLI tool installed:
  - [Codex](https://github.com/openai/codex) — `npm install -g @openai/codex`
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — `npm install -g @anthropic-ai/claude-code`
  - [Gemini CLI](https://github.com/google-gemini/gemini-cli) — `npm install -g @anthropic-ai/gemini-cli`

## Installation

```bash
pip install -e .
```

## Quick Start

### CLI

```bash
# Start a Codex job
tcd start -p codex -m "Fix the bug in main.py" -d /path/to/project

# Start a Claude Code job
tcd start -p claude -m "Add unit tests for the auth module" -d /path/to/project

# Start a Gemini job
tcd start -p gemini -m "Refactor the database layer" -d /path/to/project

# Check if the job is done (exit codes: 0=idle, 1=working, 2=context_limit, 3=not_found)
tcd check <job_id>

# Block until completion
tcd wait <job_id> --timeout 300

# Get the output
tcd output <job_id>

# Send a follow-up message
tcd send <job_id> "Now add error handling"

# List all jobs
tcd jobs

# Kill a job
tcd kill <job_id>

# Clean up finished jobs
tcd clean
```

### Python SDK

```python
from tcd import TCD

tcd = TCD()

# Start a job
job = tcd.start("claude", "Fix the bug in main.py", cwd="/path/to/project")

# Wait for completion (blocks)
result = tcd.wait(job.id, timeout=300)
print(f"State: {result.state}")

# Get output
output = tcd.output(job.id)
print(output)

# Multi-turn conversation
tcd.send(job.id, "Now add tests for the fix")
result = tcd.wait(job.id, timeout=300)

# Clean up
tcd.kill(job.id)
tcd.clean()
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `tcd start -p <provider> -m <prompt>` | Start a new AI job |
| `tcd status <job_id> [--json]` | Show job status |
| `tcd check <job_id> [--json]` | Non-blocking completion check (--json adds diagnostics) |
| `tcd wait <job_id> [--timeout N]` | Block until job completes |
| `tcd log <job_id> [--tail N] [--event TYPE]` | View job event log |
| `tcd output <job_id> [--full] [--raw]` | Get job output |
| `tcd send <job_id> <message>` | Send follow-up message |
| `tcd jobs [--status S] [--json]` | List all jobs |
| `tcd attach <job_id>` | Attach to tmux session (debugging) |
| `tcd kill <job_id> [--all]` | Kill running job(s) |
| `tcd clean [--all]` | Clean finished jobs |

### Start Options

| Option | Description |
|--------|-------------|
| `-p, --provider` | AI CLI provider: `codex`, `claude`, `gemini` |
| `-m, --prompt` | Task prompt (use `-` for stdin) |
| `-d, --cwd` | Working directory (default: `.`) |
| `--model` | Model name override |
| `--timeout` | Timeout in minutes (default: 60) |
| `--sandbox` | Codex sandbox mode |

## Provider Support

| Feature | Codex | Claude Code | Gemini CLI |
|---------|-------|-------------|------------|
| Auto-approve mode | `-a never` | `--dangerously-skip-permissions` | `--yolo` |
| Completion detection | notify-hook | marker + idle | marker + idle |
| Session parsing | JSONL | JSONL | capture-pane |
| TUI ready indicator | `›` | `❯` | `Type your message` |
| Trust dialog handling | N/A | Auto-accept | Auto-accept |

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌──────────────┐
│ Orchestrator     │────▶│  tcd CLI / SDK  │────▶│  tmux        │
│ (upstream agent) │     │                 │     │  sessions    │
└─────────────────┘     └─────────────────┘     └──────┬───────┘
                              │                        │
                    ┌─────────┤                  ┌─────┴──────┐
                    │         │                  │            │
               ┌────┴───┐ ┌──┴────┐ ┌─────┐   AI CLI TUIs:
               │ Codex  │ │Claude │ │Gemini│   Codex, Claude,
               │Provider│ │Provdr │ │Provdr│   Gemini
               └────────┘ └───────┘ └─────┘
```

### Completion Detection (3-strategy fallback)

1. **Signal file** (fastest): Provider writes a JSON signal file on turn completion
2. **Marker scan**: Scans tmux pane for `TCD_DONE:<req_id>` markers
3. **Idle detection**: Compares consecutive pane captures; N seconds of no change = idle

## Observability

### Event Log

Every job automatically produces an append-only JSONL event log at `~/.tcd/jobs/<id>.events.jsonl`:

```bash
tcd log <job_id>                    # All events
tcd log <job_id> --tail 5           # Last 5 events
tcd log <job_id> --event job.checked # Filter by type
```

### Diagnostics

`tcd check --json` returns structured status with automatic health checks:

```json
{
  "state": "working",
  "elapsed_s": 120,
  "turn_count": 0,
  "warnings": [
    {"code": "SANDBOX_MISMATCH", "severity": "warn", "message": "..."}
  ],
  "pane_tail": "... last 5 lines ..."
}
```

Warning codes: `SANDBOX_MISMATCH`, `STALL`, `PERMISSION_ERROR`, `TURN0_STUCK`.

### Token Tracking

Codex jobs record cumulative token usage (parsed from NDJSON session files):

```bash
tcd status <job_id>        # Shows "Tokens: in=5000 out=3000"
tcd status <job_id> --json # Includes total_tokens in JSON
```

## Upstream Agent Integration

Add to your project's `CLAUDE.md` for agent-to-agent delegation:

```markdown
## Multi-Agent via tcd

When tasks can be delegated to another AI agent:

1. Start a worker: `tcd start -p codex -m "Task description" -d /path/to/project`
2. Poll for completion: `tcd check <job_id>` (0=idle, 1=working)
3. Get results: `tcd output <job_id>`
4. Send follow-ups: `tcd send <job_id> "Additional instructions"`
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -q

# Run a specific test file
python -m pytest tests/test_sdk.py -q
```

## License

MIT
