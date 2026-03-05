"""Tests for tmux_adapter — requires real tmux."""

from __future__ import annotations

import time

import pytest

from tcd.tmux_adapter import TmuxAdapter, TmuxNotFoundError


# ---------------------------------------------------------------------------
# Unit tests (no tmux needed)
# ---------------------------------------------------------------------------


def test_build_script_command_darwin(monkeypatch):
    monkeypatch.setattr("tcd.tmux_adapter.platform.system", lambda: "Darwin")
    cmd = TmuxAdapter.build_script_command("/tmp/test.log", "codex -a never")
    assert cmd == "script -q /tmp/test.log codex -a never"


def test_build_script_command_linux(monkeypatch):
    monkeypatch.setattr("tcd.tmux_adapter.platform.system", lambda: "Linux")
    cmd = TmuxAdapter.build_script_command("/tmp/test.log", "codex -a never")
    assert cmd == "script -q -c 'codex -a never' /tmp/test.log"


def test_build_script_command_quotes_paths(monkeypatch):
    """Paths with spaces are properly quoted."""
    monkeypatch.setattr("tcd.tmux_adapter.platform.system", lambda: "Darwin")
    cmd = TmuxAdapter.build_script_command("/tmp/my log.txt", "codex -a never")
    assert "'/tmp/my log.txt'" in cmd


# ---------------------------------------------------------------------------
# Integration tests (require tmux)
# ---------------------------------------------------------------------------

SESSION_NAME = "tcd-test-adapter"


@pytest.fixture()
def tmux():
    adapter = TmuxAdapter()
    adapter.check_tmux()  # skip if tmux missing
    yield adapter
    # cleanup
    if adapter.session_exists(SESSION_NAME):
        adapter.kill_session(SESSION_NAME)


def test_create_and_kill_session(tmux: TmuxAdapter):
    assert tmux.create_session(SESSION_NAME, "bash", "/tmp")
    assert tmux.session_exists(SESSION_NAME)
    assert tmux.kill_session(SESSION_NAME)
    assert not tmux.session_exists(SESSION_NAME)


def test_session_exists_false(tmux: TmuxAdapter):
    assert not tmux.session_exists("tcd-test-nonexistent-xyz")


def test_send_keys_and_capture(tmux: TmuxAdapter):
    tmux.create_session(SESSION_NAME, "bash", "/tmp")
    time.sleep(0.3)
    tmux.send_keys(SESSION_NAME, "echo TCD_HELLO_WORLD")
    time.sleep(0.5)
    output = tmux.capture_pane(SESSION_NAME)
    assert output is not None
    assert "TCD_HELLO_WORLD" in output


def test_send_long_text(tmux: TmuxAdapter):
    tmux.create_session(SESSION_NAME, "cat", "/tmp")
    time.sleep(0.3)
    long_text = "A" * 6000
    assert tmux.send_long_text(SESSION_NAME, long_text)
    time.sleep(0.5)
    output = tmux.capture_pane(SESSION_NAME)
    assert output is not None
    # capture-pane may not show all 6000 chars, but should show a portion
    assert "AAAA" in output


def test_send_text_auto_selects(tmux: TmuxAdapter):
    tmux.create_session(SESSION_NAME, "bash", "/tmp")
    time.sleep(0.3)
    # Short text → send_keys path
    assert tmux.send_text(SESSION_NAME, "echo SHORT_MSG")
    time.sleep(0.5)
    output = tmux.capture_pane(SESSION_NAME)
    assert output is not None
    assert "SHORT_MSG" in output
