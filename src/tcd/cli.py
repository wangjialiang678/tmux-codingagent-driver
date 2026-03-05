"""tcd CLI entry point."""

from __future__ import annotations

import json
import logging
import os
import sys
import time

import click

from tcd.collector import ResponseCollector
from tcd.config import ensure_dirs, job_signal_path
from tcd.diagnostics import diagnose
from tcd.event_log import emit, load_events
from tcd.job import Job, JobManager, _now_iso
from tcd.output_cleaner import clean_output
from tcd.provider import get_provider, list_providers
from tcd.tmux_adapter import TmuxAdapter, TmuxNotFoundError

logger = logging.getLogger(__name__)

_MARKER_PROVIDERS = {"claude", "gemini"}


def _get_tmux() -> TmuxAdapter:
    tmux = TmuxAdapter()
    try:
        tmux.check_tmux()
    except TmuxNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    return tmux


@click.group()
def cli():
    """tcd — tmux-codingagent-driver: Drive AI CLI tools via tmux."""
    ensure_dirs()


# ---------------------------------------------------------------------------
# tcd start
# ---------------------------------------------------------------------------

@cli.command()
@click.option("-p", "--provider", required=True, type=click.Choice(["codex", "claude", "gemini"]),
              help="AI CLI provider.")
@click.option("-m", "--prompt", required=True, help="Task prompt (use '-' for stdin).")
@click.option("-d", "--cwd", default=".", help="Working directory.")
@click.option("--model", default=None, help="Model name override.")
@click.option("--timeout", default=60, type=int, help="Timeout in minutes.")
@click.option("--sandbox", default=None, help="Codex sandbox mode.")
def start(provider: str, prompt: str, cwd: str, model: str | None, timeout: int, sandbox: str | None):
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

    # Check AI CLI is installed
    if hasattr(prov, "check_cli"):
        try:
            prov.check_cli()
        except FileNotFoundError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    # Create job
    mgr = JobManager()
    job = mgr.create_job(provider, prompt, cwd, model=model, timeout_minutes=timeout, sandbox=sandbox)
    emit(job.id, "job.created", provider=provider, sandbox=sandbox, cwd=cwd, model=model)

    # Build and launch
    launch_cmd = prov.build_launch_command(job)
    if not tmux.create_session(job.tmux_session, launch_cmd, cwd):
        click.echo(f"Error: failed to create tmux session.", err=True)
        job.status = "failed"
        job.error = "tmux session creation failed"
        mgr.save_job(job)
        sys.exit(1)

    # Update job status
    job.status = "running"
    job.started_at = _now_iso()
    job.turn_state = "working"
    mgr.save_job(job)

    # Wait for TUI init (poll for readiness indicator, up to 30s)
    # Handles trust dialogs from Claude Code and Gemini CLI (which may restart)
    indicator = prov.tui_ready_indicator
    tui_ready = False
    trust_handled = False
    tui_wait_started = time.time()
    for _ in range(60):
        time.sleep(0.5)
        pane = tmux.capture_pane(job.tmux_session)
        if pane is None:
            continue

        # Handle trust/confirmation dialogs (Claude Code, Gemini CLI)
        trust_phrases = [
            "Yes, I trust this folder",
            "Enter to confirm",
            "Do you trust the files in this folder",
        ]
        if any(phrase in pane for phrase in trust_phrases):
            tmux.send_enter(job.tmux_session)
            trust_handled = True
            time.sleep(2)
            continue

        # After trust handling, wait for restart to complete
        if trust_handled and "restarting" in pane.lower():
            time.sleep(1)
            continue

        if indicator and indicator in pane:
            if trust_handled:
                # Extra delay after restart to let TUI fully initialize
                time.sleep(1)
            tui_ready = True
            break
    if not tui_ready:
        # Fallback: just wait a bit and try anyway
        time.sleep(2)
    elapsed_ms = int((time.time() - tui_wait_started) * 1000)
    if tui_ready:
        emit(job.id, "job.tui_ready", elapsed_ms=elapsed_ms, trust_handled=trust_handled)
    else:
        emit(job.id, "job.tui_timeout", elapsed_ms=elapsed_ms, trust_handled=trust_handled)

    # Inject prompt
    req_id = f"{job.id}-0-{int(time.time())}"
    wrapped = prov.build_prompt_wrapper(prompt, req_id)
    if not tmux.send_text(job.tmux_session, wrapped):
        job.status = "failed"
        job.error = "failed to send initial prompt to tmux session"
        job.completed_at = _now_iso()
        mgr.save_job(job)
        click.echo("Error: failed to send initial prompt to tmux session.", err=True)
        sys.exit(1)
    emit(job.id, "job.prompt_sent", bytes=len(wrapped.encode("utf-8")), req_id=req_id)

    click.echo(f"Job started: {job.id}")
    click.echo(f"Provider: {provider}")
    click.echo(f"tmux session: {job.tmux_session}")


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


