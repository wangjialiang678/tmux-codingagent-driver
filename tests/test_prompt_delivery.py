"""Regression tests for two-phase prompt delivery verification.

Failure mode this guards (2026-09-07, Codex 0.153.4): a bracketed paste lands
in the composer but the trailing Enter is eaten while the TUI is still
digesting it. The prompt text is visible in the pane, so the old one-phase
check reported "delivered" and the job sat at turn 0 for 46 minutes.

Echoed prompt text proves the paste landed; only a provider working marker
proves a turn is running.
"""

from tcd.readiness import verify_prompt_delivery

MARKERS = ("esc to interrupt", "working (")
PROMPT = "implement richpage block\nsecond line of the prompt"


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
    tmux = FakeTmux(PROMPT, on_enter="Working (12s - esc to interrupt)")

    assert verify_prompt_delivery(
        tmux, "sess", "job1", PROMPT, markers=MARKERS
    ) is True
    assert tmux.enters >= 1, "should nudge with a bare Enter"
    assert tmux.texts == [], "must NOT resend the prompt (would duplicate it)"


def test_running_turn_is_not_disturbed(monkeypatch):
    """A turn already running needs neither Enter nor resend."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    tmux = FakeTmux("Working (3s - esc to interrupt)")

    assert verify_prompt_delivery(
        tmux, "sess", "job2", PROMPT, markers=MARKERS
    ) is True
    assert tmux.enters == 0
    assert tmux.texts == []


def test_absent_prompt_is_resent(monkeypatch):
    """Nothing landed -> resend the prompt (the original failure mode)."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    tmux = FakeTmux("$ waiting for input", on_text=PROMPT + "\nWorking (1s - esc to interrupt)")

    verify_prompt_delivery(tmux, "sess", "job3", PROMPT, markers=MARKERS, retries=2)
    assert tmux.texts, "an absent prompt must be resent"
    assert tmux.enters == 0, "bare Enter is only for the pasted-not-submitted case"


def test_stuck_prompt_gives_up_without_duplicating(monkeypatch):
    """Enter never helps -> report failure, and resends stay bounded."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    tmux = FakeTmux(PROMPT)  # Enter never helps: composer stays stuck

    assert verify_prompt_delivery(
        tmux, "sess", "job4", PROMPT, markers=MARKERS, retries=2
    ) is False
    assert tmux.enters >= 1
    assert len(tmux.texts) <= 2, "resends must stay within the retry budget"
