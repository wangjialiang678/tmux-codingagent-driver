"""Tests for the tcd doctor command and checks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from tcd.cli import cli
from tcd.job import Job
from tcd.tmux_adapter import TmuxNotFoundError


@pytest.fixture()
def runner():
    """Provide a Click CLI runner."""
    return CliRunner()


@pytest.fixture()
def doctor_env(tmp_path, monkeypatch):
    """Redirect doctor state and commands to deterministic fakes."""
    import tcd.doctor as doctor

    home = tmp_path / ".tcd"
    jobs_dir = home / "jobs"

    monkeypatch.setattr(doctor, "TCD_HOME", home)
    monkeypatch.setattr(doctor, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, f"{Path(args[0]).name} 1.2.3\n", ""),
    )
    return doctor, home, jobs_dir


class _FakeProvider:
    name = "fake"
    cli_command = "fake-cli"
    tui_ready_indicator = "READY"
    tui_stable_secs = 1.5
    verify_prompt_delivery = True
    supports_sandbox = False

    def check_cli(self) -> None:
        return None

    def build_launch_command(self, job: Job) -> str:
        return "fake-launch"


class _FakeTmux:
    tmux = "/fake/tmux"

    def __init__(self, sessions: set[str] | None = None) -> None:
        self.sessions = sessions or set()
        self.created: list[tuple[str, str, str]] = []
        self.killed: list[str] = []
        self.sent: list[tuple[str, str]] = []

    def check_tmux(self) -> None:
        return None

    def list_sessions(self) -> set[str]:
        return self.sessions

    def create_session(self, session: str, command: str, cwd: str) -> bool:
        self.created.append((session, command, cwd))
        return True

    def kill_session(self, session: str) -> bool:
        self.killed.append(session)
        return True

    def send_text(self, session: str, text: str) -> bool:
        self.sent.append((session, text))
        return True


def _configure_provider(monkeypatch, doctor, provider: _FakeProvider) -> None:
    monkeypatch.setattr(doctor, "list_providers", lambda: [provider.name])
    monkeypatch.setattr(doctor, "get_provider", lambda name: provider)


def _result(report, code: str):
    return next(result for result in report.results if result.code == code)


def test_static_checks_report_versions_assumptions_and_job_count(doctor_env, monkeypatch):
    doctor, home, jobs_dir = doctor_env
    provider = _FakeProvider()
    tmux = _FakeTmux()
    _configure_provider(monkeypatch, doctor, provider)
    monkeypatch.setattr(doctor, "TmuxAdapter", lambda: tmux)

    jobs_dir.mkdir(parents=True)
    job = Job("job-one", "fake", "completed", "prompt", "/tmp", "tcd-fake-job-one")
    (jobs_dir / "job-one.json").write_text(job.to_json())

    report = doctor.run_doctor()

    assert report.exit_code == 0
    assert _result(report, "TMUX").details == {"path": "/fake/tmux", "version": "tmux 1.2.3"}
    assert _result(report, "CLI").details == {
        "provider": "fake",
        "command": "fake-cli",
        "version": "fake-cli 1.2.3",
    }
    assert _result(report, "DETECTION_ASSUMPTIONS").details == {
        "provider": "fake",
        "tui_ready_indicator": "READY",
        "tui_stable_secs": 1.5,
        "verify_prompt_delivery": True,
        # Reported because it is the other pane string that silently rots when
        # an upstream CLI changes its wording.
        "working_markers": [],
        "supports_sandbox": False,
    }
    assert _result(report, "TCD_HOME").status == "pass"
    assert _result(report, "JOB_RECORDS").details == {"count": 1}
    assert home.exists()


def test_static_checks_mark_missing_tmux_as_error(doctor_env, monkeypatch):
    doctor, _, _ = doctor_env
    provider = _FakeProvider()
    _configure_provider(monkeypatch, doctor, provider)

    class MissingTmux:
        def check_tmux(self) -> None:
            raise TmuxNotFoundError("tmux not found")

    monkeypatch.setattr(doctor, "TmuxAdapter", MissingTmux)

    report = doctor.run_doctor()

    assert report.exit_code == 2
    assert _result(report, "TMUX").status == "error"
    assert "tmux not found" in _result(report, "TMUX").message


def test_static_checks_mark_missing_provider_cli_as_error(doctor_env, monkeypatch):
    doctor, _, _ = doctor_env

    class MissingCliProvider(_FakeProvider):
        def check_cli(self) -> None:
            raise FileNotFoundError("fake-cli not found in PATH")

    provider = MissingCliProvider()
    _configure_provider(monkeypatch, doctor, provider)
    monkeypatch.setattr(doctor, "TmuxAdapter", _FakeTmux)

    report = doctor.run_doctor()

    assert report.exit_code == 2
    assert _result(report, "CLI").status == "error"
    assert "fake-cli not found" in _result(report, "CLI").message


def test_static_checks_warn_for_ghost_jobs(doctor_env, monkeypatch):
    doctor, _, jobs_dir = doctor_env
    provider = _FakeProvider()
    _configure_provider(monkeypatch, doctor, provider)
    monkeypatch.setattr(doctor, "TmuxAdapter", _FakeTmux)

    jobs_dir.mkdir(parents=True)
    job = Job("ghost", "fake", "running", "prompt", "/tmp", "tcd-fake-ghost")
    (jobs_dir / "ghost.json").write_text(job.to_json())

    report = doctor.run_doctor()

    assert report.exit_code == 1
    ghosts = _result(report, "GHOST_JOBS")
    assert ghosts.status == "warning"
    assert ghosts.details == {"count": 1, "job_ids": ["ghost"]}
    assert "tcd jobs" in ghosts.message


def test_static_checks_mark_unwritable_tcd_home_as_error(doctor_env, monkeypatch):
    doctor, _, _ = doctor_env
    provider = _FakeProvider()
    _configure_provider(monkeypatch, doctor, provider)
    monkeypatch.setattr(doctor, "TmuxAdapter", _FakeTmux)

    def fail_tempfile(*args, **kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(doctor.tempfile, "NamedTemporaryFile", fail_tempfile)

    report = doctor.run_doctor()

    assert report.exit_code == 2
    assert _result(report, "TCD_HOME").status == "error"
    assert "read-only filesystem" in _result(report, "TCD_HOME").message


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [("pass", 0), ("warning", 1), ("error", 2)],
)
def test_report_exit_codes(status, expected_exit):
    from tcd.doctor import DoctorReport, DoctorResult

    report = DoctorReport(results=[DoctorResult(code="TEST", status=status, message="test")])

    assert report.exit_code == expected_exit


def test_doctor_cli_json_output_and_options(runner, monkeypatch):
    from tcd.doctor import DoctorReport, DoctorResult

    report = DoctorReport(
        results=[DoctorResult(code="TMUX", status="pass", message="tmux is available")]
    )
    called: dict[str, object] = {}

    def fake_run_doctor(*, live: bool, provider: str | None, timeout: int):
        called.update(live=live, provider=provider, timeout=timeout)
        return report

    monkeypatch.setattr("tcd.cli.ensure_dirs", lambda: None)
    monkeypatch.setattr("tcd.cli.run_doctor", fake_run_doctor)

    result = runner.invoke(cli, ["doctor", "--live", "--provider", "codex", "--timeout", "12", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == report.to_dict()
    assert called == {"live": True, "provider": "codex", "timeout": 12}


def test_doctor_cli_preserves_report_exit_code(runner, monkeypatch):
    from tcd.doctor import DoctorReport, DoctorResult

    monkeypatch.setattr("tcd.cli.ensure_dirs", lambda: None)

    for status, expected_exit in (("pass", 0), ("warning", 1), ("error", 2)):
        report = DoctorReport(results=[DoctorResult(code="TEST", status=status, message="message")])
        monkeypatch.setattr("tcd.cli.run_doctor", lambda **kwargs: report)

        result = runner.invoke(cli, ["doctor"])

        assert result.exit_code == expected_exit


def test_doctor_cli_help_runs(runner, monkeypatch):
    monkeypatch.setattr("tcd.cli.ensure_dirs", lambda: None)

    result = runner.invoke(cli, ["doctor", "--help"])

    assert result.exit_code == 0
    assert "--live" in result.output
    assert "--json" in result.output


def test_live_readiness_drift_cleans_session_and_temp_directory(doctor_env, monkeypatch):
    doctor, _, _ = doctor_env
    provider = _FakeProvider()
    tmux = _FakeTmux()
    _configure_provider(monkeypatch, doctor, provider)
    monkeypatch.setattr(doctor, "TmuxAdapter", lambda: tmux)
    monkeypatch.setattr(doctor, "wait_for_tui", lambda *args, **kwargs: (False, 90_000, False))

    report = doctor.run_doctor(live=True, provider="fake", timeout=5)

    readiness = _result(report, "READINESS_DRIFT")
    assert readiness.status == "error"
    assert "ready indicator" in readiness.message
    assert len(tmux.created) == 1
    assert tmux.killed == [tmux.created[0][0]]
    assert not Path(tmux.created[0][2]).exists()
    assert tmux.sent == []


def test_live_delivery_drift_is_reported(doctor_env, monkeypatch):
    doctor, _, _ = doctor_env
    provider = _FakeProvider()
    tmux = _FakeTmux()
    _configure_provider(monkeypatch, doctor, provider)
    monkeypatch.setattr(doctor, "TmuxAdapter", lambda: tmux)
    monkeypatch.setattr(doctor, "wait_for_tui", lambda *args, **kwargs: (True, 10, False))
    monkeypatch.setattr(doctor, "verify_prompt_delivery", lambda *args, **kwargs: False)

    report = doctor.run_doctor(live=True, provider="fake", timeout=5)

    assert _result(report, "DELIVERY_DRIFT").status == "error"
    assert tmux.sent
    assert tmux.killed == [tmux.created[0][0]]


def test_live_check_runs_when_cli_version_is_unknown(doctor_env, monkeypatch):
    doctor, _, _ = doctor_env
    provider = _FakeProvider()
    tmux = _FakeTmux()
    _configure_provider(monkeypatch, doctor, provider)
    monkeypatch.setattr(doctor, "TmuxAdapter", lambda: tmux)
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "unsupported version flag"),
    )
    monkeypatch.setattr(doctor, "wait_for_tui", lambda *args, **kwargs: (True, 10, False))
    monkeypatch.setattr(doctor, "verify_prompt_delivery", lambda *args, **kwargs: True)

    report = doctor.run_doctor(live=True, provider="fake", timeout=5)

    assert _result(report, "CLI").status == "warning"
    assert _result(report, "LIVE_DELIVERY").status == "pass"
    assert tmux.created


def test_live_cleanup_runs_when_launch_raises(doctor_env, monkeypatch):
    doctor, _, _ = doctor_env

    class ExplodingProvider(_FakeProvider):
        def build_launch_command(self, job: Job) -> str:
            raise RuntimeError("launch command failed")

    provider = ExplodingProvider()
    tmux = _FakeTmux()
    _configure_provider(monkeypatch, doctor, provider)
    monkeypatch.setattr(doctor, "TmuxAdapter", lambda: tmux)

    report = doctor.run_doctor(live=True, provider="fake", timeout=5)

    assert _result(report, "LIVE_CHECK_FAILED").status == "error"
    assert "launch command failed" in _result(report, "LIVE_CHECK_FAILED").message
    assert len(tmux.killed) == 1
    assert tmux.created == []


def test_get_version_falls_back_to_dash_v():
    """tmux only accepts -V; reporting it as "version unknown" is noise."""
    from tcd.doctor import _get_version

    version, error = _get_version("tmux")
    assert error is None
    assert version and "tmux" in version.lower()
