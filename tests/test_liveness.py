"""Liveness classification.

The two verbatim screens below are from 2026-09-17, when six Codex jobs sat
dead for an hour while `tcd check --json` reported `state: "working"` and a
grep for "rate limit" over the logs found nothing (the log had stopped being
written; the grep only ever saw history). They are the regression anchor, but
they are *instances* — the thing under test is "does this job show signs of
progress", not "does it say usage limit".
"""

from __future__ import annotations

import pytest

from tcd.liveness import (
    AWAITING_INPUT_SIGNS,
    BLOCKED_SIGNS,
    DEFAULT_SILENCE_SECONDS,
    DEFAULT_WORKING_MARKERS,
    PANE_STATES,
    assess,
    classify_pane,
    extract_recovery_time,
    warning_for,
)
from tcd.provider import PROVIDER_ERROR_SIGNS
from tcd.providers.codex import CodexProvider

CODEX_MARKERS = CodexProvider.working_markers
CODEX_PLACEHOLDER = (CodexProvider.composer_placeholder,)

# --- verbatim fixtures, 2026-09-17 ---------------------------------------

USAGE_LIMIT_PANE = """\
• Ran npm test -w @tms/server
  └ 116 passed, 3 failed

■ You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to
purchase more credits or try again at Sep 19th, 2026 8:27 PM.

› Ask Codex to do anything
"""

RATE_LIMIT_CHOICE_PANE = """\
  Approaching rate limits
  Switch to gpt-5.6-luna for lower credit usage?
› 1. Switch to gpt-5.6-luna                 Fast and affordable agentic coding model.
  2. Keep current model
  3. Keep current model (never show again)  Hide future rate limit reminders about switching models.
  Press enter to confirm or esc to go back
"""

WORKING_PANE = """\
• Edited packages/server/src/modules/finance/service.ts (+40 lines)

  Working (2m 13s • esc to interrupt)
"""

IDLE_PANE = """\
• Ran npm run typecheck
  └ 0 errors

› Ask Codex to do anything
"""

UNSEEN_PANE = """\
■ Something entirely new happened that nobody has taught this classifier about.
  The agent is not saying anything we recognise.
"""


def classify(pane, **kw):
    return classify_pane(
        pane,
        working_markers=CODEX_MARKERS,
        composer_placeholders=CODEX_PLACEHOLDER,
        **kw,
    )


# --- screen classification ------------------------------------------------

def test_usage_limit_screen_is_blocked_not_working():
    v = classify(USAGE_LIMIT_PANE)
    assert v.state == "blocked"
    assert "usage limit" in (v.reason or "")
    assert v.evidence


def test_usage_limit_screen_carries_the_recovery_time():
    # The one thing the driver actually needs in order to decide wait-or-pay.
    assert classify(USAGE_LIMIT_PANE).recover_at == "Sep 19th, 2026 8:27 PM"


def test_blocked_beats_a_trailing_idle_composer():
    # The real screen had BOTH the notice and the composer placeholder on it.
    # Reporting "idle, turn over" would have hidden the reason.
    assert CodexProvider.composer_placeholder in USAGE_LIMIT_PANE
    assert classify(USAGE_LIMIT_PANE).state == "blocked"


def test_keypress_prompt_is_awaiting_input():
    v = classify(RATE_LIMIT_CHOICE_PANE)
    assert v.state == "awaiting_input"
    assert any("1." in line or "confirm" in line.lower() for line in v.evidence)


def test_working_screen_is_working():
    assert classify(WORKING_PANE).state == "working"


def test_idle_composer_is_idle_prompt():
    assert classify(IDLE_PANE).state == "idle_prompt"


def test_unrecognised_screen_is_unknown_never_working():
    v = classify(UNSEEN_PANE)
    assert v.state == "unknown"
    assert v.evidence, "an unknown screen must carry its text out to a human"


def test_empty_pane_is_unknown():
    for pane in (None, "", "   \n \n"):
        assert classify(pane).state == "unknown"


def test_every_state_is_reachable_and_declared():
    seen = {
        classify(p).state
        for p in (USAGE_LIMIT_PANE, RATE_LIMIT_CHOICE_PANE, WORKING_PANE, IDLE_PANE, UNSEEN_PANE)
    }
    assert seen == set(PANE_STATES)


