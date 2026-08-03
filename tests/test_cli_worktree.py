"""CLI tests for worktree integration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from tcd.cli import cli
from tcd.job import Job, JobManager
from tcd.worktree import MergeResult


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


def _create_worktree_job(repo_root: Path, worktree_path: Path, *, persisted_repo_root: Path | None = None) -> Job:
    mgr = JobManager()
    job = mgr.create_job("codex", "test prompt", str(repo_root))
    job.status = "running"
    job.cwd = str(worktree_path)
    job.worktree_path = str(worktree_path)
    job.worktree_branch = "tcd/test-branch"
    job.worktree_repo_root = str(persisted_repo_root or repo_root)
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

        def capture_pane(self, session, **kwargs):
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


def test_start_worktree_rolls_back_on_tmux_create_failure(runner, tmp_jobs, monkeypatch):
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
            return False

    remove_worktree_mock = MagicMock()
    delete_branch_mock = MagicMock()

    monkeypatch.setattr("tcd.cli._get_tmux", lambda: FakeTmux())
    monkeypatch.setattr("tcd.cli.get_provider", lambda provider: FakeProvider())
    monkeypatch.setattr("tcd.worktree.is_git_repo", lambda _cwd: True)
    monkeypatch.setattr("tcd.worktree.create_worktree", lambda _cwd, _name: Path("/tmp/repo-wt-rollback-create"))
    monkeypatch.setattr("tcd.worktree.remove_worktree", remove_worktree_mock)
    monkeypatch.setattr("tcd.worktree.delete_branch", delete_branch_mock)
    monkeypatch.setattr("tcd.cli.subprocess.run", lambda *args, **kwargs: SimpleNamespace(stdout=""))

    result = runner.invoke(
        cli,
        ["start", "-p", "codex", "-m", "hello", "-d", "/tmp", "--worktree", "--wt-name", "rollback-create"],
    )
    assert result.exit_code == 1

    remove_worktree_mock.assert_called_once_with("/tmp/repo-wt-rollback-create")
    delete_branch_mock.assert_called_once_with(Path("/tmp"), "tcd/rollback-create")
    job = JobManager().list_jobs()[0]
    assert job.status == "failed"
    assert job.worktree_path is None
    assert job.worktree_branch is None


def test_start_worktree_rolls_back_on_prompt_send_failure(runner, tmp_jobs, monkeypatch):
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

    remove_worktree_mock = MagicMock()
    delete_branch_mock = MagicMock()

    monkeypatch.setattr("tcd.cli._get_tmux", lambda: FakeTmux())
    monkeypatch.setattr("tcd.cli.get_provider", lambda provider: FakeProvider())
    monkeypatch.setattr("tcd.worktree.is_git_repo", lambda _cwd: True)
    monkeypatch.setattr("tcd.worktree.create_worktree", lambda _cwd, _name: Path("/tmp/repo-wt-rollback-send"))
    monkeypatch.setattr("tcd.worktree.remove_worktree", remove_worktree_mock)
    monkeypatch.setattr("tcd.worktree.delete_branch", delete_branch_mock)
    monkeypatch.setattr("tcd.cli.subprocess.run", lambda *args, **kwargs: SimpleNamespace(stdout=""))
    monkeypatch.setattr("tcd.cli.time.sleep", lambda _seconds: None)

    result = runner.invoke(
        cli,
        ["start", "-p", "codex", "-m", "hello", "-d", "/tmp", "--worktree", "--wt-name", "rollback-send"],
    )
    assert result.exit_code == 1

    remove_worktree_mock.assert_called_once_with("/tmp/repo-wt-rollback-send")
    delete_branch_mock.assert_called_once_with(Path("/tmp"), "tcd/rollback-send")
    job = JobManager().list_jobs()[0]
    assert job.status == "failed"
    assert job.worktree_path is None
    assert job.worktree_branch is None


def test_merge_command_success(runner, tmp_jobs, tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)
    merge_branch_mock = MagicMock(return_value=MergeResult(success=True, stdout="Merge made by the 'ort' strategy."))
    remove_worktree_mock = MagicMock()
    delete_branch_mock = MagicMock()

    monkeypatch.setattr("tcd.worktree.is_git_repo", lambda _path: True)
    monkeypatch.setattr("tcd.worktree.merge_branch", merge_branch_mock)
    monkeypatch.setattr("tcd.worktree.remove_worktree", remove_worktree_mock)
    monkeypatch.setattr("tcd.worktree.delete_branch", delete_branch_mock)
    monkeypatch.setattr("tcd.worktree.branch_has_new_commits", lambda *a, **kw: True)

    result = runner.invoke(cli, ["merge", job.id])
    assert result.exit_code == 0
    assert "Merged" in result.output

    remove_worktree_mock.assert_called_once_with(str(worktree_path))
    delete_branch_mock.assert_called_once_with(repo_root, "tcd/test-branch")


def test_merge_command_squash(runner, tmp_jobs, tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)
    merge_branch_mock = MagicMock(return_value=MergeResult(success=True, stdout="Squash commit"))

    monkeypatch.setattr("tcd.worktree.is_git_repo", lambda _path: True)
    monkeypatch.setattr("tcd.worktree.merge_branch", merge_branch_mock)
    monkeypatch.setattr("tcd.worktree.remove_worktree", MagicMock())
    monkeypatch.setattr("tcd.worktree.delete_branch", MagicMock())
    monkeypatch.setattr("tcd.worktree.branch_has_new_commits", lambda *a, **kw: True)

    result = runner.invoke(cli, ["merge", job.id, "--squash", "--no-cleanup"])
    assert result.exit_code == 0
    merge_branch_mock.assert_called_once_with(repo_root, "tcd/test-branch", strategy="squash")
    assert "Squashed" in result.output
    assert "Commit it" in result.output


def test_merge_command_squash_keeps_the_source_until_it_is_committed(runner, tmp_jobs, tmp_path, monkeypatch):
    """`git merge --squash` stages without committing.

    Deleting the worktree and force-deleting the branch at that point would
    leave the only copy of the work in the index, one `git reset` from gone.
    """
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)
    merge_branch_mock = MagicMock(return_value=MergeResult(success=True, stdout="Squash commit"))
    remove_worktree_mock = MagicMock()
    delete_branch_mock = MagicMock()

    monkeypatch.setattr("tcd.worktree.is_git_repo", lambda _path: True)
    monkeypatch.setattr("tcd.worktree.merge_branch", merge_branch_mock)
    monkeypatch.setattr("tcd.worktree.remove_worktree", remove_worktree_mock)
    monkeypatch.setattr("tcd.worktree.delete_branch", delete_branch_mock)
    monkeypatch.setattr("tcd.worktree.branch_has_new_commits", lambda *a, **kw: True)

    result = runner.invoke(cli, ["merge", job.id, "--squash"])
    assert result.exit_code == 0
    assert "Squashed" in result.output
    assert "Commit it" in result.output

    remove_worktree_mock.assert_not_called()
    delete_branch_mock.assert_not_called()

    updated = JobManager().load_job(job.id)
    assert updated.worktree_path == str(worktree_path)
    assert updated.worktree_branch == "tcd/test-branch"


def test_merge_command_conflict(runner, tmp_jobs, tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)
    monkeypatch.setattr("tcd.worktree.is_git_repo", lambda _path: True)
    monkeypatch.setattr("tcd.worktree.merge_branch", lambda *args, **kwargs: MergeResult(success=False, stderr="CONFLICT"))
    monkeypatch.setattr("tcd.worktree.branch_has_new_commits", lambda *a, **kw: True)

    result = runner.invoke(cli, ["merge", job.id])
    assert result.exit_code != 0
    assert "Merge conflict" in result.output


def test_merge_command_no_cleanup(runner, tmp_jobs, tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)
    remove_worktree_mock = MagicMock()

    monkeypatch.setattr("tcd.worktree.is_git_repo", lambda _path: True)
    monkeypatch.setattr("tcd.worktree.merge_branch", lambda *args, **kwargs: MergeResult(success=True, stdout="Merge made"))
    monkeypatch.setattr("tcd.worktree.remove_worktree", remove_worktree_mock)
    monkeypatch.setattr("tcd.worktree.delete_branch", MagicMock())
    monkeypatch.setattr("tcd.worktree.branch_has_new_commits", lambda *a, **kw: True)

    result = runner.invoke(cli, ["merge", job.id, "--no-cleanup"])
    assert result.exit_code == 0
    remove_worktree_mock.assert_not_called()


def test_merge_command_no_new_commits(runner, tmp_jobs, tmp_path, monkeypatch):
    """Merge should fail early when branch has no new commits (AI forgot to commit)."""
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)

    monkeypatch.setattr("tcd.worktree.is_git_repo", lambda _path: True)
    monkeypatch.setattr("tcd.worktree.branch_has_new_commits", lambda *a, **kw: False)

    result = runner.invoke(cli, ["merge", job.id])
    assert result.exit_code != 0
    assert "no new commits" in result.output


def test_merge_command_falls_back_when_persisted_repo_root_invalid(runner, tmp_jobs, tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path, persisted_repo_root=tmp_path / "missing-repo")
    merge_branch_mock = MagicMock(return_value=MergeResult(success=True, stdout="Merge made"))

    monkeypatch.setattr("tcd.worktree.get_main_repo_root", lambda _path: repo_root)
    monkeypatch.setattr("tcd.worktree.merge_branch", merge_branch_mock)
    monkeypatch.setattr("tcd.worktree.remove_worktree", MagicMock())
    monkeypatch.setattr("tcd.worktree.delete_branch", MagicMock())
    monkeypatch.setattr("tcd.worktree.branch_has_new_commits", lambda *a, **kw: True)

    result = runner.invoke(cli, ["merge", job.id, "--no-cleanup"])

    assert result.exit_code == 0
    merge_branch_mock.assert_called_once_with(repo_root, "tcd/test-branch", strategy="merge")


class _FakeTmux:
    def session_exists(self, _session):
        return True

    def kill_session(self, _session):
        return True


def _kill_mocks(monkeypatch, *, dirty: bool, unmerged: bool):
    """Wire up kill's cleanup dependencies; returns (remove, delete) mocks."""
    remove_worktree_mock = MagicMock(return_value=True)
    delete_branch_mock = MagicMock()
    monkeypatch.setattr("tcd.cli._get_tmux", lambda: _FakeTmux())
    monkeypatch.setattr("tcd.worktree.remove_worktree", remove_worktree_mock)
    monkeypatch.setattr("tcd.worktree.delete_branch", delete_branch_mock)
    monkeypatch.setattr("tcd.worktree.worktree_is_dirty", lambda _p: dirty)
    monkeypatch.setattr("tcd.worktree.branch_has_new_commits", lambda _r, _b: unmerged)
    return remove_worktree_mock, delete_branch_mock


