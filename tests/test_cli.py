"""Tests for tcd CLI."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

import pytest

from tcd import __version__
from tcd.cli import cli
from tcd.config import JOBS_DIR
from tcd.event_log import load_events
from tcd.job import Job, JobManager


@pytest.fixture()
def runner():
    return CliRunner()


def test_version_flag_reports_package_version(runner):
    """`tcd --version` must exist and report `__version__`.

    Both spellings are checked because `-v` is verbosity, not version.
    """
    for flag in ("--version", "-V"):
        result = runner.invoke(cli, [flag])
        assert result.exit_code == 0
        assert __version__ in result.output


def test_pyproject_takes_its_version_from_the_package():
    """The version must have exactly one source.

    pyproject and __init__ previously both carried a literal and had drifted to
    0.1.0 while the changelog was on 0.3.x. Asserting the *wiring* rather than
    the installed metadata keeps this honest without depending on whether the
    environment has been re-synced since the last bump.
    """
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()

    assert 'dynamic = ["version"]' in pyproject
    assert '[tool.hatch.version]' in pyproject
    assert 'path = "src/tcd/__init__.py"' in pyproject
    assert "\nversion = " not in pyproject, "a literal version reintroduces the drift"


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

        def capture_pane(self, session, **kwargs):
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


def test_send_retries_enter_when_claude_leaves_followup_queued(runner, tmp_jobs, monkeypatch):
    job = _create_test_job(tmp_jobs, status="running", provider="claude")
    job.turn_state = "idle"
    JobManager().save_job(job)

    class FakeTmux:
        def __init__(self):
            self.enter_count = 0

        def check_tmux(self):
            return None

        def send_text(self, session, text):
            return True

        def capture_pane(self, session, **kwargs):
            return "Press up to edit queued messages"

        def send_enter(self, session):
            self.enter_count += 1
            return True

    class FakeProvider:
        def build_prompt_wrapper(self, message, req_id):
            return message

        def has_queued_message_notice(self, pane):
            return "queued messages" in pane

    fake_tmux = FakeTmux()
    monkeypatch.setattr("tcd.cli._get_tmux", lambda: fake_tmux)
    monkeypatch.setattr("tcd.cli.get_provider", lambda provider: FakeProvider())
    monkeypatch.setattr("tcd.submission_recovery.time.sleep", lambda _seconds: None)

    result = runner.invoke(cli, ["send", job.id, "follow up"])
    assert result.exit_code == 0
    assert fake_tmux.enter_count == 1

    events = load_events(job.id, event_filter="job.message_submit_retry")
    assert len(events) == 1
    assert events[0]["reason"] == "queued_message_notice"
    assert events[0]["success"] is True


def test_send_does_not_retry_enter_without_queued_notice(runner, tmp_jobs, monkeypatch):
    job = _create_test_job(tmp_jobs, status="running", provider="claude")
    job.turn_state = "idle"
    JobManager().save_job(job)

    class FakeTmux:
        def __init__(self):
            self.enter_count = 0

        def check_tmux(self):
            return None

        def send_text(self, session, text):
            return True

        def capture_pane(self, session, **kwargs):
            return "Claude is responding normally"

        def send_enter(self, session):
            self.enter_count += 1
            return True

    class FakeProvider:
        def build_prompt_wrapper(self, message, req_id):
            return message

        def has_queued_message_notice(self, pane):
            return "queued messages" in pane

    fake_tmux = FakeTmux()
    monkeypatch.setattr("tcd.cli._get_tmux", lambda: fake_tmux)
    monkeypatch.setattr("tcd.cli.get_provider", lambda provider: FakeProvider())
    monkeypatch.setattr("tcd.submission_recovery.time.sleep", lambda _seconds: None)

    result = runner.invoke(cli, ["send", job.id, "follow up"])
    assert result.exit_code == 0
    assert fake_tmux.enter_count == 0
    assert load_events(job.id, event_filter="job.message_submit_retry") == []


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


def test_status_running_idle_explains_turn_complete_not_job_complete(runner, tmp_jobs, monkeypatch):
    job = _create_test_job(tmp_jobs, status="running", provider="claude")
    job.turn_state = "idle"
    JobManager().save_job(job)

    class FakeTmux:
        def check_tmux(self):
            return None

        def session_exists(self, name):
            return True

    monkeypatch.setattr("tcd.cli.TmuxAdapter", FakeTmux)

    result = runner.invoke(cli, ["status", job.id])
    assert result.exit_code == 0
    assert "turn is complete" in result.output
    assert "session is still running" in result.output


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

        def capture_pane(self, session, **kwargs):
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


def test_output_running_idle_includes_debug_note(runner, tmp_jobs, monkeypatch):
    job = _create_test_job(tmp_jobs, status="running", provider="claude")
    job.turn_state = "idle"
    JobManager().save_job(job)

    class FakeCollector:
        def collect(self, job):
            return "AI response text here"

    monkeypatch.setattr("tcd.cli.ResponseCollector", lambda: FakeCollector())

    result = runner.invoke(cli, ["output", job.id])
    combined_output = result.output + getattr(result, "stderr", "")
    assert result.exit_code == 0
    assert "AI response text here" in combined_output
    assert "turn is complete" in combined_output
    assert "output --full" in combined_output


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


# ---------------------------------------------------------------------------
# Job-state reconciliation
#
# Job records mirror tmux state at write time. Without reconciliation, a job
# whose session died without `tcd kill` stays "running" forever and its
# reported elapsed time keeps growing (observed: a job claiming 30 days).
# ---------------------------------------------------------------------------

def test_jobs_reconciles_dead_sessions(runner, tmp_jobs, monkeypatch):
    alive = _create_test_job(tmp_jobs, status="running")
    dead = _create_test_job(tmp_jobs, status="running")

    class FakeTmux:
        def list_sessions(self):
            return {alive.tmux_session}

    monkeypatch.setattr("tcd.cli.TmuxAdapter", lambda: FakeTmux())

    result = runner.invoke(cli, ["jobs"])
    assert result.exit_code == 0

    mgr = JobManager()
    assert mgr.load_job(alive.id).status == "running"
    reconciled = mgr.load_job(dead.id)
    assert reconciled.status == "completed"
    assert reconciled.completed_at is not None


def test_jobs_no_reconcile_leaves_records_alone(runner, tmp_jobs, monkeypatch):
    dead = _create_test_job(tmp_jobs, status="running")

    class FakeTmux:
        def list_sessions(self):
            return set()

    monkeypatch.setattr("tcd.cli.TmuxAdapter", lambda: FakeTmux())

    result = runner.invoke(cli, ["jobs", "--no-reconcile"])
    assert result.exit_code == 0
    assert JobManager().load_job(dead.id).status == "running"


def test_elapsed_stops_at_completion(tmp_jobs):
    from tcd.cli import _elapsed

    job = _create_test_job(tmp_jobs, status="completed")
    job.started_at = "2026-01-01T00:00:00+00:00"
    job.completed_at = "2026-01-01T00:05:00+00:00"

    assert _elapsed(job) == 300


def test_elapsed_counts_up_while_running(tmp_jobs):
    from tcd.cli import _elapsed

    job = _create_test_job(tmp_jobs, status="running")
    job.started_at = "2026-01-01T00:00:00+00:00"
    job.completed_at = None

    # Still open-ended, so it must be far larger than any fixed window.
    assert _elapsed(job) > 300


# ---------------------------------------------------------------------------
# tcd clean must not orphan resources
# ---------------------------------------------------------------------------

def test_clean_keeps_jobs_owning_an_unrestored_stash(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="failed")
    job.worktree_stash_ref = "abc123def456"
    job.worktree_stash_restored = False
    JobManager().save_job(job)

    result = runner.invoke(cli, ["clean"])
    assert result.exit_code == 0
    assert "Cleaned 0 job(s)." in result.output
    assert JobManager().load_job(job.id) is not None


def test_clean_force_removes_jobs_owning_resources(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="failed")
    job.worktree_stash_ref = "abc123def456"
    JobManager().save_job(job)

    result = runner.invoke(cli, ["clean", "--force"])
    assert result.exit_code == 0
    assert JobManager().load_job(job.id) is None


def test_clean_removes_jobs_with_restored_stash(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="failed")
    job.worktree_stash_ref = "abc123def456"
    job.worktree_stash_restored = True
    JobManager().save_job(job)

    result = runner.invoke(cli, ["clean"])
    assert result.exit_code == 0
    assert JobManager().load_job(job.id) is None


# ---------------------------------------------------------------------------
# Options must not be silently dropped
# ---------------------------------------------------------------------------

def test_start_rejects_sandbox_on_providers_that_ignore_it(runner, tmp_jobs):
    result = runner.invoke(cli, ["start", "-p", "claude", "-m", "hi", "--sandbox", "workspace-write"])
    assert result.exit_code == 1
    assert "does not support --sandbox" in result.output


def test_start_provider_choices_come_from_the_registry(runner):
    from tcd.provider import list_providers

    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    for name in list_providers():
        assert name in result.output


def test_clean_releases_jobs_whose_stash_was_removed_by_hand(runner, tmp_jobs, monkeypatch):
    """A stash the user already popped must not pin the record forever."""
    job = _create_test_job(tmp_jobs, status="failed")
    job.worktree_stash_ref = "abc123def456"
    job.worktree_repo_root = "/repo"
    JobManager().save_job(job)

    monkeypatch.setattr("tcd.worktree.stash_exists", lambda _root, _ref: False)

    result = runner.invoke(cli, ["clean"])
    assert result.exit_code == 0
    assert JobManager().load_job(job.id) is None


def test_clean_force_lists_what_it_orphans(runner, tmp_jobs, monkeypatch):
    job = _create_test_job(tmp_jobs, status="failed")
    job.worktree_stash_ref = "abc123def456"
    job.worktree_repo_root = "/repo"
    JobManager().save_job(job)

    monkeypatch.setattr("tcd.worktree.stash_exists", lambda _root, _ref: True)

    result = runner.invoke(cli, ["clean", "--force"])
    assert result.exit_code == 0
    assert "Orphaning resources" in result.output
    assert "abc123de" in result.output
    assert JobManager().load_job(job.id) is None


def test_clean_guidance_does_not_point_at_force(runner, tmp_jobs, monkeypatch):
    """`clean --force` drops the record without releasing the resource."""
    job = _create_test_job(tmp_jobs, status="failed")
    job.worktree_stash_ref = "abc123def456"
    job.worktree_repo_root = "/repo"
    JobManager().save_job(job)

    monkeypatch.setattr("tcd.worktree.stash_exists", lambda _root, _ref: True)

    result = runner.invoke(cli, ["clean"])
    assert "tcd merge" in result.output
    assert "tcd kill" in result.output


# ---------------------------------------------------------------------------
# tcd verify — "is the task done", separate from "is the turn idle"
# ---------------------------------------------------------------------------

def test_verify_reports_complete(runner, tmp_jobs, tmp_path):
    job = _create_test_job(tmp_jobs, status="running")
    job.cwd = str(tmp_path)
    (tmp_path / "deliverable.py").write_text("x")
    job.acceptance_files = ["deliverable.py"]
    JobManager().save_job(job)

    result = runner.invoke(cli, ["verify", job.id])
    assert result.exit_code == 0
    assert "passed" in result.output


def test_verify_reports_incomplete_with_the_failing_clause(runner, tmp_jobs, tmp_path):
    job = _create_test_job(tmp_jobs, status="running")
    job.cwd = str(tmp_path)
    job.acceptance_files = ["missing.py"]
    JobManager().save_job(job)

    result = runner.invoke(cli, ["verify", job.id])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "missing.py" in result.output


def test_verify_distinguishes_no_contract_from_success(runner, tmp_jobs):
    """Exit 0 must mean "verified done", never "nothing was checked"."""
    job = _create_test_job(tmp_jobs, status="running")

    result = runner.invoke(cli, ["verify", job.id])
    assert result.exit_code == 2
    assert "no acceptance contract" in result.output


def test_verify_missing_job(runner, tmp_jobs):
    result = runner.invoke(cli, ["verify", "deadbeef"])
    assert result.exit_code == 3


def test_verify_json_shape(runner, tmp_jobs, tmp_path):
    job = _create_test_job(tmp_jobs, status="running")
    job.cwd = str(tmp_path)
    job.acceptance_commands = ["exit 0"]
    JobManager().save_job(job)

    result = runner.invoke(cli, ["verify", job.id, "--json"])
    payload = json.loads(result.output)
    assert payload["task_state"] == "complete"
    assert payload["checks"][0]["kind"] == "command"


def test_start_rejects_require_commit_outside_a_git_repo(runner, tmp_jobs, tmp_path):
    result = runner.invoke(cli, ["start", "-p", "codex", "-m", "x", "-d", str(tmp_path), "--require-commit"])
    assert result.exit_code == 1
    assert "needs a git repository" in result.output


# ---------------------------------------------------------------------------
# Terminal-state hygiene
# ---------------------------------------------------------------------------

def test_check_does_not_report_a_failed_job_as_exit_zero(runner, tmp_jobs):
    """Exit 0 for a failed job scores as success in any aggregator."""
    job = _create_test_job(tmp_jobs, status="failed")

    result = runner.invoke(cli, ["check", job.id])
    assert result.exit_code == 4


def test_check_still_reports_completed_as_zero(runner, tmp_jobs):
    job = _create_test_job(tmp_jobs, status="completed")

    result = runner.invoke(cli, ["check", job.id])
    assert result.exit_code == 0


def test_clean_keeps_jobs_whose_tmux_session_is_still_alive(runner, tmp_jobs, monkeypatch):
    """merge marks a job completed without killing its session."""
    job = _create_test_job(tmp_jobs, status="completed")

    class FakeTmux:
        def list_sessions(self):
            return {job.tmux_session}

    monkeypatch.setattr("tcd.tmux_adapter.TmuxAdapter", lambda: FakeTmux())

    result = runner.invoke(cli, ["clean"])
    assert result.exit_code == 0
    assert "Cleaned 0 job(s)." in result.output
    assert "still alive" in result.output
    assert JobManager().load_job(job.id) is not None


def test_start_json_emits_machine_readable_job(runner, tmp_jobs, monkeypatch):
    class FakeProvider:
        tui_ready_indicator = "READY"
        def check_cli(self): return None
        def build_launch_command(self, job): return "fake"
        def build_prompt_wrapper(self, message, req_id): return message

    class FakeTmux:
        def create_session(self, session, cmd, cwd): return True
        def capture_pane(self, session, **kwargs): return "READY"
        def send_enter(self, session): return True
        def send_text(self, session, text): return True

    monkeypatch.setattr("tcd.cli._get_tmux", lambda: FakeTmux())
    monkeypatch.setattr("tcd.cli.get_provider", lambda provider: FakeProvider())
    monkeypatch.setattr("tcd.cli.time.sleep", lambda _s: None)

    result = runner.invoke(cli, ["start", "-p", "codex", "-m", "hi", "-d", "/tmp", "--json"])
    assert result.exit_code == 0

    payload = json.loads(result.output)
    assert payload["job_id"] == JobManager().list_jobs()[0].id
    assert payload["tmux_session"].startswith("tcd-codex-")
