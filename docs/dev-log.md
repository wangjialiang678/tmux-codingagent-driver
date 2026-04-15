# Development Log

<!-- Automatically appended after each verification run during the execution phase -->

---
## Round 6 — 2026-03-05 v0.3.0 Worktree Support (Codex-driven closed-loop)

Phase: Phase 1–3 full implementation
Trigger: PRD prd-worktree.md confirmed; implemented in 3 phases via Codex Worker
Workflow: Closed-loop testing + Codex sub-agent orchestration

### Implementation Process

| Phase | Codex Job | Duration | Output |
|------|-----------|------|------|
| Phase 1: Worktree primitives | 326fbb48 | 5m11s | `src/tcd/worktree.py` + `tests/test_worktree.py` (12 tests) |
| Phase 2: Job + SDK integration | ac139660 | ~5m | `job.py` fields + `sdk.py` start/merge/kill + `tests/test_worktree_sdk.py` (12 tests) |
| Phase 3: CLI integration | 7632b48d | ~5m | `cli.py` --worktree/merge + `tests/test_cli_worktree.py` (7 tests) |

### Codex Self-Fix

- Phase 1: macOS `/var` vs `/private/var` path difference caused `get_repo_root` assertion failure → Codex automatically added `.resolve()` to fix it

### Closed-Loop Verification Results

| Test Item | Result |
|--------|------|
| P0-1: Dependency install | PASS |
| P0-2: Module import (worktree) | PASS |
| P0-3: Regression tests (222 tests, baseline 191) | PASS |
| P0-4: CLI entry point (including merge subcommand) | PASS |
| P1 Phase 1: Worktree primitives (12/12) | PASS |
| P1 Phase 2: SDK integration (12/12) | PASS |
| P1 Phase 3: CLI integration (7/7) | PASS |

### Summary
- Total tests: 222 (+31 new tests)
- Total fixes: 1 (Codex self-fix for macOS path)
- No oscillation
- New files: 4 (worktree.py, test_worktree.py, test_worktree_sdk.py, test_cli_worktree.py)
- Modified files: 3 (job.py, sdk.py, cli.py)

---
## Round 1 — 2026-03-02 Steps 1–9 Unit Test Regression

Phase: P0
Trigger: Steps 1–9 implementation complete (project scaffolding, tmux_adapter, provider, job, codex provider, notify_hook, output_cleaner, collector, CLI)

### Verification Results
| Test Item | Pass Criteria | Actual Command | Result |
|--------|---------|---------|------|
| P0-1: Dependency install | uv sync exit code=0, no errors | `uv sync` | PASS |
| P0-2: Module import | import raises no ImportError | `uv run python -c "import tcd"` | PASS |
| P0-3: Compile check | py_compile no errors | `uv run tcd --help` (implicit) | PASS |
| P0-4: Unit tests | pytest exit code=0 | `uv run pytest tests/ -v` → 62/62 passed | PASS |
| P0-5: CLI entry point | tcd --help includes subcommands | `uv run tcd --help` | PASS |
| P0-6: tmux detection | clear error when tmux absent | Verified via unit tests | PASS |

---
## Round 2 — 2026-03-02 Step 10 E2E Integration Tests (First Attempt)

Phase: P1 end-to-end
Trigger: Step 10 — real Codex CLI E2E test

### Failure Analysis

- **Test item**: P1-3a: Codex startup
- **Failure reason 1**: `Unknown provider: 'codex'. Available: (none)` — CodexProvider module was not registered at import time
- **Fix**: Added `import tcd.providers.codex  # noqa: F401` in `src/tcd/__init__.py`
- **Attempts**: 1

- **Test item**: P1-3b / P1-5a: Completion detection / wait
- **Failure reason 2**: `tcd wait` timed out (exit 2); Codex TUI received prompt text but did not submit (Enter had no effect)
- **Root cause**: `_escape_single_quotes()` converted `'` to `'\''`, but `subprocess.run` with a list argument does not go through the shell, so the escape characters were sent literally to the TUI, corrupting the text (`'\''Hello'\''` instead of `'Hello'`)
- **Fix A**: Removed `_escape_single_quotes`, switched to tmux `send-keys -l` (literal mode)
- **Attempts**: 1

