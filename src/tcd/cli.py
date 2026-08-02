"""tcd CLI entry point."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import click

from tcd import __version__
from tcd.collector import ResponseCollector
from tcd.config import ensure_dirs, job_signal_path
from tcd.diagnostics import Warning as DiagnosticWarning, diagnose
from tcd.doctor import run_doctor
from tcd.event_log import emit, load_events
from tcd.readiness import verify_prompt_delivery, wait_for_tui
from tcd.job import Job, JobManager, _now_iso
from tcd.output_cleaner import clean_output
from tcd.provider import get_provider, list_providers
from tcd.submission_recovery import retry_queued_message_submission
from tcd.tmux_adapter import TmuxAdapter, TmuxNotFoundError

logger = logging.getLogger(__name__)

_MARKER_PROVIDERS = {"claude", "gemini"}
_RUNNING_IDLE_NOTE = (
    "Note: this turn is complete, but the tmux session is still running for follow-ups. "
    "For marker providers, idle can come from TCD_DONE or idle fallback; use `tcd output --full` "
    "and `tcd log` if you need to verify the marker."
)


def _get_tmux() -> TmuxAdapter:
    tmux = TmuxAdapter()
    try:
        tmux.check_tmux()
    except TmuxNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    return tmux


@click.group()
@click.version_option(__version__, "-V", "--version", prog_name="tcd")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v=INFO, -vv=DEBUG).")
@click.pass_context
def cli(ctx: click.Context, verbose: int):
    """tcd — tmux-codingagent-driver: Drive AI CLI tools via tmux."""
    # Configure logging: default=WARNING, -v=INFO, -vv=DEBUG
    level = logging.WARNING
    if verbose >= 2:
        level = logging.DEBUG
    elif verbose >= 1:
        level = logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=level,
        stream=sys.stderr,
    )
    if ctx.invoked_subcommand != "doctor":
        ensure_dirs()


# ---------------------------------------------------------------------------
# tcd doctor
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--live", is_flag=True, help="Launch disposable provider sessions for live probes.")
@click.option(
    "--provider",
    type=click.Choice(list_providers()),
    default=None,
    help="Run the live probe for one provider only (requires --live).",
)
@click.option(
    "--timeout",
    type=click.IntRange(min=1),
    default=90,
    show_default=True,
    help="Maximum seconds for each live provider probe.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def doctor(live: bool, provider: str | None, timeout: int, as_json: bool):
    """Check whether tcd's provider detection assumptions still hold.

    Exit codes: 0=all checks pass, 1=one or more warnings, 2=one or more errors.
    """
    report = run_doctor(live=live, provider=provider, timeout=timeout)
    if as_json:
        click.echo(json.dumps(report.to_dict(), ensure_ascii=False))
    else:
        click.echo(report.format_text())
    sys.exit(report.exit_code)


# ---------------------------------------------------------------------------
# tcd start
# ---------------------------------------------------------------------------

@cli.command()
@click.option("-p", "--provider", required=True, type=click.Choice(list_providers()),
              help="AI CLI provider.")
@click.option("-m", "--prompt", required=True, help="Task prompt (use '-' for stdin).")
@click.option("-d", "--cwd", default=".", help="Working directory.")
@click.option("--model", default=None, help="Model name override.")
@click.option("--timeout", default=60, type=int, help="Timeout in minutes.")
@click.option("--sandbox", default=None, help="Sandbox mode (providers that support it; Codex only today).")
@click.option("--worktree", is_flag=True, default=False, help="Run in a git worktree for isolation.")
@click.option("--wt-name", default=None, help="Custom worktree branch name (default: job ID).")
def start(
    provider: str,
    prompt: str,
    cwd: str,
    model: str | None,
    timeout: int,
    sandbox: str | None,
    worktree: bool,
    wt_name: str | None,
):
    """Start a new AI job."""
    tmux = _get_tmux()

    # Read from stdin if prompt is '-'
    if prompt == "-":
        prompt = sys.stdin.read().strip()
        if not prompt:
            click.echo("Error: empty prompt from stdin.", err=True)
            sys.exit(1)

    # Resolve working directory
    cwd = os.path.abspath(cwd)

    # Get provider
    try:
        prov = get_provider(provider)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Reject options the provider cannot honour, rather than accepting them and
    # dropping them on the floor (`--sandbox` used to print a reassuring
    # "Sandbox: ..." line while only Codex actually applied it).
    if sandbox and not getattr(prov, "supports_sandbox", False):
        click.echo(
            f"Error: provider {provider!r} does not support --sandbox; "
            f"it would be ignored. Remove the flag or use a provider that supports it.",
            err=True,
        )
        sys.exit(1)

    # Check AI CLI is installed
    if hasattr(prov, "check_cli"):
        try:
            prov.check_cli()
        except FileNotFoundError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    stash_ref = None
    if worktree:
        from tcd.worktree import WorktreeError, auto_stash, is_git_repo

        if not is_git_repo(cwd):
            click.echo("Error: cwd is not a git repository.", err=True)
            sys.exit(1)

        try:
            stash_ref = auto_stash(cwd)
            if stash_ref:
                click.echo(f"Stashed uncommitted changes ({stash_ref[:8]}).")
                logger.info("start: auto-stashed dirty state, ref=%s", stash_ref)
        except WorktreeError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    # Create job
    mgr = JobManager()
    job = mgr.create_job(provider, prompt, cwd, model=model, timeout_minutes=timeout, sandbox=sandbox)
    logger.info("start %s: provider=%s cwd=%s sandbox=%s worktree=%s", job.id, provider, cwd, sandbox, worktree)

    if worktree:
        from tcd.worktree import create_worktree

        name = wt_name or job.id
        try:
            wt_path = create_worktree(cwd, name)
        except WorktreeError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        job.worktree_repo_root = cwd
        cwd = str(wt_path)
        job.cwd = cwd
        job.worktree_path = cwd
        job.worktree_branch = f"tcd/{name}"
        if stash_ref:
            job.worktree_stash_ref = stash_ref
        mgr.save_job(job)
        logger.info("start %s: worktree created at %s branch=tcd/%s", job.id, cwd, name)
        emit(job.id, "job.worktree_created", worktree_path=cwd, branch=f"tcd/{name}")

    try:
        emit(job.id, "job.created", provider=provider, sandbox=sandbox, cwd=cwd, model=model)

        # Build and launch
        launch_cmd = prov.build_launch_command(job)
        if not tmux.create_session(job.tmux_session, launch_cmd, cwd):
            job.status = "failed"
            job.error = "tmux session creation failed"
            mgr.save_job(job)
            raise RuntimeError("failed to create tmux session")

        # Update job status
        job.status = "running"
        job.started_at = _now_iso()
        job.turn_state = "working"
        mgr.save_job(job)

        # Wait for TUI readiness. Handles trust dialogs and, for providers that
        # need it (Codex), waits for the pane to settle so the prompt isn't
        # injected before the TUI can accept input. See tcd.readiness.
        tui_ready, elapsed_ms, trust_handled = wait_for_tui(tmux, job.tmux_session, prov)
        if tui_ready:
            logger.info("start %s: TUI ready in %dms (trust_handled=%s)", job.id, elapsed_ms, trust_handled)
            emit(job.id, "job.tui_ready", elapsed_ms=elapsed_ms, trust_handled=trust_handled)
        else:
            logger.warning("start %s: TUI not ready after %dms, proceeding anyway (trust_handled=%s)", job.id, elapsed_ms, trust_handled)
            emit(job.id, "job.tui_timeout", elapsed_ms=elapsed_ms, trust_handled=trust_handled)

        # Inject prompt
        req_id = f"{job.id}-0-{int(time.time())}"
        wrapped = prov.build_prompt_wrapper(prompt, req_id)
        if not tmux.send_text(job.tmux_session, wrapped):
            job.status = "failed"
            job.error = "failed to send initial prompt to tmux session"
            job.completed_at = _now_iso()
            mgr.save_job(job)
            raise RuntimeError("failed to send initial prompt to tmux session")
        logger.info("start %s: prompt sent (%d bytes, req_id=%s)", job.id, len(wrapped.encode("utf-8")), req_id)
        emit(job.id, "job.prompt_sent", bytes=len(wrapped.encode("utf-8")), req_id=req_id)

        # Verify the prompt actually landed and resend if it was dropped (e.g.
        # injected while a slow TUI was still initializing).
        if getattr(prov, "verify_prompt_delivery", False):
            verify_prompt_delivery(tmux, job.tmux_session, job.id, wrapped)

        click.echo(f"Job started: {job.id}")
        click.echo(f"Provider: {provider}")
        if job.sandbox:
            click.echo(f"Sandbox: {job.sandbox}")
        click.echo(f"tmux session: {job.tmux_session}")
    except Exception as exc:
        if job.status != "failed":
            job.status = "failed"
        if not job.error:
            job.error = str(exc)
        if not job.completed_at:
            job.completed_at = _now_iso()
        mgr.save_job(job)

        if job.worktree_path and job.worktree_branch:
            try:
                from pathlib import Path

                from tcd.worktree import delete_branch, get_main_repo_root, remove_worktree

                repo_root = Path(job.worktree_repo_root) if job.worktree_repo_root else get_main_repo_root(job.cwd)
                worktree_path = job.worktree_path
                cleaned = remove_worktree(worktree_path)
                if cleaned:
                    delete_branch(repo_root, job.worktree_branch)
                    emit(job.id, "job.worktree_removed", worktree_path=worktree_path)
                    job.worktree_path = None
                    job.worktree_branch = None
                    mgr.save_job(job)
                else:
                    logger.warning("start %s: worktree removal failed during rollback, skipping branch cleanup", job.id)
            except Exception:
                logger.warning("start %s: failed to rollback worktree setup", job.id, exc_info=True)

        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# tcd status
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def status(job_id: str, as_json: bool):
    """Show job status."""
    mgr = JobManager()
    job = mgr.load_job(job_id)
    if job is None:
        click.echo(f"Error: job {job_id!r} not found.", err=True)
        sys.exit(1)

    # Refresh status if running
    _refresh_status(job, mgr)

    if as_json:
        d = job.to_dict()
        d["elapsed_seconds"] = _elapsed(job)
        note = _running_idle_note(job)
        if note:
            d["state_note"] = note
        click.echo(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        click.echo(f"ID:       {job.id}")
        click.echo(f"Provider: {job.provider}")
        click.echo(f"Status:   {job.status}")
        click.echo(f"Turn:     {job.turn_count}")
        if job.turn_state:
            click.echo(f"State:    {job.turn_state}")
        if job.error:
            click.echo(f"Error:    {job.error}")
        if job.total_tokens.get("input", 0) or job.total_tokens.get("output", 0):
            click.echo(f"Tokens:   in={job.total_tokens['input']} out={job.total_tokens['output']}")
        click.echo(f"Elapsed:  {_elapsed(job)}s")
        note = _running_idle_note(job)
        if note:
            click.echo(note)


# ---------------------------------------------------------------------------
# tcd output
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option("--full", is_flag=True, help="Full scrollback output.")
@click.option("--raw", is_flag=True, help="Raw output (no ANSI cleaning).")
@click.option("--tail", type=int, default=None, help="Show only last N lines.")
@click.option("--since-line", type=int, default=None, help="Show lines after line N (for incremental polling).")
def output(job_id: str, full: bool, raw: bool, tail: int | None, since_line: int | None):
    """Get job output."""
    mgr = JobManager()
    job = mgr.load_job(job_id)
    if job is None:
        click.echo(f"Error: job {job_id!r} not found.", err=True)
        sys.exit(1)

    collector = ResponseCollector()
    if raw:
        result = collector.collect_raw(job)
    elif full:
        result = collector.collect_full(job)
    else:
        result = collector.collect(job)

    if result:
        lines = result.splitlines()
        total = len(lines)
        if since_line is not None:
            logger.debug("output %s: total=%d since_line=%d showing=%d", job_id, total, since_line, len(lines[since_line:]))
            lines = lines[since_line:]
        elif tail is not None:
            logger.debug("output %s: total=%d tail=%d", job_id, total, tail)
            lines = lines[-tail:]
        click.echo("\n".join(lines))
        # Print total line count to stderr for callers to track position
        if since_line is not None or tail is not None:
            click.echo(f"__lines_total={total}", err=True)
        note = _running_idle_note(job)
        if note:
            click.echo(note, err=True)
    else:
        logger.debug("output %s: no output available", job_id)
        click.echo("(no output available)", err=True)


# ---------------------------------------------------------------------------
# tcd log
# ---------------------------------------------------------------------------

@cli.command("log")
@click.argument("job_id")
@click.option("--tail", type=click.IntRange(min=1), default=None, help="Show last N events.")
@click.option("--event", "event_filter", default=None, help="Filter by event type.")
def log_events(job_id: str, tail: int | None, event_filter: str | None):
    """Show job event log."""
    events = load_events(job_id, event_filter=event_filter)
    if tail is not None:
        events = events[-tail:]

    if not events:
        click.echo("No events found.")
        return

    for entry in events:
        click.echo(_format_event_line(entry))


# ---------------------------------------------------------------------------
# tcd check
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option("--json", "as_json", is_flag=True, help="Output state and diagnostics as JSON.")
def check(job_id: str, as_json: bool):
    """Non-blocking completion check.

    Exit codes: 0=idle, 1=working, 2=context_limit, 3=not_found
    """
    mgr = JobManager()
    job = mgr.load_job(job_id)
    if job is None:
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "state": "not_found",
                        "elapsed_s": 0,
                        "turn_count": 0,
                        "warnings": [],
                        "pane_tail": "",
                    },
                    ensure_ascii=False,
                )
            )
        sys.exit(3)

    _refresh_status(job, mgr)

    state = "working"
    exit_code = 1

    if job.status in ("completed", "failed"):
        state = job.status
        exit_code = 0
    else:
        # Check provider completion detection
        try:
            prov = get_provider(job.provider)
            result = prov.detect_completion(job)
        except (json.JSONDecodeError, OSError, ValueError, KeyError):
            logger.exception("Provider completion check failed for job %s", job.id)
            result = None

        if result is not None:
            if result.state == "idle":
                completed_turn = job.turn_count
                _advance_turn_if_needed(job)
                job.turn_state = "idle"
                if result.last_agent_message:
                    job.last_agent_message = result.last_agent_message
                _accumulate_tokens(job, result.tokens)
                mgr.save_job(job)
                logger.info("check %s: state=idle turn=%d elapsed=%ds", job.id, completed_turn, _elapsed(job))
                emit(job.id, "job.checked", state="idle")
                emit(job.id, "job.turn_complete", turn=completed_turn, **({"tokens": result.tokens} if result.tokens else {}))
                state = "idle"
                exit_code = 0
            elif result.state == "context_limit":
                completed_turn = job.turn_count
                _advance_turn_if_needed(job)
                job.turn_state = "context_limit"
                _accumulate_tokens(job, result.tokens)
                mgr.save_job(job)
                logger.warning("check %s: context_limit reached at turn=%d elapsed=%ds", job.id, completed_turn, _elapsed(job))
                emit(job.id, "job.checked", state="context_limit")
                emit(job.id, "job.turn_complete", turn=completed_turn, **({"tokens": result.tokens} if result.tokens else {}))
                state = "context_limit"
                exit_code = 2

        if state == "working":
            # Capture a pane hash so STALL detection can distinguish
            # "AI actively generating output" from "AI truly stuck".
            _pane_hash = None
            try:
                _tmux_for_hash = TmuxAdapter()
                _pane_for_hash = _tmux_for_hash.capture_pane(job.tmux_session)
                if _pane_for_hash:
                    import hashlib
                    _pane_hash = hashlib.md5(_pane_for_hash.encode()).hexdigest()[:8]
            except Exception:
                pass
            emit(job.id, "job.checked", state="working", **({"pane_hash": _pane_hash} if _pane_hash else {}))

    if as_json:
        pane_tail = ""
        activity_lines: list[str] = []
        scrollback = None
        try:
            tmux = TmuxAdapter()
            pane = tmux.capture_pane(job.tmux_session)
            if pane:
                pane_tail = "\n".join(pane.splitlines()[-5:])
            # Grab more scrollback to extract meaningful activity
            scrollback = tmux.capture_pane(job.tmux_session, start_line="-200")
            if scrollback:
                activity_lines = _extract_activity_lines(scrollback, provider=job.provider)
                logger.debug("check %s: extracted %d activity lines from scrollback", job.id, len(activity_lines))
        except Exception:
            logger.exception("Failed to capture pane for diagnostics for job %s", job.id)

        diag_warnings = diagnose(job, pane_tail=pane_tail or None)
        provider_error = _detect_provider_error(job, scrollback or pane_tail)
        if provider_error:
            diag_warnings.append(
                DiagnosticWarning(
                    code="PROVIDER_ERROR",
                    message=f"{provider_error} — the agent cannot make progress; kill and restart",
                    severity="error",
                )
            )
        payload = {
            "state": state,
            "elapsed_s": _elapsed(job),
            "turn_count": job.turn_count,
            "warnings": [
                {"code": w.code, "severity": w.severity, "message": w.message}
                for w in diag_warnings
            ],
            "pane_tail": pane_tail,
            "activity": activity_lines,
        }
        note = _running_idle_note(job)
        if note:
            payload["state_note"] = note
        click.echo(json.dumps(payload, ensure_ascii=False))

    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# tcd wait
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option("--timeout", default=300, type=int, help="Max wait time in seconds.")
def wait(job_id: str, timeout: int):
    """Block until job completes.

    Exit codes: 0=completed, 1=failed, 2=timeout
    """
    mgr = JobManager()
    job = mgr.load_job(job_id)
    if job is None:
        click.echo(f"Error: job {job_id!r} not found.", err=True)
        sys.exit(1)

    deadline = time.time() + timeout
    poll_interval = 2

    while time.time() < deadline:
        job = mgr.load_job(job_id)
        if job is None:
            sys.exit(1)

        _refresh_status(job, mgr)

        if job.status == "completed":
            sys.exit(0)
        if job.status == "failed":
            sys.exit(1)

        # Check provider completion
        try:
            prov = get_provider(job.provider)
            result = prov.detect_completion(job)
            if result and result.state == "idle":
                completed_turn = job.turn_count
                _advance_turn_if_needed(job)
                job.turn_state = "idle"
                if result.last_agent_message:
                    job.last_agent_message = result.last_agent_message
                _accumulate_tokens(job, result.tokens)
                mgr.save_job(job)
                emit(job.id, "job.turn_complete", turn=completed_turn, **({"tokens": result.tokens} if result.tokens else {}))
                note = _running_idle_note(job)
                if note:
                    click.echo(note, err=True)
                sys.exit(0)
            if result and result.state == "context_limit" and job.turn_state == "working":
                completed_turn = job.turn_count
                _advance_turn_if_needed(job)
                job.turn_state = "context_limit"
                _accumulate_tokens(job, result.tokens)
                mgr.save_job(job)
                emit(job.id, "job.turn_complete", turn=completed_turn, **({"tokens": result.tokens} if result.tokens else {}))
        except (json.JSONDecodeError, OSError, ValueError, KeyError):
            logger.exception("Provider completion wait check failed for job %s", job.id)

        time.sleep(poll_interval)

    sys.exit(2)  # timeout


# ---------------------------------------------------------------------------
# tcd send
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.argument("message", required=False)
@click.option("--file", "file_path", default=None, help="Read message from file.")
def send(job_id: str, message: str | None, file_path: str | None):
    """Send a follow-up message to a running job."""
    tmux = _get_tmux()
    mgr = JobManager()
    job = mgr.load_job(job_id)
    if job is None:
        click.echo(f"Error: job {job_id!r} not found.", err=True)
        sys.exit(1)

    if job.status != "running":
        click.echo(f"Error: job {job_id} is not running (status={job.status}).", err=True)
        sys.exit(1)

    # Resolve message
    if file_path and message:
        click.echo("Error: provide either --file or a message argument, not both.", err=True)
        sys.exit(1)
    if file_path:
        try:
            with open(file_path) as f:
                message = f.read().strip()
        except OSError as e:
            click.echo(f"Error: failed to read file: {e}", err=True)
            sys.exit(1)
    if not message:
        click.echo("Error: no message provided.", err=True)
        sys.exit(1)

    # Clear signal file for new turn
    signal = job_signal_path(job.id)
    signal.unlink(missing_ok=True)

    # Wrap and send
    prov = get_provider(job.provider)
    req_id = f"{job.id}-{job.turn_count}-{int(time.time())}"
    wrapped = prov.build_prompt_wrapper(message, req_id)
    if not tmux.send_text(job.tmux_session, wrapped):
        job.status = "failed"
        job.error = "failed to send message to tmux session"
        job.completed_at = _now_iso()
        mgr.save_job(job)
        click.echo("Error: failed to send message to tmux session.", err=True)
        sys.exit(1)
    logger.info("send %s: message sent (%d bytes, turn=%d, req_id=%s)", job.id, len(wrapped.encode("utf-8")), job.turn_count, req_id)
    emit(
        job.id,
        "job.message_sent",
        bytes=len(wrapped.encode("utf-8")),
        req_id=req_id,
        turn=job.turn_count,
    )
    retry_queued_message_submission(tmux, job, prov, req_id)

    # Update job
    job.turn_state = "working"
    mgr.save_job(job)

    click.echo(f"Message sent to job {job_id}.")


# ---------------------------------------------------------------------------
# tcd jobs
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--status", "status_filter", default=None, help="Filter by status.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--no-reconcile", is_flag=True, default=False, help="Skip the tmux liveness check.")
def jobs(status_filter: str | None, as_json: bool, no_reconcile: bool):
    """List all jobs.

    Job records are a mirror of tmux state written at job time, so the list is
    reconciled against live sessions first — otherwise jobs whose session died
    without a `tcd kill` stay 'running' forever.
    """
    mgr = JobManager()
    if not no_reconcile:
        _reconcile_jobs(mgr)
    all_jobs = mgr.list_jobs(status_filter=status_filter)

    if as_json:
        data = [j.to_dict() for j in all_jobs]
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        if not all_jobs:
            click.echo("No jobs found.")
            return
        # Table header
        click.echo(f"{'ID':<12} {'PROVIDER':<10} {'STATUS':<12} {'TURN':<6} {'ELAPSED'}")
        click.echo("-" * 55)
        for j in all_jobs:
            elapsed = _elapsed(j)
            click.echo(f"{j.id:<12} {j.provider:<10} {j.status:<12} {j.turn_count:<6} {elapsed}s")


# ---------------------------------------------------------------------------
# tcd attach
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
def attach(job_id: str):
    """Attach to a job's tmux session (for debugging)."""
    mgr = JobManager()
    job = mgr.load_job(job_id)
    if job is None:
        click.echo(f"Error: job {job_id!r} not found.", err=True)
        sys.exit(1)

    tmux = _get_tmux()
    if not tmux.session_exists(job.tmux_session):
        click.echo(f"Error: tmux session {job.tmux_session} no longer exists.", err=True)
        sys.exit(1)

    os.execvp("tmux", ["tmux", "attach-session", "-t", job.tmux_session])


