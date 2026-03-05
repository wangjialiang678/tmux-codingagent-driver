"""Tests for ResponseCollector."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tcd.collector import ResponseCollector
from tcd.job import Job


@pytest.fixture()
def job():
    return Job(
        id="coll1234",
        provider="codex",
        status="completed",
        prompt="test",
        cwd="/tmp",
        tmux_session="tcd-codex-coll1234",
    )


@pytest.fixture()
def mock_tmux():
    tmux = MagicMock()
    tmux.session_exists.return_value = False
    tmux.capture_pane.return_value = None
    return tmux


def test_collect_from_log_file(job, mock_tmux, tmp_path, monkeypatch):
    monkeypatch.setattr("tcd.collector.job_log_path", lambda jid: tmp_path / f"{jid}.log")
    log_path = tmp_path / f"{job.id}.log"
    log_path.write_text("Hello from Codex\nI completed the task.")

    collector = ResponseCollector(tmux=mock_tmux)
    result = collector.collect(job)
    assert result is not None
    assert "Hello from Codex" in result


def test_collect_from_capture_pane(job, mock_tmux):
    mock_tmux.session_exists.return_value = True
    mock_tmux.capture_pane.return_value = "Live output from pane"

    collector = ResponseCollector(tmux=mock_tmux)
    result = collector.collect(job)
    assert result is not None
    assert "Live output from pane" in result


def test_collect_returns_none_when_nothing(job, mock_tmux, tmp_path, monkeypatch):
    monkeypatch.setattr("tcd.collector.job_log_path", lambda jid: tmp_path / f"{jid}.log")
    collector = ResponseCollector(tmux=mock_tmux)
    result = collector.collect(job)
    assert result is None


def test_collect_raw_skips_cleaning(job, mock_tmux, tmp_path, monkeypatch):
    monkeypatch.setattr("tcd.collector.job_log_path", lambda jid: tmp_path / f"{jid}.log")
    log_path = tmp_path / f"{job.id}.log"
    log_path.write_text("\x1b[31mcolored\x1b[0m output")

    collector = ResponseCollector(tmux=mock_tmux)
    result = collector.collect_raw(job)
    assert result is not None
    assert "\x1b[31m" in result  # ANSI preserved
