"""Tests for Claude Code provider."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tcd.job import Job
from tcd.provider import get_provider
from tcd.providers.claude import ClaudeProvider


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
