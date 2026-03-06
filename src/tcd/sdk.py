"""Python SDK for tcd — programmatic access to AI CLI jobs."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Literal

from tcd.collector import ResponseCollector
from tcd.config import ensure_dirs, job_signal_path
from tcd.diagnostics import Warning as DiagnosticWarning, diagnose
from tcd.event_log import emit
from tcd.job import Job, JobManager, _now_iso
from tcd.provider import get_provider
from tcd.tmux_adapter import TmuxAdapter, TmuxNotFoundError

_MARKER_PROVIDERS = {"claude", "gemini"}


class TCDError(Exception):
    """Base error for TCD SDK."""


class JobNotFoundError(TCDError):
    """Job does not exist."""


class JobNotRunningError(TCDError):
    """Job is not in running state."""


class TimeoutError(TCDError):
    """Wait operation timed out."""


@dataclass
class CheckResult:
    """Result of a completion check."""
    state: Literal["idle", "working", "context_limit", "completed", "failed", "not_found"]
    last_agent_message: str | None = None


@dataclass
class DiagnosticCheckResult(CheckResult):
    """Completion check result with diagnostics and pane tail context."""
    warnings: list[DiagnosticWarning] = field(default_factory=list)
    pane_tail: str = ""


class TCD:
    """High-level Python API for tcd operations.

    Usage::

        from tcd import TCD

        tcd = TCD()
        job = tcd.start("claude", "Fix the bug in main.py", cwd="/path/to/project")
        tcd.wait(job.id, timeout=300)
        output = tcd.output(job.id)
        print(output)
    """

    def __init__(self) -> None:
        ensure_dirs()
        self._mgr = JobManager()
        self._tmux = TmuxAdapter()
        try:
            self._tmux.check_tmux()
        except TmuxNotFoundError as e:
            raise TCDError(str(e)) from e

    def start(
        self,
        provider: str,
        prompt: str,
        cwd: str = ".",
        *,
        model: str | None = None,
        timeout: int = 60,
        sandbox: str | None = None,
        worktree: bool = False,
        worktree_name: str | None = None,
    ) -> Job:
        """Start a new AI job.

        Args:
            provider: AI CLI provider name ("codex", "claude", "gemini").
            prompt: Task prompt.
            cwd: Working directory.
            model: Model name override.
            timeout: Timeout in minutes.

        Returns:
            The created Job object.

        Raises:
            TCDError: On provider or tmux errors.
        """
        import os

        cwd = os.path.abspath(cwd)

        try:
            prov = get_provider(provider)
        except ValueError as e:
            raise TCDError(str(e)) from e

        if hasattr(prov, "check_cli"):
            try:
                prov.check_cli()
            except FileNotFoundError as e:
                raise TCDError(str(e)) from e

        job = self._mgr.create_job(provider, prompt, cwd, model=model, timeout_minutes=timeout, sandbox=sandbox)
        if worktree:
            from tcd.worktree import WorktreeError, create_worktree, is_git_repo

            if not is_git_repo(cwd):
                raise TCDError("cwd is not a git repository")

            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd,
                capture_output=True,
                text=True,
            )
            if status.stdout.strip():
                raise TCDError("uncommitted changes in working directory")

            name = worktree_name or job.id
            try:
                wt_path = create_worktree(cwd, name)
            except WorktreeError as e:
                raise TCDError(str(e)) from e

            job.worktree_repo_root = cwd
            cwd = str(wt_path)
            job.cwd = cwd
            job.worktree_path = cwd
            job.worktree_branch = f"tcd/{name}"
            self._mgr.save_job(job)
            emit(job.id, "job.worktree_created", worktree_path=str(wt_path), branch=f"tcd/{name}")

        try:
            emit(job.id, "job.created", provider=provider, sandbox=sandbox, cwd=cwd, model=model)

            launch_cmd = prov.build_launch_command(job)
            if not self._tmux.create_session(job.tmux_session, launch_cmd, cwd):
                job.status = "failed"
                job.error = "tmux session creation failed"
                self._mgr.save_job(job)
                raise TCDError("Failed to create tmux session")

            job.status = "running"
            job.started_at = _now_iso()
            job.turn_state = "working"
            self._mgr.save_job(job)

            # Wait for TUI readiness
            tui_ready, elapsed_ms, trust_handled = self._wait_for_tui(job, prov)
            if tui_ready:
                emit(job.id, "job.tui_ready", elapsed_ms=elapsed_ms, trust_handled=trust_handled)
            else:
                emit(job.id, "job.tui_timeout", elapsed_ms=elapsed_ms, trust_handled=trust_handled)

            # Inject prompt
            req_id = f"{job.id}-0-{int(time.time())}"
            wrapped = prov.build_prompt_wrapper(prompt, req_id)
            if not self._tmux.send_text(job.tmux_session, wrapped):
                raise TCDError("Failed to send prompt to tmux session")
            emit(job.id, "job.prompt_sent", bytes=len(wrapped.encode("utf-8")), req_id=req_id)

            return job
        except Exception as exc:
            if job.status != "failed":
                job.status = "failed"
            if not job.error:
                job.error = str(exc)
            if not job.completed_at:
                job.completed_at = _now_iso()
            self._mgr.save_job(job)

            if job.worktree_path and job.worktree_branch:
                try:
                    from pathlib import Path

                    from tcd.worktree import delete_branch, get_main_repo_root, remove_worktree

                    repo_root = Path(job.worktree_repo_root) if job.worktree_repo_root else get_main_repo_root(job.cwd)
                    worktree_path = job.worktree_path
                    remove_worktree(worktree_path)
                    delete_branch(repo_root, job.worktree_branch)
                    emit(job.id, "job.worktree_removed", worktree_path=worktree_path)
                    job.worktree_path = None
                    job.worktree_branch = None
                    self._mgr.save_job(job)
                except Exception:
                    pass  # best-effort rollback

            if isinstance(exc, TCDError):
                raise
            raise TCDError(str(exc)) from exc

    def check(self, job_id: str) -> CheckResult:
        """Non-blocking completion check.

        Returns:
            CheckResult with state and optional last agent message.

        Raises:
            JobNotFoundError: If job doesn't exist.
        """
        job = self._mgr.load_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} not found")

        self._refresh_status(job)

        if job.status == "completed":
            return CheckResult(state="completed")
        if job.status == "failed":
            return CheckResult(state="failed")

        try:
            prov = get_provider(job.provider)
            result = prov.detect_completion(job)
        except (OSError, ValueError, KeyError):
            result = None

        if result is not None:
            if result.state == "idle":
                completed_turn = job.turn_count
                self._advance_turn_if_needed(job)
                job.turn_state = "idle"
                if result.last_agent_message:
                    job.last_agent_message = result.last_agent_message
                self._accumulate_tokens(job, result.tokens)
                self._mgr.save_job(job)
                emit(job.id, "job.checked", state="idle")
                emit(job.id, "job.turn_complete", turn=completed_turn, **({"tokens": result.tokens} if result.tokens else {}))
                return CheckResult(state="idle", last_agent_message=result.last_agent_message)
            elif result.state == "context_limit":
                completed_turn = job.turn_count
                self._advance_turn_if_needed(job)
                job.turn_state = "context_limit"
                self._accumulate_tokens(job, result.tokens)
                self._mgr.save_job(job)
                emit(job.id, "job.checked", state="context_limit")
                emit(job.id, "job.turn_complete", turn=completed_turn, **({"tokens": result.tokens} if result.tokens else {}))
                return CheckResult(state="context_limit")

        emit(job.id, "job.checked", state="working")
        return CheckResult(state="working")

    def check_with_diagnostics(self, job_id: str) -> DiagnosticCheckResult:
        """Run check() and include rule-based diagnostics + pane tail."""
        result = self.check(job_id)

        job = self._mgr.load_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} not found")

        pane = self._tmux.capture_pane(job.tmux_session) or ""
        pane_tail = "\n".join(pane.splitlines()[-5:])
        warnings = diagnose(job, pane_tail=pane_tail or None)

        return DiagnosticCheckResult(
            state=result.state,
            last_agent_message=result.last_agent_message,
            warnings=warnings,
            pane_tail=pane_tail,
        )

    def wait(self, job_id: str, timeout: int = 300) -> CheckResult:
        """Block until job completes or times out.

        Args:
            job_id: Job ID.
            timeout: Max wait time in seconds.

        Returns:
            CheckResult with final state.

        Raises:
            JobNotFoundError: If job doesn't exist.
            TimeoutError: If timeout is reached.
        """
        job = self._mgr.load_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} not found")

        deadline = time.time() + timeout
        poll_interval = 2

        while time.time() < deadline:
            result = self.check(job_id)
            if result.state in ("idle", "completed", "failed"):
                return result
            time.sleep(poll_interval)

        raise TimeoutError(f"Timed out after {timeout}s waiting for job {job_id}")

    def output(self, job_id: str, *, full: bool = False, raw: bool = False) -> str | None:
        """Get job output.

        Args:
            job_id: Job ID.
            full: Full scrollback output.
            raw: Raw output (no ANSI cleaning).

        Returns:
            Output text or None if unavailable.

        Raises:
            JobNotFoundError: If job doesn't exist.
        """
        job = self._mgr.load_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} not found")

        collector = ResponseCollector()
        if raw:
            return collector.collect_raw(job)
        elif full:
            return collector.collect_full(job)
        else:
            return collector.collect(job)

    def send(self, job_id: str, message: str) -> None:
        """Send a follow-up message to a running job.

        Args:
            job_id: Job ID.
            message: The message to send.

        Raises:
            JobNotFoundError: If job doesn't exist.
            JobNotRunningError: If job is not running.
        """
        job = self._mgr.load_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} not found")
        if job.status != "running":
            raise JobNotRunningError(f"Job {job_id} is not running (status={job.status})")

        # Clear signal file for new turn
        signal = job_signal_path(job.id)
        signal.unlink(missing_ok=True)

        prov = get_provider(job.provider)
        req_id = f"{job.id}-{job.turn_count}-{int(time.time())}"
        wrapped = prov.build_prompt_wrapper(message, req_id)
        if not self._tmux.send_text(job.tmux_session, wrapped):
            raise TCDError("Failed to send message to tmux session")
        emit(
            job.id,
            "job.message_sent",
            bytes=len(wrapped.encode("utf-8")),
            req_id=req_id,
            turn=job.turn_count,
        )

        job.turn_state = "working"
        self._mgr.save_job(job)

    def jobs(self, *, status: str | None = None) -> list[Job]:
        """List jobs, optionally filtered by status."""
        return self._mgr.list_jobs(status_filter=status)

    def merge_worktree(
        self,
        job_id: str,
        *,
        strategy: str = "merge",
        cleanup: bool = True,
    ) -> bool:
        """Merge a worktree job's branch back and clean up.

        Returns True if merge succeeded, False if there were conflicts.
        """
        from pathlib import Path

        from tcd.worktree import delete_branch, get_main_repo_root, merge_branch, remove_worktree

        job = self._mgr.load_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} not found")
        if not job.worktree_branch:
            raise TCDError(f"Job {job_id} has no worktree")

        repo_root = Path(job.worktree_repo_root) if job.worktree_repo_root else get_main_repo_root(job.cwd)
        success = merge_branch(repo_root, job.worktree_branch, strategy=strategy)

        if not success:
            emit(job.id, "job.worktree_merged", success=False, strategy=strategy)
            return False

        emit(job.id, "job.worktree_merged", success=True, strategy=strategy)

        if cleanup:
            if job.worktree_path:
                remove_worktree(job.worktree_path)
                emit(job.id, "job.worktree_removed", worktree_path=job.worktree_path)
            if strategy == "squash":
                delete_branch(repo_root, job.worktree_branch, force=True)
            else:
                delete_branch(repo_root, job.worktree_branch)
            job.worktree_path = None
            job.worktree_branch = None
            self._mgr.save_job(job)

        return True

    def kill(self, job_id: str) -> None:
        """Kill a running job.

        Raises:
            JobNotFoundError: If job doesn't exist.
        """
        job = self._mgr.load_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} not found")

        if self._tmux.session_exists(job.tmux_session):
            self._tmux.kill_session(job.tmux_session)
        job.status = "failed"
        job.error = "killed by user"
        job.completed_at = _now_iso()
        self._mgr.save_job(job)
        if job.worktree_path:
            try:
                from pathlib import Path

                from tcd.worktree import delete_branch, get_main_repo_root, remove_worktree

                repo_root = Path(job.worktree_repo_root) if job.worktree_repo_root else get_main_repo_root(job.cwd)

                remove_worktree(job.worktree_path)
                if job.worktree_branch:
                    delete_branch(repo_root, job.worktree_branch)
                emit(job.id, "job.worktree_removed", worktree_path=job.worktree_path)
                job.worktree_path = None
                job.worktree_branch = None
                self._mgr.save_job(job)
            except Exception:
                pass  # best-effort cleanup
        emit(job.id, "job.killed", reason="user")

    def clean(self, *, include_running: bool = False) -> int:
        """Clean completed/failed jobs.

        Returns:
            Number of jobs cleaned.
        """
        return self._mgr.clean_jobs(include_running=include_running)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_status(self, job: Job) -> None:
        """Refresh job status based on tmux session state."""
        if job.status != "running":
            return
        if not self._tmux.session_exists(job.tmux_session):
            if job.turn_state == "working":
                job.status = "failed"
                job.error = job.error or "tmux session disappeared while turn was working"
            else:
                job.status = "completed"
            job.completed_at = _now_iso()
            self._mgr.save_job(job)

    def _wait_for_tui(self, job: Job, prov) -> tuple[bool, int, bool]:
        """Wait for TUI to be ready, handling trust dialogs."""
        indicator = prov.tui_ready_indicator
        trust_handled = False
        tui_ready = False
        wait_started = time.time()

        for _ in range(60):
            time.sleep(0.5)
            pane = self._tmux.capture_pane(job.tmux_session)
            if pane is None:
                continue

            # Handle trust dialogs
            trust_phrases = [
                "Yes, I trust this folder",
                "Enter to confirm",
                "Do you trust the files in this folder",
            ]
            if any(phrase in pane for phrase in trust_phrases):
                self._tmux.send_enter(job.tmux_session)
                trust_handled = True
                time.sleep(2)
                continue

            if trust_handled and "restarting" in pane.lower():
                time.sleep(1)
                continue

            if indicator and indicator in pane:
                if trust_handled:
                    time.sleep(1)
                tui_ready = True
                break
        else:
            # Fallback
            time.sleep(2)
        elapsed_ms = int((time.time() - wait_started) * 1000)
        return tui_ready, elapsed_ms, trust_handled

    @staticmethod
    def _advance_turn_if_needed(job: Job) -> None:
        """Advance turn counter once when marker-based providers finish a working turn."""
        if job.provider in _MARKER_PROVIDERS and job.turn_state == "working":
            job.turn_count += 1

    @staticmethod
    def _accumulate_tokens(job: Job, tokens: dict[str, int] | None) -> None:
        """Add turn tokens to the job's cumulative total."""
        if tokens:
            job.total_tokens["input"] = job.total_tokens.get("input", 0) + tokens.get("input", 0)
            job.total_tokens["output"] = job.total_tokens.get("output", 0) + tokens.get("output", 0)
