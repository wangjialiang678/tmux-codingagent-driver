# Changelog

## v0.4.0 — 2026-08-03

Data-safety release, from the first review of two months of sustained
production use rather than of tcd's own development. Full write-up in
[docs/workflow-issues.md](docs/workflow-issues.md#2026-08-production-review-juneaugust-usage).

### Breaking

- **`tcd kill` no longer discards a worktree that still holds work.** It ran
  `git worktree remove --force`, throwing away whatever the agent had not
  committed — and agents forgetting to commit is a failure mode this project
  already documented. Kill now keeps a worktree with uncommitted changes or
  unmerged commits and prints where it is. Pass `--force` for the old
  behaviour.
- **`--sandbox` is rejected by providers that ignore it.** Only Codex ever
  applied it, but `tcd start` accepted it for any provider and echoed a
  reassuring `Sandbox: ...` line. Providers now declare `supports_sandbox`, and
  `claude` / `gemini` error out instead of silently dropping the flag.
- **`tcd clean` skips jobs that still own resources.** The job record is the
  only pointer to a worktree path and an auto-stash ref; deleting it stranded
  them. Use `--force` to clean anyway.

### Your uncommitted work comes back

Six orphaned `tcd: auto-stash before worktree` stashes were found across two
repos driven by tcd in July, one holding 7 files and +513 lines.

- `stash_pop` popped the **top of the stack** while the job's own stash SHA sat
  unused in `worktree_stash_ref`. The stash stack is repo-wide, so two
  concurrent worktree jobs handed each other's changes back. The ref is now
  re-resolved to its current `stash@{n}` position at pop time
  (`find_stash_selector`), and a ref that is gone fails loudly instead of
  popping whatever else is there.
- The stash was restored only by `tcd merge`. `tcd kill` restores it too, with
  `worktree_stash_restored` making the restore idempotent across both paths.

### Job state is reconciled instead of assumed

`tcd jobs` was reporting 54 `running` jobs against 9 live tmux sessions, the
oldest claiming 30 days of runtime.

- `tcd jobs` reconciles against a single `tmux list-sessions` call before
  listing; `--no-reconcile` opts out.
- `_elapsed` stops the clock at `completed_at` instead of measuring against
  now.

### Providers: fixes applied to the class, not the instance

- **Launch hardening is now a base-class default.** v0.3.2's TUI stability
  gating and prompt-delivery verification were set on `CodexProvider` only, so
  `claude`, `gemini`, and any future provider silently re-inherited the
  dropped-prompt bug. Safe values are the defaults; providers opt out.
- **`Provider.detect_provider_error`** surfaces `PROVIDER_ERROR` from
  `tcd check --json` when the pane shows a fatal API failure. A model-id typo
  (400 `invalid_request_error`) and an upstream 503 reconnect loop both used to
  report `state: working` until the timeout expired.
- **Claude's session lookup is scoped to the job.** It returned the newest
  `.jsonl` anywhere under `~/.claude/projects/`, which is the orchestrator's
  own transcript whenever tcd is driven from inside a Claude Code session. Now
  filtered by the job's project directory and start time.
- `--provider` choices come from the registry instead of a hardcoded list, and
  activity extraction no longer strips chrome using Codex-only prefixes for
  every provider.

### Tests

290 passing (was 253), including the concurrency case that proves job A's
stash survives job B's merge.

---

## v0.3.3 — 2026-08-02

Follow-up submission hardening, plus a real version number.

### `tcd send` no longer silently leaves the follow-up queued

- Claude Code can accept pasted text but leave it in its queued input state
  after the first Enter, showing `Press up to edit queued messages` — the
  follow-up looked delivered but the agent never saw it. New
  `tcd/submission_recovery.py` inspects the pane right after a send, and when
  the provider reports the queued-message hint (providers opt in via
  `has_queued_message_notice(pane)`), sends one extra Enter and records
  `job.message_submit_retry` in the event log.
- README now states plainly that for marker providers (`claude`, `gemini`)
  `tcd check` exit 0 means **the current turn is idle**, not that the job is
  finished — the tmux session deliberately stays `running` for follow-ups.

### Version is now reported and single-sourced

- `tcd --version` / `tcd -V` exists (`-v` remains verbosity).
- The version lives in `src/tcd/__init__.py` and `pyproject.toml` reads it via
  `[tool.hatch.version]`, so the two can no longer drift. Previously both said
  `0.1.0` while the docs and changelog were on `0.3.x`, and the only way to tell
  which build you had installed was `git log`.

---

## v0.3.2 — 2026-06-20

Codex launch reliability fixes. Every tcd-driven Codex job in a fresh directory
(which is *every* `--worktree` job) was silently failing on turn 0 and falling
back to other agents. Four distinct, stacked causes were found and fixed; the
end-to-end path (`start` → develop → commit → `merge`) now works headlessly.

### Codex no longer dies on launch (auto-update)

- Codex's launch-time auto-updater would run `npm install -g @openai/codex`,
  print "Please restart Codex", and exit **before the agent ever started** —
  whenever upstream published a new version (frequently), and N-ways in parallel
  batch mode. `build_launch_command` now passes
  `-c check_for_update_on_startup=false`.

### Codex no longer blocks on the trust dialog

- Current Codex asks "Do you trust the contents of this directory?" on first
  entry to any new directory. The readiness loop's trust-phrase list only knew
  the older "Do you trust the files in this folder" wording, so it never
  confirmed the dialog — the prompt was injected into the dialog and the job
  died. tcd now (a) pre-trusts the working directory via
  `-c projects."<cwd>".trust_level="trusted"` (canonical path) and (b) matches
  the broader "Do you trust" phrase and auto-confirms with Enter as a fallback.

### Codex no longer stalls on MCP startup

- Codex blocks the TUI from accepting input until **every** enabled MCP server
  finishes starting; a single slow one (e.g. `playwright` via `npx`) stalled
  startup for minutes, so the prompt landed in a not-yet-ready TUI and was
  dropped. tcd-driven jobs are headless coders that don't need most of the
  user's interactive MCP servers, so `build_launch_command` now enumerates them
  from the Codex config and disables each (`-c mcp_servers.<name>.enabled=false`)
  **except a keep-list** — default `context7` (live library docs), overridable
  via the `TCD_CODEX_MCP_KEEP` env var (comma-separated; empty = keep none).

### Prompt injection is now verified and resilient

- New shared module `tcd/readiness.py` holds the TUI-readiness and
  prompt-delivery logic that `cli.py` (`tcd start`) and `sdk.py` previously
  duplicated — they had drifted, so SDK-side fixes never reached the CLI path.
- `wait_for_tui` adds optional **pane-stability gating** (`tui_stable_secs`,
  2.5s for Codex): it waits for the pane to stop changing before declaring the
  TUI ready, instead of firing on a readiness indicator that appears in a
  startup banner. It captures the **visible screen only** (`-S 0`) so a
  dismissed dialog lingering in scrollback can't be re-matched forever.
- `verify_prompt_delivery` (`verify_prompt_delivery=True` for Codex) confirms
  the prompt was received (echoed text / "esc to interrupt") and resends up to
  twice if it was dropped. New events: `job.prompt_resend`,
  `job.prompt_confirmed`, `job.prompt_unconfirmed`.

### Tests

- Added Codex launch-command tests (auto-update off, pre-trust, MCP disable),
  SDK resend/no-resend tests, and readiness coverage. Total: 246 passing.

---

## v0.3.1 — 2026-03-09

Worktree robustness fix round: 5 commits focused on merge safety and STALL false-positive issues. See `docs/workflow-issues.md` for details.

### Worktree Merge Robustness

- **Prevent false success**: `merge_worktree` now validates a clean working tree before merging, detects noop merges (no new commits on branch), and marks them explicitly — eliminating "reported success but nothing was merged"
- **Concurrent cleanup defense**: `remove_worktree` returns a bool status; repeated calls are safe; all callers now handle cleanup failure paths uniformly
- **SDK stash_pop fix**: corrected the timing of stash_pop during merge to prevent dirty working tree from swallowing uncommitted user changes
- **`worktree_repo_root` validation**: `cli.py` / `sdk.py` now validate the path before use; unified the timing of status persistence

### Merge Pre-check

- `tcd merge` runs a conflict pre-detection dry-run (`git merge --no-commit --no-ff`) before the actual merge, reporting conflicts upfront rather than failing mid-way
- Worktree is preserved on pre-check failure to allow manual intervention

### Diagnostics: STALL False-Positive Fix

- `STALL` rule now includes a "turn in progress" exemption: no false positive when `turn_count > 0` and `elapsed` is below the new threshold
- `TURN0_STUCK` threshold retained at 120s; adjusted STALL threshold reduces false kills on long-running tasks
- Added 28 new diagnostic test cases covering STALL boundary conditions

### Documentation

- Added `docs/workflow-issues.md`: consolidates worktree production issues and fix paths, serving as a regression baseline
- Added `docs/feature-request-parallel-batch-start.md`: v0.4.0 proposal for `tcd batch` parallel job launch
- Added `docs/example-batch-tasks.json`: sample task definitions for the batch command

### Tests

- Added worktree CLI/SDK tests + diagnostic tests (total diagnostics +28)
- Total test count: 223 → 280+

---

## v0.3.0 — 2026-03-05

Git worktree parallel isolation, incremental output, activity extraction, and logging system.

### Git Worktree Support

- Added `src/tcd/worktree.py`: git worktree primitives (create/remove/merge/delete_branch)
- `tcd start --worktree [--wt-name NAME]`: start a job inside an isolated worktree
- `tcd merge <job_id> [--squash] [--no-cleanup]`: merge worktree branch back to the main branch
- SDK `start()` adds `worktree`/`worktree_name` parameters and a new `merge_worktree()` method
- `kill` automatically cleans up the worktree
- Job model adds `worktree_path`/`worktree_branch` fields

### Incremental Output and Activity Extraction

- `tcd output --tail N`: output only the last N lines
- `tcd output --since-line N`: incremental polling (output lines after line N)
- `tcd check --json` adds an `activity` field: meaningful action lines extracted from scrollback via regex (Edited, Created, Ran, etc.)
- Line count exposed via stderr `__lines_total=N` for external poll tracking

### Logging System

- `tcd -v` (INFO) / `tcd -vv` (DEBUG) verbose logging to stderr
- INFO level covers all key flows: start, check, send, kill, merge, refresh_status
- WARNING level covers abnormal paths: TUI timeout, context_limit, merge conflicts, worktree cleanup failure, session disappearance

### Default Sandbox Change

- Codex default sandbox changed from `workspace-write` to `danger-full-access`
- Diagnostic rule R1 only warns when `workspace-write`/`workspace-read` is explicitly specified

### Tests

- Added 32 test cases (worktree primitives 12 + SDK integration 12 + CLI 7 + diagnostics 1)
- Total test count: 191 → 223

---

## v0.2.0 — 2026-03-05

Event log and diagnostics system, significantly improving tcd observability. See `docs/prd-event-log.md` for details.

### Phase 1: Event Log

- Added `src/tcd/event_log.py`: append-only JSONL event log (emit + load_events)
- Instrumented 7 event types at key paths in cli.py / sdk.py: job.created, job.tui_ready, job.tui_timeout, job.prompt_sent, job.checked, job.turn_complete, job.message_sent, job.killed
- Added `tcd log` command: view event log with `--tail N` and `--event <type>` filtering
- `config.py` adds `job_events_path()`; clean command now also removes `.events.jsonl` files

### Phase 2: Diagnostics Engine

- Added `src/tcd/diagnostics.py`: 4 rules for automatic issue detection
  - SANDBOX_MISMATCH: prompt contains write intent but sandbox mode is workspace-write
  - STALL: 4 consecutive checks with no state change and elapsed > 60s
  - PERMISSION_ERROR: permission denied message found in pane output
  - TURN0_STUCK: turn 0 remains working for more than 120s
- `tcd check --json`: outputs structured JSON (state, elapsed_s, turn_count, warnings, pane_tail)
- SDK adds `check_with_diagnostics()` method and `DiagnosticCheckResult` dataclass

### Phase 3: Skill Update

- `codex-worker` Skill updated to poll with `tcd check --json`
- Added automatic response strategies for 4 warning types
- Filesystem layout updated to include `.events.jsonl`; other commands updated to include `tcd log`

### Phase 4: Token Recording

- `CompletionResult` adds a `tokens` field
- Codex provider `detect_completion()` parses token_count from the NDJSON session file
- `Job` model adds `total_tokens` field, accumulated per turn
- `tcd status` and `tcd status --json` display cumulative token usage
- `job.turn_complete` event records token data

### Tests

- Added 23 test cases (event log, diagnostic rules, token accumulation, CLI JSON output, etc.)
- Total test count: 160 → 183

---

## v0.1.1 — 2026-03-05

Code review fix round: fixes for 3 Critical + 4 Major + 2 Minor issues.

### Critical Fixes

- **C-2**: Claude/Gemini multi-turn session turn_count increment — previously turn_count was always 0, causing req_id collisions
- **C-3**: Provider launch command injection protection — model parameter now validated against a regex whitelist + escaped with shlex.quote
- **C-1**: Marker detection changed from substring match to strict full-line match, preventing prefix false positives

### Major Fixes

- **M-1**: Gemini response extraction no longer includes the user prompt text
- **M-4**: Session disappearance now distinguishes completed (normal exit) from failed (abnormal termination)
- **M-6**: `--sandbox` parameter is now properly threaded from CLI to the Codex provider launch command (was dead code)

### Minor Fixes

- **m-1**: CLI start/send now checks the send_text return value and marks the job as failed on failure
- **m-3**: Narrowed broad `except Exception` clauses to specific exception types and added logger.exception logging

### Tests

- Added 13 test cases (model injection, strict marker matching, session disappearance state distinction, etc.)
- Total test count: 147 → 160

---

## v0.1.0 — 2026-03-02

Initial release. Phases 1–4 fully complete, 119 unit tests passing, 3 provider E2E verifications passed.

### Phase 1: Core Framework + Codex Driver

- tmux Adapter: create/kill session, send_keys, send_long_text (bracketed paste), capture_pane
- Provider abstract base class + registry
- Codex Provider: notify-hook completion detection, JSONL session parsing
- Job management: JSON persistence, state machine (pending → running → completed/failed)
- Response Collector: session file → capture-pane → log fallback
- ANSI output cleanup (CSI/OSC/DCS/ESC sequence removal)
- CLI entry points: start, send, status, output, check, wait, jobs, attach, kill, clean

### Phase 2: Claude Code Driver

- Claude Code Provider: `--dangerously-skip-permissions`, `unset CLAUDECODE`
- Marker protocol: TCD_REQ/TCD_DONE injection and scanning
- Idle detection module (20s threshold)
- Trust dialog auto-handling ("Yes, I trust this folder")

### Phase 3: Gemini CLI Driver

- Gemini CLI Provider: `--yolo` mode
- Marker + idle detection (15s threshold)
- Trust dialog + restart-wait handling

### Phase 4: Python SDK + Documentation

- Python SDK: `from tcd import TCD` (start/check/wait/output/send/jobs/kill/clean)
- README.md: CLI reference, SDK examples, provider support table, architecture diagram

### Key Technical Decisions

- Multi-line text uses `paste-buffer -p` (bracketed paste) instead of `send-keys -l`, solving the Ink TUI newline-submit problem
- Three-layer completion detection: signal file → marker scan → idle detect
- Platform-adaptive `script` command (macOS vs Linux)