# ---------------------------------------------------------------------------
# tcd kill
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id", required=False)
@click.option("--all", "kill_all", is_flag=True, help="Kill all running jobs.")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Also discard a worktree that still holds unmerged commits or uncommitted changes.",
)
def kill(job_id: str | None, kill_all: bool, force: bool):
    """Kill a running job.

    The agent's worktree is kept when it still holds work that is not on the
    main branch; pass --force to discard it.
    """
    tmux = _get_tmux()
    mgr = JobManager()

    if kill_all:
        for j in mgr.list_jobs(status_filter="running"):
            _kill_job(j, tmux, mgr, force=force)
            click.echo(f"Killed: {j.id}")
        return

    if not job_id:
        click.echo("Error: provide a job ID or --all.", err=True)
        sys.exit(1)

    job = mgr.load_job(job_id)
    if job is None:
        click.echo(f"Error: job {job_id!r} not found.", err=True)
        sys.exit(1)

    _kill_job(job, tmux, mgr, force=force)
    click.echo(f"Killed: {job.id}")


# ---------------------------------------------------------------------------
# tcd merge
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option("--squash", is_flag=True, default=False, help="Squash merge.")
@click.option("--no-cleanup", is_flag=True, default=False, help="Don't remove worktree after merge.")
def merge(job_id: str, squash: bool, no_cleanup: bool):
    """Merge a worktree job's branch back to main."""
    mgr = JobManager()
    job = mgr.load_job(job_id)
    if job is None:
        click.echo(f"Error: Job {job_id!r} not found.", err=True)
        sys.exit(1)
    if not job.worktree_branch:
        click.echo(f"Error: Job {job_id} has no worktree.", err=True)
        sys.exit(1)

    from pathlib import Path

    from tcd.worktree import branch_has_new_commits, delete_branch, get_main_repo_root, is_git_repo, merge_branch, remove_worktree

    # Determine repo_root with defensive fallback chain
    repo_root = None
    if job.worktree_repo_root:
        candidate = Path(job.worktree_repo_root)
        if candidate.exists() and is_git_repo(candidate):
            repo_root = candidate
        else:
            logger.warning(
                "merge %s: invalid persisted worktree_repo_root=%s, falling back to path derivation",
                job.id,
                candidate,
            )

    if repo_root is None:
        # Fallback: try to derive from worktree path or cwd
        if not job.worktree_repo_root:
            logger.warning("merge %s: worktree_repo_root not set, falling back to path derivation", job.id)
        for candidate in [job.worktree_path, job.cwd]:
            if candidate and Path(candidate).exists():
                try:
                    repo_root = get_main_repo_root(candidate)
                    break
                except Exception:
                    continue

    if repo_root is None:
        click.echo("Error: cannot determine repo root (worktree may be deleted).", err=True)
        click.echo(f"Run manually: git merge --no-ff {job.worktree_branch}", err=True)
        sys.exit(1)

    strategy = "squash" if squash else "merge"

    # Pre-check: warn early if branch has no new commits (common when AI forgets to commit)
    if not branch_has_new_commits(repo_root, job.worktree_branch):
        click.echo(f"Warning: '{job.worktree_branch}' has no new commits relative to HEAD.", err=True)
        click.echo("The AI agent likely forgot to commit its changes. Check the worktree:", err=True)
        if job.worktree_path:
            click.echo(f"  cd {job.worktree_path} && git status", err=True)
        click.echo(f"  git log {job.worktree_branch} --oneline -5", err=True)
        emit(job.id, "job.worktree_merged", success=False, reason="no_new_commits")
        sys.exit(1)

    logger.info("merge %s: merging branch=%s strategy=%s repo_root=%s", job.id, job.worktree_branch, strategy, repo_root)
    merge_result = merge_branch(repo_root, job.worktree_branch, strategy=strategy)

    if not merge_result.success:
        logger.warning("merge %s: conflict on branch=%s", job.id, job.worktree_branch)
        click.echo(f"Merge conflict on {job.worktree_branch}. Resolve manually.", err=True)
        emit(job.id, "job.worktree_merged", success=False, strategy=strategy)
        sys.exit(1)

    if merge_result.noop:
        click.echo(f"Warning: '{job.worktree_branch}' is already up to date — no changes merged.", err=True)
        click.echo(f"Verify branch has commits: git log {job.worktree_branch} --oneline -5", err=True)
        emit(job.id, "job.worktree_merged", success=True, strategy=strategy, noop=True)
        sys.exit(1)

    emit(job.id, "job.worktree_merged", success=True, strategy=strategy)
    logger.info("merge %s: success, cleanup=%s", job.id, not no_cleanup)
    click.echo(f"Merged {job.worktree_branch} ({strategy}).")
    if merge_result.stdout:
        click.echo(merge_result.stdout)

    # Mark job as completed after successful merge
    if job.status == "running":
        job.status = "completed"
        job.completed_at = _now_iso()
        mgr.save_job(job)

    if not no_cleanup and job.worktree_path:
        try:
            cleaned = remove_worktree(job.worktree_path)
            if cleaned:
                if strategy == "squash":
                    delete_branch(repo_root, job.worktree_branch, force=True)
                else:
                    delete_branch(repo_root, job.worktree_branch)
                emit(job.id, "job.worktree_removed", worktree_path=job.worktree_path)
                job.worktree_path = None
                job.worktree_branch = None
                mgr.save_job(job)
                click.echo("Worktree cleaned up.")
            else:
                logger.warning("merge %s: worktree removal failed, skipping branch cleanup", job.id)
                click.echo("Warning: worktree removal failed, skipping branch cleanup.", err=True)
        except Exception as exc:
            logger.warning("merge %s: cleanup failed", job.id, exc_info=True)
            click.echo(f"Warning: merge succeeded but cleanup failed: {exc}", err=True)

    # Restore stashed changes if any were auto-stashed before worktree creation
    _restore_auto_stash(job, repo_root, mgr)


