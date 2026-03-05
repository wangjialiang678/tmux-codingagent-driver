# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

tcd (tmux-codingagent-driver) drives AI CLI tools (Codex, Claude Code, Gemini CLI) programmatically via tmux. It launches AI agents in detached tmux sessions, injects prompts, detects turn completion, and collects responses for higher-level orchestration.

## Commands

```bash
# Install dependencies
uv sync

# Run all tests
uv run pytest tests/ -q

# Run a single test file
uv run pytest tests/test_sdk.py -q

# Run a specific test
uv run pytest tests/test_bridge_absorb.py::TestUtf8Chunks::test_cjk_not_split -v

# Install globally (for E2E testing)
uv tool install .

# Run CLI directly without installing
uv run tcd --help
```

## Architecture

### Two Usage Modes

1. **Full SDK**: `from tcd import TCD` - high-level orchestration (start/wait/check/output/send/kill)
2. **Lightweight import**: `from tcd.tmux_adapter import TmuxAdapter` - direct tmux operations without job management

### Module Dependency Flow

```
cli.py (Click CLI) --> sdk.py (TCD class) --> job.py (Job + JobManager)
                                          --> provider.py (ABC + registry)
                                          --> tmux_adapter.py (tmux primitives)
                                          --> collector.py (response collection)
                                          --> event_log.py (append-only JSONL events)

provider.py <-- providers/codex.py   (CodexProvider + CodexOutput)
            <-- providers/claude.py  (ClaudeProvider)
            <-- providers/gemini.py  (GeminiProvider)

provider.py --> marker_detector.py (TCD_DONE marker scan + context limit detection)
            --> idle_detector.py (consecutive capture-pane comparison)
            --> notify_hook.py (Codex signal file writer, runs as subprocess)

collector.py --> output_cleaner.py (ANSI stripping, noise removal, JSON extraction)

config.py (path constants: ~/.tcd/jobs/, file naming conventions)
diagnostics.py --> event_log.py (rule-based health checks: stall, permission, context limit)
```

### Provider System

Providers use a decorator-based registry pattern (`@register_provider`). Each provider implements:
- `build_launch_command()` - shell command to start the AI CLI
- `build_prompt_wrapper()` - wrap prompt with completion markers
- `detect_completion()` - 3-strategy fallback: signal file -> marker scan -> idle detection
- `parse_response()` - extract response from session/log files

Providers auto-register on import via `src/tcd/__init__.py`.

### Completion Detection (3-strategy fallback)

1. **Signal file**: Provider writes JSON to `~/.tcd/jobs/<id>.turn-complete` (Codex notify-hook)
2. **Marker scan**: Scans tmux pane for `TCD_DONE:<req_id>` markers (Claude, Gemini)
3. **Idle detection**: Consecutive capture-pane comparisons; N seconds unchanged = idle

### Key Design Decisions

- **`send_text()` auto-selects** between `send_keys -l` (short text) and `paste-buffer -p` (long/multiline). Ink-based TUIs treat newlines in send-keys as Enter keypresses, so bracketed paste is required for multi-line input.
- **UTF-8 chunking**: `send_keys` splits at character boundaries (4096 byte limit) to avoid cutting multi-byte chars.
- **CaptureDepth enum**: Semantic constants (STATUS=20, HEALTH=50, CONTEXT=500, CHECKPOINT=2000, FULL=-1) instead of magic numbers.
- **Atomic job persistence**: `JobManager.save_job()` uses write-to-temp + `os.replace()`.
- **Trust dialog handling**: `sdk.py:_wait_for_tui()` auto-accepts trust prompts before injecting the first prompt.

### Event Logging & Diagnostics

- `event_log.py`: Append-only JSONL event stream per job (`<id>.events.jsonl`). `emit(job_id, event, **data)` is fire-and-forget (never raises).
- `diagnostics.py`: Rule-based health checks consuming event logs. Returns `Warning` objects with severity levels. Powers `tcd check --json` diagnostic output.
- All path constants centralized in `config.py` (`TCD_HOME`, `JOBS_DIR`, `job_*_path()` helpers).

### State Storage

All job state lives in `~/.tcd/jobs/` as flat files: `<id>.json` (metadata), `<id>.log` (script recording), `<id>.prompt`, `<id>.turn-complete` (signal), `<id>.events.jsonl` (event log).

## Testing Conventions

- Tests use `unittest.mock` extensively to avoid real tmux/subprocess calls
- `test_tmux_adapter.py` creates real tmux sessions (integration tests)
- `test_bridge_absorb.py` covers absorbed tmux-bridge code (UTF-8 chunking, DCS cleaning, JSON extraction, CodexOutput parsing)
- Provider tests verify command building, completion detection, and response parsing independently

## Closed-Loop Testing Rules

- Test plan with locked criteria: `docs/test-plan.md`
- Dev log: `docs/dev-log.md`
- After each logical unit, run P0+P1 verification per the test plan
- Every 3 feature paths, run full regression (all P0 + passed P1)
- On failure: fix business code only, never relax criteria
- Stop conditions: 5 fails on one item, oscillation (fix A breaks B x2), 15 total fixes, or 3 consecutive P0 rounds failing
