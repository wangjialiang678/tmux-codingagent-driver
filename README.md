# tcd — tmux-codingagent-driver

Drive AI CLI tools (Codex, Claude Code, Gemini CLI) programmatically via tmux.

tcd launches AI coding agents in detached tmux sessions, injects prompts, detects turn completion, and collects responses — enabling higher-level orchestration systems to coordinate multiple AI agents.

> **设计文档**：[docs/architecture.md](docs/architecture.md) — 需求、四个关键设计
> 决策及其代价、技术架构、架构债与不变量。`docs/` 下其余文档的现状见
> [docs/README.md](docs/README.md)。

## Features

- **Multi-provider support**: Codex, Claude Code, Gemini CLI
- **Git worktree isolation**: Parallel jobs in isolated worktrees with auto-merge; uncommitted changes are stashed per job and restored on every exit path
- **Completion detection**: Signal files, marker protocol, idle detection (3-strategy fallback)
- **Multi-turn conversations**: Send follow-up messages to running jobs, with Claude queued-submit recovery
- **Incremental output**: `--tail` and `--since-line` for efficient polling
- **Activity extraction**: Real-time activity lines from scrollback (Edited, Created, Ran, etc.)
- **Event logging**: Append-only JSONL event log per job for full lifecycle tracing
- **Diagnostics**: Rule-based health checks (sandbox mismatch, stall, permission errors, stuck turns, fatal provider API errors)
- **Drift detection**: `tcd doctor` re-verifies the pane strings tcd matches on, which upstream CLI upgrades break silently
- **Token tracking**: Cumulative token usage recording (Codex)
- **Verbose logging**: `-v`/`-vv` for INFO/DEBUG level diagnostics
- **Python SDK**: Programmatic access for agent orchestration
- **CLI interface**: Full CLI for interactive use and scripting

## Requirements