# ---------------------------------------------------------------------------
# tcd clean
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--all", "clean_all", is_flag=True, help="Clean all jobs (including running).")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Clean even jobs that still own a worktree or an unrestored stash.",
)
def clean(clean_all: bool, force: bool):
    """Clean completed/failed jobs.

    The job record is the only place a worktree path and auto-stash ref are
    written down, so jobs still holding either are skipped unless --force.
    """
    mgr = JobManager()

    skipped: list[tuple[str, str]] = []
    if not force:
        for job in mgr.list_jobs():
            reason = None
            if job.worktree_path and Path(job.worktree_path).exists():
                reason = f"worktree still at {job.worktree_path}"
            elif job.worktree_stash_ref and not job.worktree_stash_restored:
                reason = f"auto-stash {job.worktree_stash_ref[:8]} not restored"
            if reason:
                skipped.append((job.id, reason))

    count = mgr.clean_jobs(include_running=clean_all, skip_ids={j for j, _ in skipped})
    click.echo(f"Cleaned {count} job(s).")
    if skipped:
        click.echo(f"Kept {len(skipped)} job(s) that still own resources:", err=True)
        for job_id, reason in skipped:
            click.echo(f"  {job_id}: {reason}", err=True)
        click.echo("Resolve with `tcd merge <id>` / `tcd kill <id>`, or re-run with --force.", err=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reconcile_jobs(mgr: JobManager) -> int:
    """Close out jobs whose tmux session is gone. Returns how many changed."""
    try:
        live = TmuxAdapter().list_sessions()
    except Exception:
        logger.warning("reconcile: could not list tmux sessions", exc_info=True)
        return 0

    changed = 0
    for job in mgr.list_jobs():
        if job.status not in ("running", "pending"):
            continue
        if job.tmux_session in live:
            continue
        if job.turn_state == "working":
            job.status = "failed"
            job.error = job.error or "tmux session disappeared while turn was working"
        else:
            job.status = "completed"
        job.completed_at = job.completed_at or _now_iso()
        mgr.save_job(job)
        emit(job.id, "job.reconciled", status=job.status)
        logger.info("reconcile %s: session %s gone, marked %s", job.id, job.tmux_session, job.status)
        changed += 1
    return changed


def _refresh_status(job: Job, mgr: JobManager) -> None:
    """Refresh job status based on tmux session state."""
    if job.status != "running":
        return

    tmux = TmuxAdapter()
    if not tmux.session_exists(job.tmux_session):
        # Session disappeared during an active turn is treated as failure.
        if job.turn_state == "working":
            job.status = "failed"
            job.error = job.error or "tmux session disappeared while turn was working"
            logger.warning("refresh %s: tmux session gone while working, marking failed", job.id)
        else:
            job.status = "completed"
            logger.info("refresh %s: tmux session gone (idle), marking completed", job.id)
        job.completed_at = _now_iso()
        mgr.save_job(job)


def _advance_turn_if_needed(job: Job) -> None:
    """Advance turn counter once when marker-based providers finish a working turn."""
    if job.provider in _MARKER_PROVIDERS and job.turn_state == "working":
        job.turn_count += 1


def _accumulate_tokens(job: Job, tokens: dict[str, int] | None) -> None:
    """Add turn tokens to the job's cumulative total."""
    if tokens:
        job.total_tokens["input"] = job.total_tokens.get("input", 0) + tokens.get("input", 0)
        job.total_tokens["output"] = job.total_tokens.get("output", 0) + tokens.get("output", 0)


def _running_idle_note(job: Job) -> str | None:
    """Explain the marker-provider state where a turn is done but session remains alive."""
    if job.provider in _MARKER_PROVIDERS and job.status == "running" and job.turn_state == "idle":
        return _RUNNING_IDLE_NOTE
    return None


def _restore_auto_stash(job: Job, repo_root, mgr: JobManager) -> None:
    """Give the caller back the changes `tcd start --worktree` stashed away.

    Runs on every terminal path, not just `tcd merge` — the stash belongs to the
    user's working tree, so killing the agent must not strand it.
    """
    if not job.worktree_stash_ref or job.worktree_stash_restored:
        return
    from tcd.worktree import stash_pop

    if stash_pop(repo_root, job.worktree_stash_ref):
        job.worktree_stash_restored = True
        mgr.save_job(job)
        click.echo(f"Restored your stashed changes ({job.worktree_stash_ref[:8]}).")
        emit(job.id, "job.stash_restored", ref=job.worktree_stash_ref)
    else:
        click.echo(
            f"Warning: could not restore your auto-stash {job.worktree_stash_ref[:8]}; "
            f"it is still in `git stash list` of {repo_root}.",
            err=True,
        )
        emit(job.id, "job.stash_restore_failed", ref=job.worktree_stash_ref)


def _worktree_has_unsaved_work(job: Job, repo_root) -> str | None:
    """Return a reason string when discarding the worktree would lose work."""
    from tcd.worktree import branch_has_new_commits, worktree_is_dirty

    if job.worktree_path and worktree_is_dirty(job.worktree_path):
        return "uncommitted changes in the worktree"
    if job.worktree_branch:
        try:
            if branch_has_new_commits(repo_root, job.worktree_branch):
                return f"commits on {job.worktree_branch} that are not in HEAD"
        except Exception:
            logger.warning("kill %s: could not compare branch %s", job.id, job.worktree_branch, exc_info=True)
            return f"unknown state of branch {job.worktree_branch}"
    return None


def _kill_job(job: Job, tmux: TmuxAdapter, mgr: JobManager, *, force: bool = False) -> None:
    logger.info("kill %s: killing job (provider=%s, elapsed=%ds)", job.id, job.provider, _elapsed(job))
    if tmux.session_exists(job.tmux_session):
        tmux.kill_session(job.tmux_session)
    job.status = "failed"
    job.error = "killed by user"
    job.completed_at = _now_iso()
    mgr.save_job(job)
    if job.worktree_path:
        try:
            from pathlib import Path

            from tcd.worktree import delete_branch, get_main_repo_root, remove_worktree

            repo_root = Path(job.worktree_repo_root) if job.worktree_repo_root else get_main_repo_root(job.cwd)

            # `git worktree remove --force` throws away whatever the agent had
            # not committed, and agents forgetting to commit is a known failure
            # mode — so check before destroying anything.
            blocker = None if force else _worktree_has_unsaved_work(job, repo_root)
            if blocker:
                click.echo(f"Kept worktree: {blocker}.", err=True)
                click.echo(f"  worktree: {job.worktree_path}", err=True)
                if job.worktree_branch:
                    click.echo(f"  branch:   {job.worktree_branch}", err=True)
                click.echo(
                    f"  merge it with `tcd merge {job.id}`, or discard with `tcd kill {job.id} --force`.",
                    err=True,
                )
                emit(job.id, "job.worktree_kept", reason=blocker, worktree_path=job.worktree_path)
            else:
                cleaned = remove_worktree(job.worktree_path)
                if cleaned:
                    if job.worktree_branch:
                        delete_branch(repo_root, job.worktree_branch, force=force)
                    logger.info("kill %s: worktree removed at %s", job.id, job.worktree_path)
                    emit(job.id, "job.worktree_removed", worktree_path=job.worktree_path)
                    job.worktree_path = None
                    job.worktree_branch = None
                    mgr.save_job(job)
                else:
                    logger.warning("kill %s: worktree removal failed, skipping branch cleanup for %s", job.id, job.worktree_path)

            _restore_auto_stash(job, repo_root, mgr)
        except Exception:
            logger.warning("kill %s: failed to clean up worktree at %s", job.id, job.worktree_path, exc_info=True)
    emit(job.id, "job.killed", reason="user")


def _format_event_line(entry: dict) -> str:
    ts = str(entry.get("ts", "-"))
    event = str(entry.get("event", "unknown"))
    parts = [
        f"{key}={json.dumps(value, ensure_ascii=False)}"
        for key, value in entry.items()
        if key not in {"ts", "event"}
    ]
    if parts:
        return f"{ts} {event} " + " ".join(parts)
    return f"{ts} {event}"


# Patterns that indicate meaningful agent activity (not TUI chrome).
# Kept provider-neutral: every coding CLI reports file operations and test
# results in some form, so tuning this list to one vendor quietly degrades the
# `activity` field for the others.
_ACTIVITY_PATTERNS = re.compile(
    r"^[•\-\*]\s|"                     # bullet points (action summaries)
    r"^\s*(Edited|Created|Read|Ran |Deleted|Moved|Searched|Explored|Wrote|Updated)\b|"
    r"^\s*[✓✗✔✘⚠]|"                  # status indicators
    r"passed|failed|error|PASS|FAIL|"  # test results
    r"^\d+\s+(passed|failed)|"         # pytest summary
    r"Worked for "                      # Codex timing
)

# Line prefixes that are TUI furniture rather than work, per provider. The
# status bar shows the model name, which differs by vendor.
_CHROME_PREFIXES: dict[str, tuple[str, ...]] = {
    "codex": ("›", "gpt-", "─"),
    "claude": ("❯", ">", "claude-", "─"),
    "gemini": ("›", ">", "gemini-", "─"),
}
_DEFAULT_CHROME_PREFIXES = ("›", "❯", ">", "─")


def _extract_activity_lines(scrollback: str, max_lines: int = 15, *, provider: str | None = None) -> list[str]:
    """Extract meaningful activity lines from an agent's scrollback.

    Filters out TUI chrome, empty lines, and status bar to surface
    actual work: file operations, test results, and action summaries.
    """
    chrome = _CHROME_PREFIXES.get(provider or "", _DEFAULT_CHROME_PREFIXES)
    lines = scrollback.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip TUI chrome
        if stripped.startswith(chrome):
            continue
        if _ACTIVITY_PATTERNS.search(stripped):
            result.append(stripped)
    matched = result[-max_lines:]
    logger.debug("_extract_activity_lines: %d input lines, %d matched, returning %d", len(lines), len(result), len(matched))
    return matched


def _detect_provider_error(job: Job, pane: str | None) -> str | None:
    """Ask the provider whether the pane shows a fatal API-side failure."""
    if not pane:
        return None
    try:
        return get_provider(job.provider).detect_provider_error(pane)
    except Exception:
        logger.debug("provider error detection failed for job %s", job.id, exc_info=True)
        return None


def _elapsed(job: Job) -> int:
    """How long the job ran.

    Once a job reaches a terminal state the clock stops at ``completed_at``;
    measuring against "now" made finished jobs report weeks of runtime.
    """
    from datetime import datetime, timezone
    start = job.started_at or job.created_at
    try:
        dt = datetime.fromisoformat(start)
    except (ValueError, TypeError):
        return 0

    end = datetime.now(timezone.utc)
    if job.completed_at:
        try:
            end = datetime.fromisoformat(job.completed_at)
        except (ValueError, TypeError):
            pass
    return max(0, int((end - dt).total_seconds()))


if __name__ == "__main__":
    cli()
