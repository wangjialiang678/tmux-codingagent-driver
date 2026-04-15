# Fix Report (2026-03-05)

Executed against the orchestrator annotations and user checklist at the bottom of `docs/code-review.md`, applying each fix directly to the code and running after each item:

`UV_CACHE_DIR=.uv-cache uv run pytest tests/ -q`

## Fix Checklist Results

| Item | Result | Notes |
|---|---|---|
| C-2 | Fixed | Claude/Gemini now advance `turn_count` (idempotently) on `working -> idle/context_limit` transition, and `send` req_id is now based on current `turn_count` to avoid multi-turn collisions. |
| C-3 | Fixed | `model` parameter for `codex/claude/gemini` now has whitelist validation (`[a-zA-Z0-9._:/-]+`) and uses `shlex.quote` for shell escaping. |
| M-1 | Fixed | Gemini `_extract_between_markers` now extracts only content between the "first DONE (prompt echo)" and the "final DONE (model output)" for the same `req_id`, preventing the prompt from being treated as the reply. |
| M-4 | Fixed | `_refresh_status` in cli/sdk now distinguishes session disappearance: `turn_state=working` marks `failed`; otherwise marks `completed`. |
| m-1 | Fixed | CLI `start/send` now checks the return value of `tmux.send_text()`; on failure, persists `failed` + error message and exits. |
| m-3 | Fixed | Narrowed `except Exception`: `cli` now catches specific exceptions and records traceback; `gemini` signal read exceptions also use narrow catch + logging. |
| C-1 (optional) | Fixed | `scan_for_marker` changed from substring match to strict full-line match; prefix pattern now requires timestamp digits, preventing `turn 1` from matching `turn 10`. |
| M-6 | Not changed (as specified) | User explicitly stated "already fixed, no change needed". |

## Main Changed Files

- `src/tcd/cli.py`
- `src/tcd/sdk.py`
- `src/tcd/providers/codex.py`
- `src/tcd/providers/claude.py`
- `src/tcd/providers/gemini.py`
- `src/tcd/marker_detector.py`
- `tests/test_cli.py`
- `tests/test_sdk.py`
- `tests/test_codex_provider.py`
- `tests/test_claude_provider.py`
- `tests/test_gemini_provider.py`
- `tests/test_marker_detector.py`

## Test Record After Each Fix

1. After C-2: `143 passed, 4 failed`
2. After C-3: `146 passed, 4 failed`
3. After M-1: `146 passed, 4 failed`
4. After M-4: `150 passed, 4 failed`
5. After m-1: `152 passed, 4 failed`
6. After m-3: `152 passed, 4 failed`
7. After C-1 (optional): `156 passed, 4 failed`

## Remaining Failures (Environment-Dependent)

The consistently failing 4 items are all tmux adapter integration tests with the same failure point: the current environment cannot successfully create/operate tmux sessions (`tmux new-session` returns non-zero).

- `tests/test_tmux_adapter.py::test_create_and_kill_session`
- `tests/test_tmux_adapter.py::test_send_keys_and_capture`
- `tests/test_tmux_adapter.py::test_send_long_text`
- `tests/test_tmux_adapter.py::test_send_text_auto_selects`
