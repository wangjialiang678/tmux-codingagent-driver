"""Tests for idle_detector."""

from __future__ import annotations

from unittest.mock import MagicMock

from tcd.idle_detector import IdleDetector


def _make_detector(snapshots: list[str | None], **kwargs) -> IdleDetector:
    """Build an IdleDetector with a mock tmux that returns given snapshots."""
    tmux = MagicMock()
    tmux.capture_pane = MagicMock(side_effect=snapshots)
    return IdleDetector(tmux=tmux, poll_interval=0.01, **kwargs)


def test_is_idle_same_content():
    det = _make_detector(["hello", "hello"])
    assert det.is_idle("sess") is True


def test_is_idle_different_content():
    det = _make_detector(["hello", "hello world"])
    assert det.is_idle("sess") is False


def test_is_idle_session_gone():
    det = _make_detector([None])
    assert det.is_idle("sess") is False


def test_wait_for_idle_immediate():
    """Content stable from the start → idle detected quickly."""
    snaps = ["same"] * 20  # all identical
    det = _make_detector(snaps, idle_threshold=0.02)
    assert det.wait_for_idle("sess", timeout=1.0) is True


def test_wait_for_idle_content_changes_then_stabilizes():
    """Content changes, then stabilizes → idle detected."""
    snaps = ["a", "b", "c", "c", "c", "c", "c", "c", "c", "c"]
    det = _make_detector(snaps, idle_threshold=0.02)
    assert det.wait_for_idle("sess", timeout=1.0) is True


def test_wait_for_idle_session_gone():
    snaps = ["a", None]
    det = _make_detector(snaps, idle_threshold=0.02)
    assert det.wait_for_idle("sess", timeout=0.5) is False
