"""CLI tests for worktree integration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from tcd.cli import cli
from tcd.job import Job, JobManager


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def tmp_jobs(tmp_path, monkeypatch):
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
    return jobs_dir


def _create_worktree_job() -> Job:
    mgr = JobManager()
    job = mgr.create_job("codex", "test prompt", "/repo")
    job.status = "running"
    job.cwd = "/repo-wt-test"
    job.worktree_path = "/repo-wt-test"
    job.worktree_branch = "tcd/test-branch"
    mgr.save_job(job)
    return job


def _mock_start_dependencies(monkeypatch, worktree_path: str) -> MagicMock:
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
            return True

    create_worktree_mock = MagicMock(return_value=Path(worktree_path))
    monkeypatch.setattr("tcd.cli._get_tmux", lambda: FakeTmux())
    monkeypatch.setattr("tcd.cli.get_provider", lambda provider: FakeProvider())
    monkeypatch.setattr("tcd.worktree.is_git_repo", lambda _cwd: True)
    monkeypatch.setattr("tcd.worktree.create_worktree", create_worktree_mock)
    monkeypatch.setattr("tcd.cli.subprocess.run", lambda *args, **kwargs: SimpleNamespace(stdout=""))
    monkeypatch.setattr("tcd.cli.time.sleep", lambda _seconds: None)
    return create_worktree_mock


def test_start_worktree_flag_parsed(runner, tmp_jobs, monkeypatch):
    create_worktree_mock = _mock_start_dependencies(monkeypatch, "/tmp/repo-wt-flag")

    result = runner.invoke(
        cli,
        ["start", "-p", "codex", "-m", "hello", "-d", "/tmp", "--worktree"],
    )
    assert result.exit_code == 0

    jobs = JobManager().list_jobs()
    assert len(jobs) == 1
    create_worktree_mock.assert_called_once_with("/tmp", jobs[0].id)


def test_start_wt_name_option(runner, tmp_jobs, monkeypatch):
    create_worktree_mock = _mock_start_dependencies(monkeypatch, "/tmp/repo-wt-custom")

    result = runner.invoke(
        cli,
        ["start", "-p", "codex", "-m", "hello", "-d", "/tmp", "--worktree", "--wt-name", "custom-name"],
    )
    assert result.exit_code == 0

    create_worktree_mock.assert_called_once_with("/tmp", "custom-name")
    job = JobManager().list_jobs()[0]
    assert job.worktree_branch == "tcd/custom-name"


def test_merge_command_success(runner, tmp_jobs, monkeypatch):
    job = _create_worktree_job()
    merge_branch_mock = MagicMock(return_value=True)
    remove_worktree_mock = MagicMock()
    delete_branch_mock = MagicMock()

    monkeypatch.setattr("tcd.worktree.get_repo_root", lambda _cwd: Path("/repo"))
    monkeypatch.setattr("tcd.worktree.merge_branch", merge_branch_mock)
    monkeypatch.setattr("tcd.worktree.remove_worktree", remove_worktree_mock)
    monkeypatch.setattr("tcd.worktree.delete_branch", delete_branch_mock)

    result = runner.invoke(cli, ["merge", job.id])
    assert result.exit_code == 0
    assert "Merged" in result.output

    remove_worktree_mock.assert_called_once_with("/repo-wt-test")
    delete_branch_mock.assert_called_once_with(Path("/repo"), "tcd/test-branch")


def test_merge_command_squash(runner, tmp_jobs, monkeypatch):
    job = _create_worktree_job()
    merge_branch_mock = MagicMock(return_value=True)

    monkeypatch.setattr("tcd.worktree.get_repo_root", lambda _cwd: Path("/repo"))
    monkeypatch.setattr("tcd.worktree.merge_branch", merge_branch_mock)
    monkeypatch.setattr("tcd.worktree.remove_worktree", MagicMock())
    monkeypatch.setattr("tcd.worktree.delete_branch", MagicMock())

    result = runner.invoke(cli, ["merge", job.id, "--squash", "--no-cleanup"])
    assert result.exit_code == 0
    merge_branch_mock.assert_called_once_with(Path("/repo"), "tcd/test-branch", strategy="squash")
    assert "(squash)" in result.output


def test_merge_command_conflict(runner, tmp_jobs, monkeypatch):
    job = _create_worktree_job()
    monkeypatch.setattr("tcd.worktree.get_repo_root", lambda _cwd: Path("/repo"))
    monkeypatch.setattr("tcd.worktree.merge_branch", lambda *args, **kwargs: False)

    result = runner.invoke(cli, ["merge", job.id])
    assert result.exit_code != 0
    assert "Merge conflict" in result.output


def test_merge_command_no_cleanup(runner, tmp_jobs, monkeypatch):
    job = _create_worktree_job()
    remove_worktree_mock = MagicMock()

    monkeypatch.setattr("tcd.worktree.get_repo_root", lambda _cwd: Path("/repo"))
    monkeypatch.setattr("tcd.worktree.merge_branch", lambda *args, **kwargs: True)
    monkeypatch.setattr("tcd.worktree.remove_worktree", remove_worktree_mock)
    monkeypatch.setattr("tcd.worktree.delete_branch", MagicMock())

    result = runner.invoke(cli, ["merge", job.id, "--no-cleanup"])
    assert result.exit_code == 0
    remove_worktree_mock.assert_not_called()


def test_help_contains_merge(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "merge" in result.output
