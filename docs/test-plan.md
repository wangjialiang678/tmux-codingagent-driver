# tcd Closed-Loop Test Plan

**Version**: v0.1.0
**Date**: 2026-03-02
**Status**: COMPLETED (Phase 1–4 all verified, 119 tests pass)

---

## P0: Basic Build Checks

All items must pass after every logical unit is complete.

- [x] **P0-1: Dependency install**
  Pass criteria: `uv sync` exits with code 0, no errors
  Suggested command: `cd /Users/michael/projects/AI\ 工作流/tmux-codingagent-driver && uv sync`

- [x] **P0-2: Module import**
  Pass criteria: `from tcd import ...` raises no ImportError
  Suggested command: `uv run python -c "from tcd.tmux_adapter import TmuxAdapter; print('OK')"`

- [x] **P0-3: Type check**
  Pass criteria: mypy or pyright reports no errors (warnings acceptable)
  Suggested command: `uv run python -m py_compile src/tcd/*.py` (minimum compile check)

- [x] **P0-4: Unit tests**
  Pass criteria: `pytest` exits with code 0, all tests pass
  Suggested command: `uv run pytest tests/ -v`

- [x] **P0-5: CLI entry point executable**
  Pass criteria: `tcd --help` exits with code 0, output contains subcommands "start" "check" "output" etc.
  Suggested command: `uv run tcd --help`

- [x] **P0-6: tmux availability detection**
  Pass criteria: when tmux is not installed, gives a clear error message (including install instructions) without throwing a traceback
  Suggested command: `PATH=/usr/bin:/bin uv run tcd start -p codex -m "test" 2>&1` (temporarily remove tmux from PATH; adjust for environment)

---

## P1: Core Feature Verification (Phase 1 — Codex Driver MVP)

### Feature 1: tmux Adapter Basic Operations

- [x] **P1-1a: Create and destroy session** [CLI]
  Pass criteria: `create_session` followed by `session_exists` returns True; `kill_session` followed by returns False
  Suggested command: pytest unit tests (requires tmux integration test)
  ```python
  # Example test logic
  adapter = TmuxAdapter()
  adapter.create_session("tcd-test-001", "echo hello; read", "/tmp")
  assert adapter.session_exists("tcd-test-001") == True
  adapter.kill_session("tcd-test-001")
  assert adapter.session_exists("tcd-test-001") == False
  ```

- [x] **P1-1b: send_keys short text injection** [CLI]
  Pass criteria: after injecting text < 5000 characters, capture_pane can read the injected content
  Suggested command: pytest integration test

- [x] **P1-1c: send_long_text long text injection** [CLI]
  Pass criteria: after injecting 6000+ character text, capture_pane can read the full content (or its trailing portion)
  Suggested command: pytest integration test

- [x] **P1-1d: capture_pane read output** [CLI]
  Pass criteria: returns a non-empty string containing the actual content displayed in the session
  Suggested command: pytest integration test

### Feature 2: Job Management Lifecycle

- [x] **P1-2a: Create Job** [CLI]
  Pass criteria: `tcd start -p codex -m "echo hello"` exits with code 0, output contains "Job started:" and an 8-character hex ID
  Suggested command: `uv run tcd start -p codex -m "echo hello" -d /tmp`

- [x] **P1-2b: Job JSON persistence** [file]
  Pass criteria: `~/.tcd/jobs/{id}.json` exists, JSON parseable by jq, contains id/provider/status/prompt fields
  Suggested command: `cat ~/.tcd/jobs/*.json | jq .`

- [x] **P1-2c: Job list** [CLI]
  Pass criteria: `tcd jobs` exits with code 0, output contains the created Job's ID and status
  Suggested command: `uv run tcd jobs`

- [x] **P1-2d: Job list JSON** [CLI]
  Pass criteria: `tcd jobs --json` outputs a valid JSON array, each item contains id/provider/status
  Suggested command: `uv run tcd jobs --json | jq .`

- [x] **P1-2e: Job status query** [CLI]
  Pass criteria: `tcd status {id}` exits with code 0, displays status/provider/turn_count and other info
  Suggested command: `uv run tcd status {id}`

- [x] **P1-2f: Job status JSON** [CLI]
  Pass criteria: `tcd status {id} --json` outputs valid JSON containing id/status/turn_state
  Suggested command: `uv run tcd status {id} --json | jq .`

- [x] **P1-2g: Kill Job** [CLI]
  Pass criteria: `tcd kill {id}` exits with code 0; subsequent `tcd status {id}` shows failed
  Suggested command: `uv run tcd kill {id} && uv run tcd status {id}`

- [x] **P1-2h: Clean Jobs** [CLI]
  Pass criteria: `tcd clean` exits with code 0; completed/failed Job JSON files are deleted
  Suggested command: `uv run tcd clean && ls ~/.tcd/jobs/`

- [x] **P1-2i: Invalid Job ID friendly error** [CLI]
  Pass criteria: `tcd status invalid-id` exits with non-zero code; stderr contains friendly error message, no Python traceback
  Suggested command: `uv run tcd status nonexistent123 2>&1`

### Feature 3: Codex Provider Startup and Completion Detection

