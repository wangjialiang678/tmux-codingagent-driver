"""Worktree integration tests for Job and SDK."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tcd.job import Job
from tcd.sdk import TCD, TCDError


def _provider_mock() -> MagicMock:
    prov = MagicMock()
    prov.check_cli.return_value = None
    prov.build_launch_command.return_value = "codex"
    prov.build_prompt_wrapper.return_value = "wrapped prompt"
    prov.tui_ready_indicator = "›"
    return prov


@pytest.fixture
def mock_tmux():
    with patch("tcd.sdk.TmuxAdapter") as MockTmux:
        mock = MockTmux.return_value
        mock.check_tmux.return_value = None
        mock.session_exists.return_value = True
        mock.create_session.return_value = True
        mock.send_text.return_value = True
        mock.send_enter.return_value = True
        mock.capture_pane.return_value = "Type your message"
        mock.kill_session.return_value = True
        yield mock


@pytest.fixture
def sdk(mock_tmux):
    with patch("tcd.sdk.ensure_dirs"), patch("tcd.sdk.JobManager") as MockMgr:
        mgr = MockMgr.return_value

        def _create_job(provider, prompt, cwd, *, model=None, timeout_minutes=60, sandbox=None):
            return Job(
                id="test123",
                provider=provider,
                status="pending",
                prompt=prompt,
                cwd=cwd,
                tmux_session=f"tcd-{provider}-test123",
                model=model,
                timeout_minutes=timeout_minutes,
                sandbox=sandbox,
            )

        mgr.create_job.side_effect = _create_job
        mgr.save_job.return_value = None

        instance = TCD()
        instance._mgr = mgr
        yield instance


def test_job_worktree_fields():
    job = Job(
        id="job12345",
        provider="codex",
        status="pending",
        prompt="test prompt",
        cwd="/repo",
        tmux_session="tcd-codex-job12345",
        worktree_path="/repo-wt-job12345",
        worktree_branch="tcd/job12345",
    )

    restored = Job.from_json(job.to_json())
    assert restored.worktree_path == "/repo-wt-job12345"
    assert restored.worktree_branch == "tcd/job12345"


def test_job_backward_compat():
    legacy = {
        "id": "job12345",
        "provider": "codex",
        "status": "pending",
        "prompt": "legacy payload",
        "cwd": "/repo",
        "tmux_session": "tcd-codex-job12345",
    }

    job = Job.from_dict(legacy)
    assert job.worktree_path is None
    assert job.worktree_branch is None
    assert job.worktree_repo_root is None


def test_start_worktree_creates_worktree(sdk):
    provider = _provider_mock()

    with (
        patch("tcd.sdk.get_provider", return_value=provider),
        patch.object(TCD, "_wait_for_tui", return_value=(True, 5, False)),
        patch("tcd.worktree.is_git_repo", return_value=True),
        patch("tcd.worktree.create_worktree", return_value=Path("/repo-wt-test123")),
        patch("tcd.sdk.subprocess.run", return_value=MagicMock(stdout="")),
    ):
        job = sdk.start("codex", "do work", "/repo", worktree=True)

    assert job.worktree_path == "/repo-wt-test123"
    assert job.worktree_branch == "tcd/test123"
    assert job.cwd == "/repo-wt-test123"
    sdk._tmux.create_session.assert_called_once_with("tcd-codex-test123", "codex", "/repo-wt-test123")


def test_start_worktree_not_git_repo(sdk):
    provider = _provider_mock()

    with (
        patch("tcd.sdk.get_provider", return_value=provider),
        patch("tcd.worktree.is_git_repo", return_value=False),
    ):
        with pytest.raises(TCDError, match="cwd is not a git repository"):
            sdk.start("codex", "do work", "/repo", worktree=True)


def test_start_worktree_uncommitted_changes(sdk):
    provider = _provider_mock()

    with (
        patch("tcd.sdk.get_provider", return_value=provider),
        patch("tcd.worktree.is_git_repo", return_value=True),
        patch("tcd.worktree.create_worktree") as mock_create_worktree,
        patch("tcd.sdk.subprocess.run", return_value=MagicMock(stdout=" M changed.py\n")),
    ):
        with pytest.raises(TCDError, match="uncommitted changes in working directory"):
            sdk.start("codex", "do work", "/repo", worktree=True)

    mock_create_worktree.assert_not_called()


def test_start_worktree_rolls_back_on_tmux_create_failure(sdk):
    provider = _provider_mock()
    sdk._tmux.create_session.return_value = False
    job = Job(
        id="test123",
        provider="codex",
        status="pending",
        prompt="do work",
        cwd="/repo",
        tmux_session="tcd-codex-test123",
    )
    sdk._mgr.create_job.side_effect = None
    sdk._mgr.create_job.return_value = job

    with (
        patch("tcd.sdk.get_provider", return_value=provider),
        patch("tcd.worktree.is_git_repo", return_value=True),
        patch("tcd.worktree.create_worktree", return_value=Path("/repo-wt-test123")),
        patch("tcd.sdk.subprocess.run", return_value=MagicMock(stdout="")),
        patch("tcd.worktree.remove_worktree") as mock_remove,
        patch("tcd.worktree.delete_branch") as mock_delete,
    ):
        with pytest.raises(TCDError, match="Failed to create tmux session"):
            sdk.start("codex", "do work", "/repo", worktree=True)

    mock_remove.assert_called_once_with("/repo-wt-test123")
    mock_delete.assert_called_once_with(Path("/repo"), "tcd/test123")
    assert job.worktree_path is None
    assert job.worktree_branch is None


def test_start_worktree_rolls_back_on_prompt_send_failure(sdk):
    provider = _provider_mock()
    sdk._tmux.send_text.return_value = False
    job = Job(
        id="test123",
        provider="codex",
        status="pending",
        prompt="do work",
        cwd="/repo",
        tmux_session="tcd-codex-test123",
    )
    sdk._mgr.create_job.side_effect = None
    sdk._mgr.create_job.return_value = job

    with (
        patch("tcd.sdk.get_provider", return_value=provider),
        patch.object(TCD, "_wait_for_tui", return_value=(True, 5, False)),
        patch("tcd.worktree.is_git_repo", return_value=True),
        patch("tcd.worktree.create_worktree", return_value=Path("/repo-wt-test123")),
        patch("tcd.sdk.subprocess.run", return_value=MagicMock(stdout="")),
        patch("tcd.worktree.remove_worktree") as mock_remove,
        patch("tcd.worktree.delete_branch") as mock_delete,
    ):
        with pytest.raises(TCDError, match="Failed to send prompt to tmux session"):
            sdk.start("codex", "do work", "/repo", worktree=True)

    mock_remove.assert_called_once_with("/repo-wt-test123")
    mock_delete.assert_called_once_with(Path("/repo"), "tcd/test123")
    assert job.worktree_path is None
    assert job.worktree_branch is None


def test_merge_worktree_success(sdk):
    job = Job(
        id="test123",
        provider="codex",
        status="running",
        prompt="test",
        cwd="/repo-wt-test123",
        tmux_session="tcd-codex-test123",
        worktree_path="/repo-wt-test123",
        worktree_branch="tcd/test123",
        worktree_repo_root="/repo",
    )
    sdk._mgr.load_job.return_value = job

    with (
        patch("tcd.worktree.merge_branch", return_value=True),
        patch("tcd.worktree.remove_worktree") as mock_remove,
        patch("tcd.worktree.delete_branch") as mock_delete,
    ):
        merged = sdk.merge_worktree("test123")

    assert merged is True
    mock_remove.assert_called_once_with("/repo-wt-test123")
    mock_delete.assert_called_once_with(Path("/repo"), "tcd/test123")
    assert job.worktree_path is None
    assert job.worktree_branch is None
    sdk._mgr.save_job.assert_called_once_with(job)


def test_merge_worktree_conflict(sdk):
    job = Job(
        id="test123",
        provider="codex",
        status="running",
        prompt="test",
        cwd="/repo-wt-test123",
        tmux_session="tcd-codex-test123",
        worktree_path="/repo-wt-test123",
        worktree_branch="tcd/test123",
        worktree_repo_root="/repo",
    )
    sdk._mgr.load_job.return_value = job

    with (
        patch("tcd.worktree.merge_branch", return_value=False),
        patch("tcd.worktree.remove_worktree") as mock_remove,
        patch("tcd.worktree.delete_branch") as mock_delete,
    ):
        merged = sdk.merge_worktree("test123")

    assert merged is False
    mock_remove.assert_not_called()
    mock_delete.assert_not_called()
    assert job.worktree_path == "/repo-wt-test123"
    assert job.worktree_branch == "tcd/test123"
    sdk._mgr.save_job.assert_not_called()


def test_merge_worktree_squash_cleanup_forces_branch_delete(sdk):
    job = Job(
        id="test123",
        provider="codex",
        status="running",
        prompt="test",
        cwd="/repo-wt-test123",
        tmux_session="tcd-codex-test123",
        worktree_path="/repo-wt-test123",
        worktree_branch="tcd/test123",
        worktree_repo_root="/repo",
    )
    sdk._mgr.load_job.return_value = job

    with (
        patch("tcd.worktree.merge_branch", return_value=True),
        patch("tcd.worktree.remove_worktree"),
        patch("tcd.worktree.delete_branch") as mock_delete,
    ):
        merged = sdk.merge_worktree("test123", strategy="squash")

    assert merged is True
    mock_delete.assert_called_once_with(Path("/repo"), "tcd/test123", force=True)


def test_merge_worktree_no_worktree(sdk):
    job = Job(
        id="test123",
        provider="codex",
        status="running",
        prompt="test",
        cwd="/repo",
        tmux_session="tcd-codex-test123",
    )
    sdk._mgr.load_job.return_value = job

    with pytest.raises(TCDError, match="has no worktree"):
        sdk.merge_worktree("test123")


def test_kill_cleans_worktree(sdk):
    job = Job(
        id="test123",
        provider="codex",
        status="running",
        prompt="test",
        cwd="/repo-wt-test123",
        tmux_session="tcd-codex-test123",
        worktree_path="/repo-wt-test123",
        worktree_branch="tcd/test123",
        worktree_repo_root="/repo",
    )
    sdk._mgr.load_job.return_value = job

    with (
        patch("tcd.worktree.remove_worktree") as mock_remove,
        patch("tcd.worktree.delete_branch") as mock_delete,
    ):
        sdk.kill("test123")

    mock_remove.assert_called_once_with("/repo-wt-test123")
    mock_delete.assert_called_once_with(Path("/repo"), "tcd/test123")
    assert job.worktree_path is None
    assert job.worktree_branch is None


def test_kill_no_worktree(sdk):
    job = Job(
        id="test123",
        provider="codex",
        status="running",
        prompt="test",
        cwd="/repo",
        tmux_session="tcd-codex-test123",
    )
    sdk._mgr.load_job.return_value = job

    sdk.kill("test123")
    assert job.status == "failed"


def test_worktree_created_event(sdk):
    provider = _provider_mock()

    with (
        patch("tcd.sdk.get_provider", return_value=provider),
        patch.object(TCD, "_wait_for_tui", return_value=(True, 5, False)),
        patch("tcd.worktree.is_git_repo", return_value=True),
        patch("tcd.worktree.create_worktree", return_value=Path("/repo-wt-test123")),
        patch("tcd.sdk.subprocess.run", return_value=MagicMock(stdout="")),
        patch("tcd.sdk.emit") as mock_emit,
    ):
        sdk.start("codex", "do work", "/repo", worktree=True)

    assert any(call.args[:2] == ("test123", "job.worktree_created") for call in mock_emit.call_args_list)


def test_worktree_merged_event(sdk):
    job = Job(
        id="test123",
        provider="codex",
        status="running",
        prompt="test",
        cwd="/repo-wt-test123",
        tmux_session="tcd-codex-test123",
        worktree_path="/repo-wt-test123",
        worktree_branch="tcd/test123",
        worktree_repo_root="/repo",
    )
    sdk._mgr.load_job.return_value = job

    with (
        patch("tcd.worktree.merge_branch", return_value=True),
        patch("tcd.worktree.remove_worktree"),
        patch("tcd.worktree.delete_branch"),
        patch("tcd.sdk.emit") as mock_emit,
    ):
        sdk.merge_worktree("test123")

    assert any(call.args[:2] == ("test123", "job.worktree_merged") for call in mock_emit.call_args_list)