@pytest.mark.parametrize(
    "line",
    [
        "■ You've hit your usage limit.",
        "You have hit your usage limit for this week",
        "Error: quota exceeded for this organization",
        "purchase more credits to continue",
        "rate limit exceeded (429)",
        '{"type": "authentication_error"}',
    ],
)
def test_blocked_matching_has_slack_for_reworded_notices(line):
    # Wording changes without notice; several spellings per condition.
    assert classify(f"• some work\n{line}\n").state == "blocked"


@pytest.mark.parametrize(
    "when,text",
    [
        ("Sep 19th, 2026 8:27 PM", "try again at Sep 19th, 2026 8:27 PM."),
        ("5 minutes", "Please try again in 5 minutes."),
        ("midnight UTC", "Your limit resets at midnight UTC"),
    ],
)
def test_recovery_time_shapes(when, text):
    assert extract_recovery_time(text) == when


# --- feature strings must not match the TUI's own chrome -------------------

# Lines a healthy Codex job draws, or that an agent writes while working. None
# of them may match a "something is wrong" feature. The corpus is the door:
# a new pattern that collides with ordinary output fails here rather than in
# production, which is how the 2026-09-18 misfire should have been caught.
TUI_CHROME = [
    "  … +429 lines (ctrl + t to view transcript)",
    "  … +500 lines (ctrl + t to view transcript)",
    "• Working (1m 30s • esc to interrupt)",
    "• Viewed Image",
    "  └ /tmp/field-fix-b2/vehicle-audit/vehicles-actual.png",
    "• Edited src/api.ts (+429 lines)",
    "  └ 116 passed, 3 failed",
    "Total tokens used: 429000",
    "  2. Keep current model",
    "  1. read the spec",
    "> quoted markdown line",
    ">",
    "  retry logic: give up after max retries",
    "giving up on the regex approach, trying a parser",
    "docs say: please log in to continue",
    "const RATE_LIMIT = 429;",
    'test("handles 429", ...)',
    "approve this PR before merging",
    "⚠ Model metadata for `gpt-6-sol` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.",
    "warning: Model metadata for `deepseek-v4.1-flash` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.",
]

STOP_TABLES = (
    ("BLOCKED_SIGNS", BLOCKED_SIGNS),
    ("AWAITING_INPUT_SIGNS", AWAITING_INPUT_SIGNS),
    ("PROVIDER_ERROR_SIGNS", PROVIDER_ERROR_SIGNS),
)


@pytest.mark.parametrize("line", TUI_CHROME)
def test_no_stop_feature_matches_ordinary_output(line):
    for table_name, table in STOP_TABLES:
        for pattern, reason in table:
            assert not pattern.search(line), (
                f"{table_name} pattern {pattern.pattern!r} ({reason}) matches ordinary "
                f"output {line!r} — a feature string this cheap will misfire in production"
            )


def test_tui_folded_line_count_is_not_a_rate_limit():
    """The live misfire of 2026-09-18, verbatim.

    `\\b429\\b` matched Codex's folded-transcript chrome — "… +429 lines" is a
    line count, not an HTTP status — and the watcher returned 9 (blocked) with
    `silence_s: 0` on a job that was visibly working.
    """
    pane = (
        "• Viewed Image\n"
        "  └ /tmp/field-fix-b2/vehicle-audit/vehicles-actual.png\n"
        "  … +429 lines (ctrl + t to view transcript)\n"
        "• Working (1m 30s • esc to interrupt)\n"
    )
    v = classify(pane)
    assert v.state == "working", f"expected working, got {v.state}: {v.reason}"
    assert v.state != "blocked"


def test_contradiction_guard_is_on_by_default():
    """No-argument callers must get the guard too.

    The first version took `working_markers=()`, so the contradiction guard —
    the entire second line of defence — did nothing unless the caller
    remembered to opt in. A safety check whose default is "off" is a safety
    check that depends on the caller's memory.
    """
    pane = "■ quota exceeded for this org\n• Working (2m • esc to interrupt)\n"
    assert classify_pane(pane).state == "unknown"  # no kwargs at all
    assert assess("nojob", pane).pane.state == "unknown"