- [x] **P1-3a: Codex startup** [CLI]
  Pass criteria: after `tcd start -p codex -m "say hello"`, the corresponding tmux session exists (`tmux has-session -t tcd-codex-{id}` exits with code 0)
  Suggested command:
  ```bash
  JOB_ID=$(uv run tcd start -p codex -m "say hello" -d /tmp | grep -oE '[a-f0-9]{8}')
  tmux has-session -t "tcd-codex-$JOB_ID"
  ```

- [x] **P1-3b: Codex completion detection (notify-hook)** [CLI]
  Pass criteria: after Codex completes, `tcd check {id}` exits with code 0 (idle), signal file `~/.tcd/jobs/{id}.turn-complete` exists
  Suggested command:
  ```bash
  uv run tcd wait $JOB_ID --timeout 120
  uv run tcd check $JOB_ID
  echo "exit code: $?"
  ls ~/.tcd/jobs/${JOB_ID}.turn-complete
  ```

- [x] **P1-3c: Codex response collection** [CLI]
  Pass criteria: `tcd output {id}` output is non-empty, contains no ANSI escape sequences (no `\x1b[` or `\033[`), no TUI noise lines
  Suggested command:
  ```bash
  OUTPUT=$(uv run tcd output $JOB_ID)
  echo "$OUTPUT"
  echo "$OUTPUT" | grep -P '\x1b\[' && echo "FAIL: contains ANSI" || echo "PASS: clean output"
  ```

- [x] **P1-3d: Codex session file parsing** [CLI]
  Pass criteria: `tcd output {id}` can parse summary text from the Codex JSONL session file
  Suggested command: `uv run tcd output $JOB_ID`

### Feature 4: Multi-turn Conversation (send)

- [x] **P1-4a: Send follow-up instruction** [CLI]
  Pass criteria: `tcd send {id} "follow-up instruction"` exits with code 0; turn_count in `tcd status {id} --json` increments
  Suggested command:
  ```bash
  uv run tcd send $JOB_ID "now add a test"
  sleep 2
  uv run tcd status $JOB_ID --json | jq .turn_count
  ```

- [x] **P1-4b: Completion detection after send** [CLI]
  Pass criteria: after send, check first returns exit 1 (working); returns exit 0 (idle) after completion
  Suggested command:
  ```bash
  uv run tcd send $JOB_ID "add comments to the code"
  uv run tcd check $JOB_ID; echo "immediate: $?"
  uv run tcd wait $JOB_ID --timeout 120
  uv run tcd check $JOB_ID; echo "after wait: $?"
  ```

### Feature 5: Blocking Wait and Attach

- [x] **P1-5a: wait normal completion** [CLI]
  Pass criteria: `tcd wait {id} --timeout 120` exits with code 0 (completed)
  Suggested command: `uv run tcd wait $JOB_ID --timeout 120; echo "exit: $?"`

- [x] **P1-5b: wait timeout** [CLI]
  Pass criteria: `tcd wait {id} --timeout 1` exits with code 2 (timeout)
  Suggested command: start a long-running task first, then `uv run tcd wait $JOB_ID --timeout 1; echo "exit: $?"`

- [x] **P1-5c: attach connect** [CLI] (manually verified PASS)
  Pass criteria: `tcd attach {id}` enters the tmux session (manual confirmation or verify command does not error)
  Suggested command: `uv run tcd attach $JOB_ID` (manually Ctrl+B D to exit)
  Note: This item can be manually verified during final integration testing

---

## P1 Supplemental Tests (Phase 2/3 Extensions, Not Executed in Phase 1)

The following test items are only executed after the corresponding Provider is implemented in Phase 2/3:

- [x] **P1-EXT-1: Claude Provider startup and marker completion detection** [CLI] (Phase 2 E2E verified)
- [x] **P1-EXT-2: Gemini Provider startup and idle detection fallback** [CLI] (Phase 3 E2E verified)
- [ ] **P1-EXT-3: Parallel 3 different Provider Jobs** [CLI] (not tested)
- [x] **P1-EXT-4: Python SDK `from tcd import TCD`** [CLI] (18 unit tests passed)

---

## Manual Tests (Not Included in Automated P1)

- [x] **Manual-1: attach interactive debugging**
  Steps: start Job → `tcd attach` → observe AI execution in tmux → Ctrl+B D to exit
  Pass criteria: can see AI CLI real-time output; exiting attach does not affect Job execution

- [x] **Manual-2: Error when AI CLI not installed**
  Steps: `tcd start -p codex -m "test"` when codex is not in PATH
  Pass criteria: gives clear installation instructions without throwing a traceback

---

## Test Fixtures

P1 tests for this project primarily depend on actual AI CLI tools running. Ensure:
- Codex CLI is logged in and API key is valid
- Test prompts use simple tasks (e.g., "say hello", "write a hello world") to minimize wait time
- Test working directory uses `/tmp/tcd-test-{timestamp}/`; clean up after testing

---

## Verification Discipline

- **Pass criteria are locked**: all pass criteria above cannot be modified after user confirmation (unless the user explicitly approves)
- **Suggested commands may be adjusted**: suggested commands may be flexibly adjusted for the actual environment; adjustments must be recorded in docs/dev-log.md
- **Each fix changes only business code**: lowering pass criteria to make tests pass is not allowed
