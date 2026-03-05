"""Tests for Gemini CLI provider."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tcd.job import Job
from tcd.providers.gemini import GeminiProvider, _extract_between_markers
from tcd.tmux_adapter import TmuxAdapter


@pytest.fixture
def provider():
    return GeminiProvider()


@pytest.fixture
def job(tmp_path):
    j = Job(
        id="abc123",
        provider="gemini",
        status="running",
        prompt="test prompt",
        cwd=str(tmp_path),
        tmux_session="tcd-gemini-abc123",
    )
    return j


class TestGeminiProviderBasic:
    def test_name(self, provider):
        assert provider.name == "gemini"

    def test_cli_command(self, provider):
        assert provider.cli_command == "gemini"

    def test_tui_ready_indicator(self, provider):
        assert provider.tui_ready_indicator == "Type your message"

    def test_check_cli_found(self, provider):
        with patch("shutil.which", return_value="/usr/bin/gemini"):
            provider.check_cli()  # should not raise

    def test_check_cli_not_found(self, provider):
        with patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="gemini not found"):
                provider.check_cli()


class TestBuildLaunchCommand:
    def test_basic(self, provider, job):
        cmd = provider.build_launch_command(job)
        assert "gemini" in cmd
        assert "--yolo" in cmd
        assert "script -q" in cmd
        assert "[tcd: session complete]" in cmd

    def test_with_model(self, provider, job):
        job.model = "gemini-2.5-pro"
        cmd = provider.build_launch_command(job)
        assert "-m gemini-2.5-pro" in cmd

    def test_rejects_invalid_model(self, provider, job):
        job.model = "gemini; rm -rf /"
        with pytest.raises(ValueError, match="Invalid model name"):
            provider.build_launch_command(job)


class TestBuildPromptWrapper:
    def test_wraps_with_markers(self, provider):
        result = provider.build_prompt_wrapper("hello", "req-1")
        assert "TCD_REQ:req-1" in result
        assert "TCD_DONE:req-1" in result
        assert "hello" in result


class TestDetectCompletion:
    def test_signal_file(self, provider, job, tmp_path):
        """When signal file exists, return its state."""
        with patch("tcd.providers.gemini.job_signal_path") as mock_signal:
            signal_file = tmp_path / "signal.json"
            signal_file.write_text(json.dumps({"state": "idle"}))
            mock_signal.return_value = signal_file

            result = provider.detect_completion(job)
            assert result is not None
            assert result.state == "idle"

    def test_marker_in_pane(self, provider, job, tmp_path):
        """Detect TCD_DONE marker in capture-pane output."""
        pane_text = (
            "❯ TCD_REQ:abc123-0-999\n"
            "test prompt\n"
            "TCD_DONE:abc123-0-999\n"
            "❯ \n"
        )
        with patch("tcd.providers.gemini.job_signal_path") as mock_signal:
            mock_signal.return_value = tmp_path / "nonexistent"

            with patch.object(TmuxAdapter, "session_exists", return_value=True), \
                 patch.object(TmuxAdapter, "capture_pane", return_value=pane_text):
                result = provider.detect_completion(job)
                assert result is not None
                assert result.state == "idle"

    def test_no_signal_no_session(self, provider, job, tmp_path):
        """No signal file, session doesn't exist → None."""
        with patch("tcd.providers.gemini.job_signal_path") as mock_signal:
            mock_signal.return_value = tmp_path / "nonexistent"
            with patch.object(TmuxAdapter, "session_exists", return_value=False):
                result = provider.detect_completion(job)
                assert result is None


class TestExtractBetweenMarkers:
    def test_basic(self):
        text = (
            "TCD_REQ:req-1\n"
            "prompt text\n"
            "IMPORTANT: ...\n"
            "TCD_DONE:req-1\n"
            "\n"
            "Hello from Gemini\n"
            "\n"
            "TCD_DONE:req-1\n"
        )
        result = _extract_between_markers(text)
        assert result == "Hello from Gemini"

    def test_no_markers(self):
        result = _extract_between_markers("no markers here")
        assert result is None

    def test_only_req_no_done(self):
        result = _extract_between_markers("TCD_REQ:req-1\nsome text")
        assert result is None

    def test_response_content(self):
        text = (
            "Some header\n"
            "TCD_REQ:req-1\n"
            "User prompt here\n"
            "IMPORTANT: output TCD_DONE:req-1\n"
            "TCD_DONE:req-1\n"  # This is the AI echoing the marker
        )
        result = _extract_between_markers(text)
        assert result is None