def test_kill_cleans_worktree_and_branch_when_nothing_is_at_stake(
    runner, tmp_jobs, tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)

    remove_worktree_mock, delete_branch_mock = _kill_mocks(monkeypatch, dirty=False, unmerged=False)

    result = runner.invoke(cli, ["kill", job.id])
    assert result.exit_code == 0
    remove_worktree_mock.assert_called_once_with(str(worktree_path))
    delete_branch_mock.assert_called_once_with(repo_root, "tcd/test-branch", force=False)

    updated = JobManager().load_job(job.id)
    assert updated is not None
    assert updated.worktree_path is None
    assert updated.worktree_branch is None


@pytest.mark.parametrize(
    ("dirty", "unmerged"),
    [(True, False), (False, True)],
    ids=["uncommitted-changes", "unmerged-commits"],
)
def test_kill_keeps_worktree_holding_unsaved_work(
    runner, tmp_jobs, tmp_path, monkeypatch, dirty, unmerged
):
    """`git worktree remove --force` would silently discard the agent's work."""
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)

    remove_worktree_mock, delete_branch_mock = _kill_mocks(monkeypatch, dirty=dirty, unmerged=unmerged)

    result = runner.invoke(cli, ["kill", job.id])
    assert result.exit_code == 0
    remove_worktree_mock.assert_not_called()
    delete_branch_mock.assert_not_called()
    assert "Kept worktree" in result.output

    updated = JobManager().load_job(job.id)
    assert updated is not None
    assert updated.worktree_path == str(worktree_path)
    assert updated.worktree_branch == "tcd/test-branch"


