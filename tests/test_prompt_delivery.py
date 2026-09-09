"""Regression tests for two-phase prompt delivery verification.

Failure mode this guards (2026-09-07, Codex 0.153.4): a bracketed paste lands
in the composer but the trailing Enter is eaten while the TUI is still
digesting it. The prompt text is visible in the pane, so the old one-phase
check reported "delivered" and the job sat at turn 0 for 46 minutes.

Echoed prompt text proves the paste landed; only a provider working marker
proves a turn is running.
"""

from tcd.readiness import STATUS_BAR_MARKERS, verify_prompt_delivery

MARKERS = ("esc to interrupt", "working (")
PROMPT = "implement richpage block\nsecond line of the prompt"
INDICATOR = "›"
PLACEHOLDER = "Ask Codex to do anything"


class FakeTmux:
    """Pane state driven by what the caller does, like the real TUI.

    ``on_enter`` / ``on_text`` are the pane contents after that action, so a
    test can express "the turn only starts once an Enter arrives".
    """

    def __init__(self, pane, *, on_enter=None, on_text=None):
        self.pane = pane
        self._on_enter = on_enter
        self._on_text = on_text
        self.enters = 0
        self.texts = []

    def capture_pane(self, session, start_line="-", depth=None):
        return self.pane

    def send_enter(self, session):
        self.enters += 1
        if self._on_enter is not None:
            self.pane = self._on_enter
        return True

    def send_text(self, session, text):
        self.texts.append(text)
        if self._on_text is not None:
            self.pane = self._on_text
        return True


def test_pasted_but_not_submitted_is_recovered_with_bare_enter(monkeypatch):
    """Prompt visible, no turn running -> send Enter, never resend the prompt."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    # Composer shows the prompt; the turn only starts once an Enter arrives.
    tmux = FakeTmux(
        f"› {PROMPT.splitlines()[0]}\n100% context left · 3.2k tokens used",
        on_enter="Working (12s - esc to interrupt)\n› Ask Codex to do anything",
    )

    assert verify_prompt_delivery(
        tmux, "sess", "job1", PROMPT, markers=MARKERS,
        tui_ready_indicator=INDICATOR, composer_placeholder=PLACEHOLDER,
    ) is True
    assert tmux.enters >= 1, "should nudge with a bare Enter"
    assert tmux.texts == [], "must NOT resend the prompt (would duplicate it)"


def test_running_turn_is_not_disturbed(monkeypatch):
    """A turn already running needs neither Enter nor resend."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    tmux = FakeTmux("Working (3s - esc to interrupt)")

    assert verify_prompt_delivery(
        tmux, "sess", "job2", PROMPT, markers=MARKERS,
        tui_ready_indicator=INDICATOR, composer_placeholder=PLACEHOLDER,
    ) is True
    assert tmux.enters == 0
    assert tmux.texts == []


def test_first_observation_emits_prompt_confirmed(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    events = []
    monkeypatch.setattr("tcd.readiness.emit", lambda job_id, event, **data: events.append((event, data)))
    tmux = FakeTmux("Working (3s - esc to interrupt)\n› Ask Codex to do anything")

    assert verify_prompt_delivery(
        tmux, "sess", "job-first", PROMPT, markers=MARKERS,
        tui_ready_indicator=INDICATOR, composer_placeholder=PLACEHOLDER,
    ) is True
    assert ("job.prompt_confirmed", {"attempt": 0, "via": "first"}) in events


def test_composer_text_wins_over_working_marker(monkeypatch):
    """A stale activity marker cannot confirm text still in the composer."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    tmux = FakeTmux("Working (3s - esc to interrupt)\n› implement richpage block")

    assert verify_prompt_delivery(
        tmux, "sess", "job-stale", PROMPT, retries=0, markers=MARKERS,
        tui_ready_indicator=INDICATOR, composer_placeholder=PLACEHOLDER,
    ) is False
    assert tmux.enters > 0
    assert tmux.texts == []


def test_status_bar_marker_is_not_working_evidence():
    assert "tokens used" in STATUS_BAR_MARKERS
    assert "tokens used" not in MARKERS


def test_absent_prompt_is_resent(monkeypatch):
    """Nothing landed -> resend the prompt (the original failure mode)."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    tmux = FakeTmux("$ waiting for input", on_text=PROMPT + "\nWorking (1s - esc to interrupt)")

    verify_prompt_delivery(
        tmux, "sess", "job3", PROMPT, markers=MARKERS, retries=2,
        tui_ready_indicator=INDICATOR, composer_placeholder=PLACEHOLDER,
    )
    assert tmux.texts, "an absent prompt must be resent"
    assert tmux.enters == 0, "bare Enter is only for the pasted-not-submitted case"


def test_stuck_prompt_gives_up_without_duplicating(monkeypatch):
    """Enter never helps -> report failure, and resends stay bounded."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    tmux = FakeTmux(f"› {PROMPT.splitlines()[0]}")  # Enter never helps: composer stays stuck

    assert verify_prompt_delivery(
        tmux, "sess", "job4", PROMPT, markers=MARKERS, retries=2,
        tui_ready_indicator=INDICATOR, composer_placeholder=PLACEHOLDER,
    ) is False
    assert tmux.enters >= 1
    assert len(tmux.texts) <= 2, "resends must stay within the retry budget"


def test_observation_window_scales_with_utf8_bytes(monkeypatch):
    """Default observe budget is 4s + 1s/KiB, capped at 20s."""
    now = [0.0]
    monkeypatch.setattr("tcd.readiness.time.time", lambda: now[0])
    monkeypatch.setattr("tcd.readiness.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    tmux = FakeTmux("waiting")
    verify_prompt_delivery(tmux, "sess", "job-window", "中" * 2048, retries=0, markers=MARKERS)

    assert now[0] >= 10.0  # 6144 UTF-8 bytes => at least a 10-second observation