- **Failure reason 3**: After the fix, prompt text was correct but Enter still didn't trigger submission
- **Root cause**: Codex TUI initializes slowly (>1s); `cli.py` only waited 1 second before injecting the prompt. The Enter key sent immediately after text injection was swallowed by the TUI
- **Fix B**:
  1. Added a 0.2s delay between text and Enter in `send_keys`
  2. Changed the fixed `sleep(1)` in `cli.py` to poll for TUI readiness (detect `›` character, up to 10s)
- **Attempts**: 2

### Command Adjustments
- Original suggested command: fixed `time.sleep(1)` to wait for TUI
- Actual used: poll `capture_pane` to detect `›` character
- Adjustment reason: Codex TUI initialization time is variable; fixed wait is unreliable

---
## Round 3 — 2026-03-02 Step 10 E2E Integration Tests (After Fixes)

Phase: P0 + P1 full run
Trigger: Full verification after fixing send_keys and TUI wait

### P0 Verification Results
| Test Item | Pass Criteria | Actual Command | Result |
|--------|---------|---------|------|
| P0-1: Dependency install | uv sync exit code=0 | `uv sync` | PASS |
| P0-2: Module import | import raises no error | `uv run python -c "import tcd; print(tcd.__version__)"` → 0.1.0 | PASS |
| P0-3: Compile check | no errors | `uv run tcd --help` outputs normally | PASS |
| P0-4: Unit tests | pytest exit code=0 | `uv run pytest tests/ -v` → 61/61 passed | PASS |
| P0-5: CLI entry point | includes subcommands | `uv run tcd --help` → lists 10 subcommands | PASS |
| P0-6: Subcommand help | all --help OK | verified --help for each of 9 subcommands | PASS |

### P1 Verification Results (E2E with real Codex CLI)
| Test Item | Pass Criteria | Actual Command | Result |
|--------|---------|---------|------|
| P1-1a: Create/destroy session | exists correct | pytest test_tmux_adapter | PASS |
| P1-1b: send_keys short text | capture contains content | pytest test_tmux_adapter | PASS |
| P1-1c: send_long_text long text | capture contains content | pytest test_tmux_adapter | PASS |
| P1-1d: capture_pane | returns non-empty | pytest test_tmux_adapter | PASS |
| P1-2a: Create Job | exit code=0, has hex ID | `tcd start -p codex -m "..."` → Job 03439993 | PASS |
| P1-2b: JSON persistence | JSON parseable | `cat ~/.tcd/jobs/03439993.json \| python3 -m json.tool` | PASS |
| P1-2c: Job list | contains ID and status | `tcd jobs` → table output | PASS |
| P1-2d: Job list JSON | valid JSON array | `tcd jobs --json \| python3 -m json.tool` | PASS |
| P1-2e: Job status | shows status/turn_count | `tcd status 03439993` | PASS |
| P1-2f: Job status JSON | valid JSON | `tcd status 03439993 --json` → contains all fields | PASS |
| P1-2g: Kill Job | kill → status=failed | `tcd kill 03439993` + `tcd status` → failed | PASS |
| P1-2h: Clean Jobs | JSON deleted | `tcd clean` + `tcd jobs` → No jobs | PASS |
| P1-2i: Invalid ID error | exit code≠0, friendly message | `tcd status nonexistent123` → "not found" | PASS |
| P1-3a: Codex startup | tmux session exists | `tmux has-session -t tcd-codex-03439993` → 0 | PASS |
| P1-3b: Completion detection | check exit=0, signal exists | `tcd check` → 0, `.turn-complete` exists | PASS |
| P1-3c: Response collection | no ANSI sequences | `tcd output` + python3 ANSI check → 0 sequences | PASS |
| P1-3d: Session parsing | output contains summary | `tcd output` → contains Codex reply | PASS |
| P1-4a: Send follow-up instruction | turn_count increments | `tcd send "..."` → turn_count: 1→2 | PASS |
| P1-4b: Detection after send | idle exit=0 | `tcd wait` → 0, `tcd check` → 0 | PASS |
| P1-5a: wait normal completion | exit=0 | `tcd wait --timeout 30` → 0 | PASS |
| P1-5b: wait timeout | exit=2 | `tcd wait --timeout 1` → 2 | PASS |
| P1-5c: attach | no error | manually verified (skipped, requires interaction) | SKIP |