def test_kill_force_discards_worktree_with_unsaved_work(runner, tmp_jobs, tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)

    remove_worktree_mock, delete_branch_mock = _kill_mocks(monkeypatch, dirty=True, unmerged=True)

    result = runner.invoke(cli, ["kill", job.id, "--force"])
    assert result.exit_code == 0
    remove_worktree_mock.assert_called_once_with(str(worktree_path))
    delete_branch_mock.assert_called_once_with(repo_root, "tcd/test-branch", force=True)


def test_kill_restores_auto_stash_by_ref(runner, tmp_jobs, tmp_path, monkeypatch):
    """The stash belongs to the user's tree; killing the agent must return it."""
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "repo-wt-test"
    repo_root.mkdir()
    worktree_path.mkdir()
    job = _create_worktree_job(repo_root, worktree_path)
    job.worktree_stash_ref = "deadbeefcafe1234"
    JobManager().save_job(job)

    _kill_mocks(monkeypatch, dirty=False, unmerged=False)
    stash_pop_mock = MagicMock(return_value=True)
    monkeypatch.setattr("tcd.worktree.stash_pop", stash_pop_mock)

    result = runner.invoke(cli, ["kill", job.id])
    assert result.exit_code == 0
    stash_pop_mock.assert_called_once_with(repo_root, "deadbeefcafe1234")

    updated = JobManager().load_job(job.id)
    assert updated is not None
    assert updated.worktree_stash_restored is True


def test_help_contains_merge(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "merge" in result.output