# ---------------------------------------------------------------------------
# tcd output
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option("--full", is_flag=True, help="Full scrollback output.")
@click.option("--raw", is_flag=True, help="Raw output (no ANSI cleaning).")
def output(job_id: str, full: bool, raw: bool):
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
        click.echo(result)
    else:
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
                emit(job.id, "job.checked", state="context_limit")
                emit(job.id, "job.turn_complete", turn=completed_turn, **({"tokens": result.tokens} if result.tokens else {}))
                state = "context_limit"
                exit_code = 2

        if state == "working":
            emit(job.id, "job.checked", state="working")

    if as_json:
        pane_tail = ""
        try:
            pane = TmuxAdapter().capture_pane(job.tmux_session)
            if pane:
                pane_tail = "\n".join(pane.splitlines()[-5:])
        except Exception:
            logger.exception("Failed to capture pane for diagnostics for job %s", job.id)

        diag_warnings = diagnose(job, pane_tail=pane_tail or None)
        click.echo(
            json.dumps(
                {
                    "state": state,
                    "elapsed_s": _elapsed(job),
                    "turn_count": job.turn_count,
                    "warnings": [
                        {"code": w.code, "severity": w.severity, "message": w.message}
                        for w in diag_warnings
                    ],
                    "pane_tail": pane_tail,
                },
                ensure_ascii=False,
            )
        )

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
    emit(
        job.id,
        "job.message_sent",
        bytes=len(wrapped.encode("utf-8")),
        req_id=req_id,
        turn=job.turn_count,
    )

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
def jobs(status_filter: str | None, as_json: bool):
    """List all jobs."""
    mgr = JobManager()
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
def kill(job_id: str | None, kill_all: bool):
    """Kill a running job."""
    tmux = _get_tmux()
    mgr = JobManager()

    if kill_all:
        for j in mgr.list_jobs(status_filter="running"):
            _kill_job(j, tmux, mgr)
            click.echo(f"Killed: {j.id}")
        return

    if not job_id:
        click.echo("Error: provide a job ID or --all.", err=True)
        sys.exit(1)

    job = mgr.load_job(job_id)
    if job is None:
        click.echo(f"Error: job {job_id!r} not found.", err=True)
        sys.exit(1)

    _kill_job(job, tmux, mgr)
    click.echo(f"Killed: {job.id}")


# ---------------------------------------------------------------------------
# tcd clean
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--all", "clean_all", is_flag=True, help="Clean all jobs (including running).")
def clean(clean_all: bool):
    """Clean completed/failed jobs."""
    mgr = JobManager()
    count = mgr.clean_jobs(include_running=clean_all)
    click.echo(f"Cleaned {count} job(s).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        else:
            job.status = "completed"
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


def _kill_job(job: Job, tmux: TmuxAdapter, mgr: JobManager) -> None:
    if tmux.session_exists(job.tmux_session):
        tmux.kill_session(job.tmux_session)
    job.status = "failed"
    job.error = "killed by user"
    job.completed_at = _now_iso()
    mgr.save_job(job)
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


def _elapsed(job: Job) -> int:
    """Seconds since job started."""
    from datetime import datetime, timezone
    start = job.started_at or job.created_at
    try:
        dt = datetime.fromisoformat(start)
        return int((datetime.now(timezone.utc) - dt).total_seconds())
    except (ValueError, TypeError):
        return 0


if __name__ == "__main__":
    cli()