### Summary
- P0: 6/6 PASS
- P1: 21/22 PASS, 1 SKIP (P1-5c attach requires manual interaction)
- Total fixes: 3 (provider registration, send_keys escaping, TUI wait)
- No oscillation (fix A breaking B)

---
## Round 4 — 2026-03-02 Code Review Fixes

Phase: REVIEW → Fix
Trigger: code-review skill identified 4 critical issues

### Fix Details
| Issue | File | Fix |
|------|------|---------|
| file_path not validated + resource leak | cli.py:297 | Changed to `with open()` + mutual exclusion check |
| build_script_command injection | tmux_adapter.py:160 | Used `shlex.quote()` to escape log_file |
| notify_hook path injection | codex.py:47 | Changed to `json.dumps()` to serialize path list |
| send_long_text temp file leak | tmux_adapter.py:106 | Changed to `finally` block for cleanup |

### Verification Results
- `uv run pytest tests/ -v` → 62/62 passed (including new path quoting tests)
- No regression

---
## Round 5 — 2026-03-02 Phase 2–4 Full Implementation

Phase: P0 + P1 (Phase 2 Claude, Phase 3 Gemini, Phase 4 SDK + README)
Trigger: All steps of Phase 2–4 completed

### Implementation Details

#### Phase 2: Claude Code Provider
- `src/tcd/marker_detector.py` — shared TCD_REQ/TCD_DONE marker protocol
- `src/tcd/idle_detector.py` — idle detection (capture-pane comparison)
- `src/tcd/providers/claude.py` — Claude Code provider (with `--dangerously-skip-permissions`, `unset CLAUDECODE`)
- Key fix: `send_text()` for marker prompts containing newlines switched to `send_long_text()` (bracketed paste `-p`) to resolve Ink TUI swallowing Enter
- `tui_ready_indicator = "❯"`, trust dialog handled automatically

#### Phase 3: Gemini CLI Provider
- `src/tcd/providers/gemini.py` — Gemini CLI provider (`--yolo` mode)
- `tui_ready_indicator = "Type your message"`, trust dialog + second wait after restart
- Key fix: cli.py added Gemini trust dialog detection ("Do you trust the files in this folder") + second wait after restart

#### Phase 4: Python SDK + README
- `src/tcd/sdk.py` — TCD class (start/check/wait/output/send/jobs/kill/clean)
- `README.md` — full documentation (CLI reference, SDK examples, Provider support table, architecture diagram)

### Key Bug Fixes
| Issue | Root Cause | Fix |
|------|------|------|
| Claude prompt not submitted | `send-keys -l` sends newline character by character = Enter, but 0.2s delay was insufficient | Texts with newlines switched to `paste-buffer -p` (bracketed paste) |
| Gemini prompt lost | After trust dialog restart, TUI ready indicator appears a second time but prompt was sent on the first occurrence | Added trust dialog detection + second wait after restart |

### Test Results
| Test Item | Result |
|--------|------|
| Full unit tests | 119/119 PASS |
| Codex E2E (verified in Round 3) | PASS |
| Claude E2E: start → check → output → send → check → kill | PASS |
| Gemini E2E: start → check → send → check → kill | PASS |
| Python SDK tests | 18/18 PASS |

### Code Review Fixes
| Issue | Fix |
|------|------|
| gemini.py `_extract_response` dead code | Removed unused `in_response` logic |
| SDK `send_text` return value unchecked | start/send now check return value; failure raises exception |
| SDK check `except Exception` too broad | Changed to `except (OSError, ValueError, KeyError)` |

### Final Verification Summary
- P0: 6/6 PASS (dependency install, module import, compile check, 119 tests, CLI entry, subcommand help)
- P1: All 3 Provider E2E verifications passed
- Python SDK: 18 tests passed
- Total tests: 119
- Code review: passed (critical issues fixed)
