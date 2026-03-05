"""Tests for the Python SDK."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tcd.job import Job
from tcd.sdk import (
    TCD,
    CheckResult,
    DiagnosticCheckResult,
    JobNotFoundError,
    JobNotRunningError,
    TCDError,
    TimeoutError,
)


@pytest.fixture
def mock_tmux():
    """Create a mock TmuxAdapter."""
    with patch("tcd.sdk.TmuxAdapter") as MockTmux:
        mock = MockTmux.return_value
        mock.check_tmux.return_value = None
        mock.session_exists.return_value = True
        mock.create_session.return_value = True
        mock.send_text.return_value = True
        mock.send_enter.return_value = True
        mock.capture_pane.return_value = "Type your message"  # TUI ready
        mock.kill_session.return_value = True
        yield mock


@pytest.fixture
def sdk(mock_tmux, tmp_path):
    """Create a TCD SDK instance with mocked tmux and tmp job dir."""
    with patch("tcd.sdk.ensure_dirs"):
        with patch("tcd.sdk.JobManager") as MockMgr:
            mgr = MockMgr.return_value
            # create_job returns a Job
            mgr.create_job.return_value = Job(
                id="test123",
                provider="codex",
                status="pending",
                prompt="test",
                cwd="/tmp",
                tmux_session="tcd-codex-test123",
            )
            mgr.save_job.return_value = None
            sdk = TCD()
            sdk._mgr = mgr
            yield sdk


class TestSDKInit:
    def test_init_success(self, mock_tmux):
        with patch("tcd.sdk.ensure_dirs"), patch("tcd.sdk.JobManager"):
            sdk = TCD()
            assert sdk is not None

    def test_init_no_tmux(self):
        with patch("tcd.sdk.ensure_dirs"), patch("tcd.sdk.JobManager"):
            with patch("tcd.sdk.TmuxAdapter") as MockTmux:
                from tcd.tmux_adapter import TmuxNotFoundError
                MockTmux.return_value.check_tmux.side_effect = TmuxNotFoundError("no tmux")
                with pytest.raises(TCDError, match="no tmux"):
                    TCD()


class TestStart:
    def test_start_success(self, sdk, mock_tmux):
        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.build_launch_command.return_value = "codex"
            prov.build_prompt_wrapper.return_value = "wrapped prompt"
            prov.tui_ready_indicator = "›"
            mock_prov.return_value = prov

            job = sdk.start("codex", "do something", "/tmp")
            assert job.id == "test123"
            assert job.status == "running"
            mock_tmux.send_text.assert_called_once()

    def test_start_invalid_provider(self, sdk):
        with patch("tcd.sdk.get_provider", side_effect=ValueError("Unknown")):
            with pytest.raises(TCDError, match="Unknown"):
                sdk.start("invalid", "test", "/tmp")

    def test_start_session_fail(self, sdk, mock_tmux):
        mock_tmux.create_session.return_value = False
        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            mock_prov.return_value = prov
            with pytest.raises(TCDError, match="Failed to create tmux session"):
                sdk.start("codex", "test", "/tmp")


class TestCheck:
    def test_check_not_found(self, sdk):
        sdk._mgr.load_job.return_value = None
        with pytest.raises(JobNotFoundError):
            sdk.check("nonexistent")

    def test_check_completed(self, sdk, mock_tmux):
        job = Job(
            id="test123", provider="codex", status="completed",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
        )
        sdk._mgr.load_job.return_value = job
        result = sdk.check("test123")
        assert result.state == "completed"

    def test_check_idle_via_provider(self, sdk, mock_tmux):
        from tcd.provider import CompletionResult
        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
        )
        sdk._mgr.load_job.return_value = job
        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.detect_completion.return_value = CompletionResult(
                state="idle", last_agent_message="done"
            )
            mock_prov.return_value = prov
            result = sdk.check("test123")
            assert result.state == "idle"
            assert result.last_agent_message == "done"

    def test_check_advances_turn_for_marker_provider(self, sdk, mock_tmux):
        from tcd.provider import CompletionResult

        job = Job(
            id="test123", provider="gemini", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-gemini-test123",
            turn_count=0, turn_state="working",
        )
        sdk._mgr.load_job.return_value = job
        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.detect_completion.return_value = CompletionResult(state="idle")
            mock_prov.return_value = prov
            result = sdk.check("test123")

        assert result.state == "idle"
        assert job.turn_count == 1

    def test_check_does_not_advance_turn_for_codex_provider(self, sdk, mock_tmux):
        from tcd.provider import CompletionResult

        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
            turn_count=1, turn_state="working",
        )
        sdk._mgr.load_job.return_value = job
        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.detect_completion.return_value = CompletionResult(state="idle")
            mock_prov.return_value = prov
            result = sdk.check("test123")

        assert result.state == "idle"
        assert job.turn_count == 1

    def test_check_accumulates_tokens(self, sdk, mock_tmux):
        from tcd.provider import CompletionResult

        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
            turn_count=0, turn_state="working",
        )
        sdk._mgr.load_job.return_value = job
        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.detect_completion.return_value = CompletionResult(
                state="idle", tokens={"input": 5000, "output": 3000}
            )
            mock_prov.return_value = prov
            sdk.check("test123")

        assert job.total_tokens == {"input": 5000, "output": 3000}

    def test_check_accumulates_tokens_across_turns(self, sdk, mock_tmux):
        from tcd.provider import CompletionResult

        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
            turn_count=0, turn_state="working",
            total_tokens={"input": 1000, "output": 500},
        )
        sdk._mgr.load_job.return_value = job
        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.detect_completion.return_value = CompletionResult(
                state="idle", tokens={"input": 2000, "output": 1000}
            )
            mock_prov.return_value = prov
            sdk.check("test123")

        assert job.total_tokens == {"input": 3000, "output": 1500}

    def test_check_no_tokens_leaves_total_unchanged(self, sdk, mock_tmux):
        from tcd.provider import CompletionResult

        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
            turn_count=0, turn_state="working",
        )
        sdk._mgr.load_job.return_value = job
        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.detect_completion.return_value = CompletionResult(state="idle", tokens=None)
            mock_prov.return_value = prov
            sdk.check("test123")

        assert job.total_tokens == {"input": 0, "output": 0}

    def test_check_session_disappears_while_working(self, sdk, mock_tmux):
        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
            turn_state="working",
        )
        sdk._mgr.load_job.return_value = job
        mock_tmux.session_exists.return_value = False

        result = sdk.check("test123")
        assert result.state == "failed"
        assert job.error is not None

    def test_check_session_disappears_after_idle(self, sdk, mock_tmux):
        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
            turn_state="idle",
        )
        sdk._mgr.load_job.return_value = job
        mock_tmux.session_exists.return_value = False

        result = sdk.check("test123")
        assert result.state == "completed"

    def test_check_with_diagnostics_returns_pane_tail(self, sdk, mock_tmux):
        job = Job(
            id="test123", provider="codex", status="running",
            prompt="review docs", cwd="/tmp", tmux_session="tcd-codex-test123",
            sandbox="danger-full-access",
        )
        sdk._mgr.load_job.return_value = job
        mock_tmux.capture_pane.return_value = "l1\nl2\nl3\nl4\nl5\nl6\n"

        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.detect_completion.return_value = None
            mock_prov.return_value = prov
            result = sdk.check_with_diagnostics("test123")

        assert isinstance(result, DiagnosticCheckResult)
        assert result.state == "working"
        assert result.pane_tail == "l2\nl3\nl4\nl5\nl6"
        assert result.warnings == []

    def test_check_with_diagnostics_includes_warning_codes(self, sdk, mock_tmux):
        job = Job(
            id="test123", provider="codex", status="running",
            prompt="please fix this", cwd="/tmp", tmux_session="tcd-codex-test123",
            sandbox="workspace-write",
        )
        sdk._mgr.load_job.return_value = job
        mock_tmux.capture_pane.return_value = "fatal: Permission denied\n"

        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.detect_completion.return_value = None
            mock_prov.return_value = prov
            result = sdk.check_with_diagnostics("test123")

        codes = {warning.code for warning in result.warnings}
        assert "SANDBOX_MISMATCH" in codes
        assert "PERMISSION_ERROR" in codes

    def test_check_with_diagnostics_not_found(self, sdk):
        sdk._mgr.load_job.return_value = None
        with pytest.raises(JobNotFoundError):
            sdk.check_with_diagnostics("nonexistent")


class TestWait:
    def test_wait_immediate_idle(self, sdk, mock_tmux):
        from tcd.provider import CompletionResult
        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
        )
        sdk._mgr.load_job.return_value = job
        with patch("tcd.sdk.get_provider") as mock_prov:
            prov = MagicMock()
            prov.detect_completion.return_value = CompletionResult(state="idle")
            mock_prov.return_value = prov
            result = sdk.wait("test123", timeout=5)
            assert result.state == "idle"

    def test_wait_not_found(self, sdk):
        sdk._mgr.load_job.return_value = None
        with pytest.raises(JobNotFoundError):
            sdk.wait("nonexistent")


class TestSend:
    def test_send_success(self, sdk, mock_tmux):
        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
        )
        sdk._mgr.load_job.return_value = job
        with patch("tcd.sdk.get_provider") as mock_prov, \
             patch("tcd.sdk.job_signal_path") as mock_signal:
            prov = MagicMock()
            prov.build_prompt_wrapper.return_value = "wrapped"
            mock_prov.return_value = prov
            mock_signal.return_value = MagicMock(unlink=MagicMock())
            sdk.send("test123", "follow up")
            mock_tmux.send_text.assert_called_once()
            req_id = prov.build_prompt_wrapper.call_args[0][1]
            assert req_id.startswith("test123-0-")

    def test_send_not_running(self, sdk):
        job = Job(
            id="test123", provider="codex", status="completed",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
        )
        sdk._mgr.load_job.return_value = job
        with pytest.raises(JobNotRunningError):
            sdk.send("test123", "follow up")

    def test_send_not_found(self, sdk):
        sdk._mgr.load_job.return_value = None
        with pytest.raises(JobNotFoundError):
            sdk.send("nonexistent", "test")


class TestKill:
    def test_kill_success(self, sdk, mock_tmux):
        job = Job(
            id="test123", provider="codex", status="running",
            prompt="test", cwd="/tmp", tmux_session="tcd-codex-test123",
        )
        sdk._mgr.load_job.return_value = job
        sdk.kill("test123")
        mock_tmux.kill_session.assert_called_once()

    def test_kill_not_found(self, sdk):
        sdk._mgr.load_job.return_value = None
        with pytest.raises(JobNotFoundError):
            sdk.kill("nonexistent")


class TestJobs:
    def test_list_jobs(self, sdk):
        sdk._mgr.list_jobs.return_value = []
        result = sdk.jobs()
        assert result == []


class TestClean:
    def test_clean(self, sdk):
        sdk._mgr.clean_jobs.return_value = 3
        result = sdk.clean()
        assert result == 3


class TestOutput:
    def test_output_not_found(self, sdk):
        sdk._mgr.load_job.return_value = None
        with pytest.raises(JobNotFoundError):
            sdk.output("nonexistent")