def test_turning_the_guard_off_must_be_explicit():
    pane = "■ quota exceeded for this org\n• Working (2m • esc to interrupt)\n"
    assert classify_pane(pane, working_markers=()).state == "blocked"


def test_default_markers_cover_every_provider():
    """If a provider learns a new activity marker, the default must learn it.

    Otherwise the guard silently narrows for exactly the screens that provider
    draws.
    """
    from tcd.provider import get_provider, list_providers

    known = {m.casefold() for m in DEFAULT_WORKING_MARKERS}
    for name in list_providers():
        for marker in get_provider(name).working_markers:
            assert marker.casefold() in known, (
                f"provider {name!r} reports activity as {marker!r}, which "
                f"DEFAULT_WORKING_MARKERS does not know — the contradiction "
                f"guard would not fire on that provider's screens"
            )


def test_a_stop_sign_next_to_a_live_spinner_is_a_contradiction_not_a_verdict():
    # Second line of defence: even if some future feature string does match
    # ordinary output, a live activity indicator on the same screen must stop
    # it from being reported as a verdict.
    pane = "■ quota exceeded for this organization\n• Working (2m • esc to interrupt)\n"
    v = classify(pane)
    assert v.state == "unknown"
    assert "contradictory" in (v.reason or "")
    assert any("Working" in line for line in v.evidence), "the human needs both lines"


def test_a_lone_numbered_line_is_not_a_menu():
    # A prompt pasted into the composer can start with "1.".
    pane = "› 1. 先读设计文档，再写测试\n"
    assert classify(pane).state != "awaiting_input"


def test_ordinary_numbered_list_is_not_a_menu():
    # Agent prose containing "1. …" must not read as a prompt awaiting a key.
    pane = "Here is the plan:\n  1. read the spec\n  2. write the test\n\n  Working (3s • esc to interrupt)\n"
    assert classify(pane).state == "working"


def test_only_the_tail_is_classified():
    # An agent that printed the words earlier and then went back to work is
    # working; the live status area is what says what it is doing now.
    pane = "■ You've hit your usage limit.\n" + "• step\n" * 40 + "  Working (1m • esc to interrupt)\n"
    assert classify(pane).state == "working"


# --- crossing with movement ----------------------------------------------

def assess_pane(pane, silence_s, tmp_path, monkeypatch, threshold=DEFAULT_SILENCE_SECONDS):
    """Assess against a log file whose mtime we control."""
    import os
    import time

    log = tmp_path / "job1.log"
    log.write_text("x")
    events = tmp_path / "job1.events.jsonl"
    events.write_text("{}")
    stamp = time.time() - silence_s
    for p in (log, events):
        os.utime(p, (stamp, stamp))
    monkeypatch.setattr("tcd.liveness.job_log_path", lambda job_id: log)
    monkeypatch.setattr("tcd.liveness.job_events_path", lambda job_id: events)
    return assess(
        "job1",
        pane,
        working_markers=CODEX_MARKERS,
        composer_placeholders=CODEX_PLACEHOLDER,
        silence_threshold_s=threshold,
    )


def test_tonight_would_have_been_caught(tmp_path, monkeypatch):
    # Five jobs were silent for 51-60 minutes with this screen up.
    live = assess_pane(USAGE_LIMIT_PANE, 55 * 60, tmp_path, monkeypatch)
    assert live.verdict == "blocked"
    assert live.needs_human
    assert live.silent
    assert live.pane.recover_at == "Sep 19th, 2026 8:27 PM"


def test_keypress_prompt_silent_versus_fresh(tmp_path, monkeypatch):
    stuck = assess_pane(RATE_LIMIT_CHOICE_PANE, 45 * 60, tmp_path, monkeypatch)
    assert stuck.verdict == "awaiting_input"
    assert stuck.needs_human

    # Same screen, drawn two seconds ago: not yet a verdict, look again later.
    fresh = assess_pane(RATE_LIMIT_CHOICE_PANE, 2, tmp_path, monkeypatch)
    assert fresh.verdict == "awaiting_input_fresh"
    assert not fresh.needs_human


def test_silent_idle_prompt_is_not_called_dead(tmp_path, monkeypatch):
    # A job that just handed in its work looks exactly like this.
    live = assess_pane(IDLE_PANE, 40 * 60, tmp_path, monkeypatch)
    assert live.verdict == "idle_silent"
    assert not live.needs_human


