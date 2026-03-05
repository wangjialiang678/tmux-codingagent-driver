"""Tests for tcd CLI."""

from __future__ import annotations

import json

from click.testing import CliRunner

import pytest

from tcd.cli import cli
from tcd.config import JOBS_DIR
from tcd.job import Job, JobManager


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def tmp_jobs(tmp_path, monkeypatch):
    """Redirect all job paths to tmp."""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr("tcd.config.TCD_HOME", tmp_path)
    monkeypatch.setattr("tcd.config.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("tcd.job.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("tcd.job.job_json_path", lambda jid: jobs_dir / f"{jid}.json")
    monkeypatch.setattr("tcd.job.job_log_path", lambda jid: jobs_dir / f"{jid}.log")
    monkeypatch.setattr("tcd.job.job_prompt_path", lambda jid: jobs_dir / f"{jid}.prompt")
    monkeypatch.setattr("tcd.job.job_signal_path", lambda jid: jobs_dir / f"{jid}.turn-complete")
    monkeypatch.setattr("tcd.cli.job_signal_path", lambda jid: jobs_dir / f"{jid}.turn-complete")
    # Also patch collector
    monkeypatch.setattr("tcd.collector.job_log_path", lambda jid: jobs_dir / f"{jid}.log")
    return jobs_dir


def _create_test_job(jobs_dir, *, status="pending", provider="codex") -> Job:
    """Create a job file directly for testing."""
    mgr = JobManager()
    job = mgr.create_job(provider, "test prompt", "/tmp")
    job.status = status
    mgr.save_job(job)
    return job


# ---------------------------------------------------------------------------
# tcd start / send
# ---------------------------------------------------------------------------

def test_start_marks_failed_when_initial_send_fails(runner, tmp_jobs, monkeypatch):
    class FakeProvider:
        tui_ready_indicator = "READY"

        def check_cli(self):
            return None

        def build_launch_command(self, job):
            return "fake-launch"

        def build_prompt_wrapper(self, message, req_id):
            return message

    class FakeTmux:
        def create_session(self, session, cmd, cwd):
            return True

        def capture_pane(self, session):
            return "READY"

        def send_enter(self, session):
            return True

        def send_text(self, session, text):
            return False

    monkeypatch.setattr("tcd.cli._get_tmux", lambda: FakeTmux())
    monkeypatch.setattr("tcd.cli.get_provider", lambda provider: FakeProvider())

    result = runner.invoke(cli, ["start", "-p", "codex", "-m", "hello", "-d", "/tmp"])
    assert result.exit_code == 1

    jobs = JobManager().list_jobs()
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
    assert jobs[0].error == "failed to send initial prompt to tmux session"


def test_send_marks_failed_when_tmux_send_fails(runner, tmp_jobs, monkeypatch):
    job = _create_test_job(tmp_jobs, status="running")
    job.turn_state = "working"
    JobManager().save_job(job)

    class FakeTmux:
        def check_tmux(self):
            return None

        def send_text(self, session, text):
            return False

    class FakeProvider:
        def build_prompt_wrapper(self, message, req_id):
            return message

    monkeypatch.setattr("tcd.cli._get_tmux", lambda: FakeTmux())
    monkeypatch.setattr("tcd.cli.get_provider", lambda provider: FakeProvider())

    result = runner.invoke(cli, ["send", job.id, "follow up"])
    assert result.exit_code == 1

    updated = JobManager().load_job(job.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "failed to send message to tmux session"


# ---------------------------------------------------------------------------
# tcd --help
# ---------------------------------------------------------------------------

def test_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "start" in result.output
    assert "check" in result.output
    assert "output" in result.output


# ---------------------------------------------------------------------------
# tcd jobs
# ---------------------------------------------------------------------------

def test_jobs_empty(runner, tmp_jobs):
    result = runner.invoke(cli, ["jobs"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


def test_jobs_list(runner, tmp_jobs):
    _create_test_job(tmp_jobs)
    _create_test_job(tmp_jobs, status="running")
    result = runner.invoke(cli, ["jobs"])
    assert result.exit_code == 0
    assert "codex" in result.output


def test_jobs_json(runner, tmp_jobs):
    _create_test_job(tmp_jobs)
    result = runner.invoke(cli, ["jobs", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1


def test_jobs_filter(runner, tmp_jobs):
    _create_test_job(tmp_jobs, status="pending")
    j2 = _create_test_job(tmp_jobs, status="completed")
    j2.status = "completed"
    JobManager().save_job(j2)
    result = runner.invoke(cli, ["jobs", "--status", "completed"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# tcd status
# ---------------------------------------------------------------------------

def test_status_not_found(runner, tmp_jobs):
    result = runner.invoke(cli, ["status", "nonexistent"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_status_ok(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="completed")
    result = runner.invoke(cli, ["status", job.id])
    assert result.exit_code == 0
    assert job.id in result.output
    assert "completed" in result.output


def test_status_json(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="completed")
    result = runner.invoke(cli, ["status", job.id, "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["id"] == job.id


def test_status_marks_failed_if_session_disappears_while_working(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="running")
    job.turn_state = "working"
    JobManager().save_job(job)

    import tcd.cli as cli_mod

    orig = cli_mod.TmuxAdapter

    class FakeTmux:
        def check_tmux(self):
            return None

        def session_exists(self, name):
            return False

    cli_mod.TmuxAdapter = FakeTmux
    try:
        result = runner.invoke(cli, ["status", job.id])
        assert result.exit_code == 0
    finally:
        cli_mod.TmuxAdapter = orig

    updated = JobManager().load_job(job.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error is not None


def test_status_marks_completed_if_session_disappears_after_idle(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="running")
    job.turn_state = "idle"
    JobManager().save_job(job)

    import tcd.cli as cli_mod

    orig = cli_mod.TmuxAdapter

    class FakeTmux:
        def check_tmux(self):
            return None

        def session_exists(self, name):
            return False

    cli_mod.TmuxAdapter = FakeTmux
    try:
        result = runner.invoke(cli, ["status", job.id])
        assert result.exit_code == 0
    finally:
        cli_mod.TmuxAdapter = orig

    updated = JobManager().load_job(job.id)
    assert updated is not None
    assert updated.status == "completed"


# ---------------------------------------------------------------------------
# tcd check
# ---------------------------------------------------------------------------

def test_check_not_found(runner, tmp_jobs):
    result = runner.invoke(cli, ["check", "nonexistent"])
    assert result.exit_code == 3


def test_check_completed(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="completed")
    result = runner.invoke(cli, ["check", job.id])
    assert result.exit_code == 0  # idle


def test_check_working(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="running")
    # Patch session_exists to return True so it stays "running"
    import tcd.cli
    orig = tcd.cli.TmuxAdapter
    class FakeTmux:
        def check_tmux(self): pass
        def session_exists(self, name): return True
    import tcd.cli as cli_mod
    cli_mod.TmuxAdapter = FakeTmux
    try:
        result = runner.invoke(cli, ["check", job.id])
        assert result.exit_code == 1  # working
    finally:
        cli_mod.TmuxAdapter = orig


def test_check_json_includes_diagnostics_and_pane_tail(runner, tmp_jobs, monkeypatch):
    from tcd.diagnostics import Warning

    job = _create_test_job(tmp_jobs, status="running")

    class FakeTmux:
        def check_tmux(self):
            return None

        def session_exists(self, name):
            return True

        def capture_pane(self, session):
            return "l1\nl2\nl3\nl4\nl5\nl6\nl7\n"

    class FakeProvider:
        def detect_completion(self, job):
            return None

    monkeypatch.setattr("tcd.cli.TmuxAdapter", FakeTmux)
    monkeypatch.setattr("tcd.cli.get_provider", lambda provider: FakeProvider())
    monkeypatch.setattr(
        "tcd.cli.diagnose",
        lambda _job, pane_tail=None: [Warning(code="TEST_WARNING", message="test message", severity="warn")],
    )

    result = runner.invoke(cli, ["check", job.id, "--json"])
    assert result.exit_code == 1

    data = json.loads(result.output)
    assert data["state"] == "working"
    assert data["turn_count"] == job.turn_count
    assert data["pane_tail"] == "l3\nl4\nl5\nl6\nl7"
    assert data["warnings"] == [
        {"code": "TEST_WARNING", "severity": "warn", "message": "test message"}
    ]


def test_check_json_not_found(runner, tmp_jobs):
    result = runner.invoke(cli, ["check", "nonexistent", "--json"])
    assert result.exit_code == 3

    data = json.loads(result.output)
    assert data["state"] == "not_found"
    assert data["elapsed_s"] == 0
    assert data["turn_count"] == 0
    assert data["warnings"] == []
    assert data["pane_tail"] == ""


# ---------------------------------------------------------------------------
# tcd output
# ---------------------------------------------------------------------------

def test_output_not_found(runner, tmp_jobs):
    result = runner.invoke(cli, ["output", "nonexistent"])
    assert result.exit_code != 0


def test_output_from_log(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="completed")
    log_path = tmp_jobs / f"{job.id}.log"
    log_path.write_text("AI response text here")
    result = runner.invoke(cli, ["output", job.id])
    assert result.exit_code == 0
    assert "AI response text here" in result.output


# ---------------------------------------------------------------------------
# tcd kill
# ---------------------------------------------------------------------------

def test_kill_not_found(runner, tmp_jobs):
    result = runner.invoke(cli, ["kill", "nonexistent"])
    assert result.exit_code != 0


def test_kill_job(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="running")
    result = runner.invoke(cli, ["kill", job.id])
    assert result.exit_code == 0
    assert "Killed" in result.output
    # Verify status changed
    updated = JobManager().load_job(job.id)
    assert updated is not None
    assert updated.status == "failed"


# ---------------------------------------------------------------------------
# tcd clean
# ---------------------------------------------------------------------------

def test_clean(runner, tmp_jobs):
    _create_test_job(tmp_jobs, status="completed")
    _create_test_job(tmp_jobs, status="pending")
    result = runner.invoke(cli, ["clean"])
    assert result.exit_code == 0
    assert "Cleaned 1" in result.output


def test_clean_all(runner, tmp_jobs):
    _create_test_job(tmp_jobs, status="completed")
    _create_test_job(tmp_jobs, status="pending")
    result = runner.invoke(cli, ["clean", "--all"])
    assert result.exit_code == 0
    assert "Cleaned 2" in result.output
