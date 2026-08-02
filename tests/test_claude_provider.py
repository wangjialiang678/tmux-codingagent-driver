"""Tests for Claude Code provider."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tcd.job import Job
from tcd.provider import get_provider
from tcd.providers.claude import ClaudeProvider, has_queued_message_notice


@pytest.fixture()
def job(tmp_path: Path) -> Job:
    return Job(
        id="test1234",
        provider="claude",
        status="running",
        prompt="test prompt",
        cwd=str(tmp_path),
        tmux_session="tcd-claude-test1234",
    )


def test_registered():
    prov = get_provider("claude")
    assert isinstance(prov, ClaudeProvider)


def test_build_launch_command(job: Job):
    cmd = ClaudeProvider().build_launch_command(job)
    assert "claude" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "script" in cmd
    assert "[tcd: session complete]" in cmd


def test_build_launch_command_with_model(job: Job):
    job.model = "claude-sonnet-4-5-20250514"
    cmd = ClaudeProvider().build_launch_command(job)
    assert "-m claude-sonnet-4-5-20250514" in cmd


def test_build_launch_command_rejects_invalid_model(job: Job):
    job.model = "claude; rm -rf /"
    with pytest.raises(ValueError, match="Invalid model name"):
        ClaudeProvider().build_launch_command(job)


def test_build_prompt_wrapper():
    prov = ClaudeProvider()
    wrapped = prov.build_prompt_wrapper("do something", "job1-0-123")
    assert "TCD_REQ:job1-0-123" in wrapped
    assert "do something" in wrapped
    assert "TCD_DONE:job1-0-123" in wrapped


def test_has_queued_message_notice():
    assert has_queued_message_notice("Press up to edit queued messages")
    assert has_queued_message_notice("  press UP to edit queued message  ")
    assert not has_queued_message_notice("Type your message")


def test_detect_completion_signal_file(job: Job, tmp_path: Path):
    with patch("tcd.providers.claude.job_signal_path") as mock_sig:
        sig = tmp_path / "test.turn-complete"
        sig.write_text(json.dumps({"state": "idle", "lastAgentMessage": "done"}))
        mock_sig.return_value = sig

        prov = ClaudeProvider()
        result = prov.detect_completion(job)
        assert result is not None
        assert result.state == "idle"


def test_detect_completion_no_signal(job: Job, tmp_path: Path):
    with patch("tcd.providers.claude.job_signal_path") as mock_sig:
        sig = tmp_path / "nonexistent.turn-complete"
        mock_sig.return_value = sig
        with patch("tcd.providers.claude.TmuxAdapter") as MockTmux:
            mock_tmux = MockTmux.return_value
            mock_tmux.session_exists.return_value = False

            prov = ClaudeProvider()
            result = prov.detect_completion(job)
            assert result is None


def test_parse_jsonl_content_blocks(tmp_path: Path):
    """Test parsing Claude's JSONL format with content blocks."""
    f = tmp_path / "session.jsonl"
    f.write_text(
        '{"type":"human","role":"user","content":"hello"}\n'
        '{"type":"text","role":"assistant","content":[{"type":"text","text":"Hi there!"}]}\n'
    )
    result = ClaudeProvider._parse_jsonl(f)
    assert result == "Hi there!"


def test_parse_jsonl_string_content(tmp_path: Path):
    """Test parsing when content is a plain string."""
    f = tmp_path / "session.jsonl"
    f.write_text('{"role":"assistant","content":"Simple response"}\n')
    result = ClaudeProvider._parse_jsonl(f)
    assert result == "Simple response"


def test_parse_jsonl_empty(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    f.write_text("")
    result = ClaudeProvider._parse_jsonl(f)
    assert result is None


# ---------------------------------------------------------------------------
# Session-file lookup must be scoped to the job
#
# Driving tcd from inside a Claude Code session keeps the orchestrator's own
# transcript the most recently written .jsonl in ~/.claude/projects, so a
# global "newest file" scan returns the caller's transcript, not the job's.
# ---------------------------------------------------------------------------

def _make_job(cwd: str, started_at: str) -> Job:
    return Job(
        id="job1",
        provider="claude",
        status="running",
        prompt="task",
        cwd=cwd,
        tmux_session="tcd-claude-job1",
        started_at=started_at,
    )


def test_find_session_file_ignores_other_projects(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    provider = ClaudeProvider()

    job_cwd = "/Users/dev/target-project"
    job_dir = projects / provider.encode_project_dir(job_cwd)
    job_dir.mkdir(parents=True)
    job_file = job_dir / "session.jsonl"
    job_file.write_text('{"role":"assistant","content":"from the job"}\n')

    # The orchestrator's own transcript, written later, in a different project.
    other_dir = projects / provider.encode_project_dir("/Users/dev/orchestrator")
    other_dir.mkdir(parents=True)
    other_file = other_dir / "session.jsonl"
    other_file.write_text('{"role":"assistant","content":"from the caller"}\n')
    os.utime(other_file, (time.time() + 60, time.time() + 60))

    monkeypatch.setattr("tcd.providers.claude.CLAUDE_PROJECTS_DIR", projects)

    job = _make_job(job_cwd, "2020-01-01T00:00:00+00:00")
    assert provider.get_session_log_path(job) == job_file


def test_find_session_file_ignores_transcripts_older_than_the_job(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    provider = ClaudeProvider()

    job_cwd = "/Users/dev/target-project"
    job_dir = projects / provider.encode_project_dir(job_cwd)
    job_dir.mkdir(parents=True)
    stale = job_dir / "previous-session.jsonl"
    stale.write_text('{"role":"assistant","content":"last week"}\n')
    os.utime(stale, (1_600_000_000, 1_600_000_000))

    monkeypatch.setattr("tcd.providers.claude.CLAUDE_PROJECTS_DIR", projects)

    job = _make_job(job_cwd, datetime.now(timezone.utc).isoformat())
    assert provider.get_session_log_path(job) is None


def test_encode_project_dir_matches_claude_layout():
    assert (
        ClaudeProvider.encode_project_dir("/Users/dev/my project.v2")
        == "-Users-dev-my-project-v2"
    )