def test_quiet_test_suite_is_not_stalled_below_its_threshold(tmp_path, monkeypatch):
    # 12 quiet minutes is normal for a full test run and abnormal for a
    # read-only analysis job — hence a parameter, not a constant.
    tolerant = assess_pane(WORKING_PANE, 12 * 60, tmp_path, monkeypatch, threshold=30 * 60)
    assert tolerant.verdict == "progressing"
    strict = assess_pane(WORKING_PANE, 12 * 60, tmp_path, monkeypatch, threshold=5 * 60)
    assert strict.verdict == "stalled"


def test_unknown_silent_reports_the_screen(tmp_path, monkeypatch):
    live = assess_pane(UNSEEN_PANE, 40 * 60, tmp_path, monkeypatch)
    assert live.verdict == "unknown_silent"
    assert live.needs_human, "an unreadable, silent screen is never 'running'"
    assert "entirely new" in live.to_dict()["pane_tail"]


def test_our_own_polling_cannot_prove_the_job_alive(tmp_path, monkeypatch):
    """Caught live on job d7a61257, 2026-09-18 00:28.

    `tcd check` emits a `job.checked` event every poll, so the event log's
    mtime is always ~0s — a dead job polled often enough would read as busy.
    Only the job log, which the agent's own terminal writes, may vote.
    """
    import os
    import time

    log = tmp_path / "job1.log"
    log.write_text("x")
    stale = time.time() - 55 * 60
    os.utime(log, (stale, stale))
    events = tmp_path / "job1.events.jsonl"
    events.write_text("{}")  # written just now, by us
    monkeypatch.setattr("tcd.liveness.job_log_path", lambda job_id: log)
    monkeypatch.setattr("tcd.liveness.job_events_path", lambda job_id: events)

    live = assess("job1", USAGE_LIMIT_PANE, working_markers=CODEX_MARKERS)
    assert live.event_silence_s < 5, "fixture should mimic a just-written event log"
    assert live.silent, "fresh events must not mask a silent job log"
    assert live.verdict == "blocked"


def test_missing_log_is_not_evidence_of_silence(tmp_path, monkeypatch):
    monkeypatch.setattr("tcd.liveness.job_log_path", lambda job_id: tmp_path / "nope.log")
    monkeypatch.setattr("tcd.liveness.job_events_path", lambda job_id: tmp_path / "nope.jsonl")
    live = assess("job1", WORKING_PANE, working_markers=CODEX_MARKERS)
    assert live.silence_s is None
    assert not live.silent
    assert live.verdict == "progressing"


def test_every_combination_has_a_row(tmp_path, monkeypatch):
    for pane in (USAGE_LIMIT_PANE, RATE_LIMIT_CHOICE_PANE, WORKING_PANE, IDLE_PANE, UNSEEN_PANE):
        for silence in (2, 90 * 60):
            live = assess_pane(pane, silence, tmp_path, monkeypatch)
            assert live.verdict and live.advice


# --- warning rendering ----------------------------------------------------

def test_warning_codes(tmp_path, monkeypatch):
    cases = {
        USAGE_LIMIT_PANE: ("BLOCKED", "error"),
        RATE_LIMIT_CHOICE_PANE: ("AWAITING_INPUT", "error"),
        UNSEEN_PANE: ("UNKNOWN_PANE", "error"),
        IDLE_PANE: ("NO_PROGRESS", "warn"),
    }
    for pane, (code, severity) in cases.items():
        hit = warning_for(assess_pane(pane, 60 * 60, tmp_path, monkeypatch))
        assert hit is not None
        assert (hit[0], hit[2]) == (code, severity)


def test_healthy_job_raises_no_warning(tmp_path, monkeypatch):
    assert warning_for(assess_pane(WORKING_PANE, 5, tmp_path, monkeypatch)) is None


def test_blocked_warning_message_carries_recovery_time(tmp_path, monkeypatch):
    _, message, _ = warning_for(assess_pane(USAGE_LIMIT_PANE, 60 * 60, tmp_path, monkeypatch))
    assert "Sep 19th, 2026 8:27 PM" in message