- Python 3.10+
- tmux (`brew install tmux` on macOS)
- At least one AI CLI tool installed **and logged in** before `tcd start`:
  - [Codex](https://github.com/openai/codex) — `npm install -g @openai/codex` → then `codex login`
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — `npm install -g @anthropic-ai/claude-code` → then `claude` once to trust folder
  - [Gemini CLI](https://github.com/google-gemini/gemini-cli) — `npm install -g @google/gemini-cli` → then `gemini` once to auth

### Provider auth notes

- **Codex CLI** defaults to **ChatGPT Plus/Pro/Team subscription** auth (not API key). You must run `codex login` interactively once — it opens a browser and links your ChatGPT account. Credentials are cached in `~/.codex/auth.json`. Without this, tcd worktree jobs will hang at the login prompt and get killed by `TURN0_STUCK`. If you don't have a ChatGPT subscription, you can instead export `OPENAI_API_KEY=sk-...` (pay-per-token).
- **Claude Code** requires completing the folder-trust dialog once. `tcd` auto-handles the trust prompt via its Provider trust dialog handler, but first-time auth (Anthropic account login) must be done manually.
- **Gemini CLI** similarly requires initial Google account auth and may show a folder-trust dialog — tcd handles the latter automatically.

Verify before first tcd run:

```bash
codex whoami && echo "codex ok"
claude --version && echo "claude ok"
gemini --version && echo "gemini ok"
```

## Installation

```bash
pip install -e .
```

## Quick Start

### CLI

```bash
# Start a Codex job
tcd start -p codex -m "Fix the bug in main.py" -d /path/to/project

# Start with git worktree isolation (parallel-safe)
tcd start -p codex --worktree --wt-name auth -m "Implement auth module" -d /project

# Check if the job is done (exit codes: 0=idle, 1=working, 2=context_limit, 3=not_found)
tcd check <job_id>

# Structured check with diagnostics and activity
tcd check <job_id> --json

# Block until completion
tcd wait <job_id> --timeout 300

# For Claude/Gemini, exit 0 means the current turn is idle/complete.
# The job can still be "running" because the tmux session stays alive for follow-ups.

# Get the output (supports incremental polling)
tcd output <job_id>
tcd output <job_id> --since-line 50
tcd output <job_id> --tail 20

# Send a follow-up message
tcd send <job_id> "Now add error handling"

# Merge worktree back to main branch
tcd merge <job_id>
tcd merge <job_id> --squash

# List all jobs
tcd jobs

# Kill a job. A worktree still holding uncommitted changes or unmerged
# commits is kept, with its path printed — `--force` discards it.
tcd kill <job_id>
tcd kill <job_id> --force

# Clean up finished jobs. Jobs still owning a worktree or an unrestored
# auto-stash are skipped, because the job record is the only pointer to them.
tcd clean

# Check that the pane strings tcd matches on still hold. Run after upgrading
# Codex / Claude Code / Gemini CLI — when their TUI wording changes, detection
# fails silently rather than loudly.
tcd doctor
tcd doctor --live --provider codex

# Enable verbose logging
tcd -v start -p codex -m "..." -d .    # INFO level
tcd -vv check <job_id>                  # DEBUG level
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

### Parallel Jobs with Worktrees

```python
from tcd import TCD

tcd = TCD()

# Launch parallel jobs in isolated worktrees
auth_job = tcd.start("codex", "Implement auth module", cwd="/project",
                     worktree=True, worktree_name="auth")
api_job = tcd.start("codex", "Implement API layer", cwd="/project",
                    worktree=True, worktree_name="api")

# Wait for both, then merge
for job_id in [auth_job.id, api_job.id]:
    tcd.wait(job_id, timeout=600)
    tcd.merge_worktree(job_id)
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `tcd start -p <provider> -m <prompt>` | Start a new AI job |
| `tcd status <job_id> [--json]` | Show job status |
| `tcd check <job_id> [--json]` | Non-blocking completion check (--json adds diagnostics + activity) |
| `tcd wait <job_id> [--timeout N]` | Block until job completes |
| `tcd log <job_id> [--tail N] [--event TYPE]` | View job event log |
| `tcd output <job_id> [--full] [--raw] [--tail N] [--since-line N]` | Get job output |
| `tcd send <job_id> <message>` | Send follow-up message |
| `tcd merge <job_id> [--squash] [--no-cleanup]` | Merge worktree branch back to main |
| `tcd jobs [--status S] [--json] [--no-reconcile]` | List all jobs (reconciles records against live tmux sessions first) |
| `tcd attach <job_id>` | Attach to tmux session (debugging) |
| `tcd kill <job_id> [--all] [--force]` | Kill running job(s); keeps a worktree holding unsaved work unless `--force` |
| `tcd clean [--all] [--force]` | Clean finished jobs; skips jobs still owning a worktree or stash unless `--force` |
| `tcd doctor [--live] [--provider P] [--timeout N] [--json]` | Check that tcd's provider detection assumptions still hold |

### Start Options

| Option | Description |
|--------|-------------|
| `-p, --provider` | AI CLI provider: `codex`, `claude`, `gemini` |
| `-m, --prompt` | Task prompt (use `-` for stdin) |
| `-d, --cwd` | Working directory (default: `.`) |
| `--model` | Model name override |
| `--timeout` | Timeout in minutes (default: 60) |
| `--sandbox` | Codex sandbox mode (default: `danger-full-access`) |
| `--worktree` | Run in isolated git worktree |
| `--wt-name` | Worktree name (default: auto-generated from job ID) |

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

For marker providers (`claude`, `gemini`), `idle` means the current turn is complete. It does not mean the job is completed; the tmux session intentionally stays `running` so callers can use `tcd send` for follow-ups. If no `TCD_DONE` marker is visible, the idle fallback may still declare the turn done; use `tcd output --full` and `tcd log <job_id>` to inspect the pane history and events.

Claude Code can occasionally leave a follow-up in its queued input state after `tcd send`, showing `Press up to edit queued messages`. When tcd detects that hint immediately after sending, it automatically sends one extra Enter and records `job.message_submit_retry` in the event log.

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
  "activity": ["Edited src/main.py", "Ran pytest tests/"],
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

1. Start a worker: `tcd start -p codex -m - -d /path/to/project < prompt.txt`
   (stdin avoids shell-escaping breakage on prompts containing quotes)
2. Poll for turn completion: `tcd check <job_id>` (0=idle/turn complete, 1=working)
3. Get results: `tcd output <job_id>`
4. Send follow-ups: `tcd send <job_id> "Additional instructions"`
5. Release the session when done: `tcd kill <job_id>` — it stays alive for
   follow-ups until killed

Turn idle is not task complete: agents spawn their own sub-agents and can go
idle mid-task. Verify against the deliverables you asked for, and use `tcd send`
to resume the same session rather than starting over.
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
