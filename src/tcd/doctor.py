"""Health checks for tcd's external CLI detection assumptions."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from tcd.config import JOBS_DIR, TCD_HOME
from tcd.job import Job
from tcd.provider import get_provider, list_providers
from tcd.readiness import verify_prompt_delivery, wait_for_tui
from tcd.tmux_adapter import TmuxAdapter, TmuxNotFoundError

logger = logging.getLogger(__name__)

VERSION_TIMEOUT_SECONDS = 3
DEFAULT_LIVE_TIMEOUT_SECONDS = 90
PROBE_PROMPT = "Reply with exactly: TCD_DOCTOR_OK"

Status = Literal["pass", "warning", "error", "skipped"]


@dataclass
class DoctorResult:
    """One doctor check with human and machine-readable details."""

    code: str
    status: Status
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to a JSON-safe dictionary."""
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class DoctorReport:
    """Aggregate results and derive the command's documented exit code."""

    results: list[DoctorResult] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """Return 0 for pass, 1 for warnings, and 2 for errors."""
        if any(result.status == "error" for result in self.results):
            return 2
        if any(result.status == "warning" for result in self.results):
            return 1
        return 0

    @property
    def status(self) -> str:
        """Return the highest-severity status in the report."""
        return {0: "pass", 1: "warning", 2: "error"}[self.exit_code]

    def add(
        self,
        code: str,
        status: Status,
        message: str,
        **details: Any,
    ) -> None:
        """Append one check result."""
        self.results.append(DoctorResult(code, status, message, details))

    def to_dict(self) -> dict[str, Any]:
        """Convert the report to a machine-readable shape."""
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "results": [result.to_dict() for result in self.results],
        }

    def format_text(self) -> str:
        """Format the report for interactive terminal use."""
        lines: list[str] = []
        for result in self.results:
            lines.append(f"[{result.status.upper()}] {result.code}: {result.message}")
            if result.details:
                lines.append(f"  {json.dumps(result.details, ensure_ascii=False, sort_keys=True)}")
        return "\n".join(lines)


def run_doctor(
    *,
    live: bool = False,
    provider: str | None = None,
    timeout: int = DEFAULT_LIVE_TIMEOUT_SECONDS,
) -> DoctorReport:
    """Run static checks and, when requested, isolated live provider probes."""
    logger.info("doctor: start live=%s provider=%s timeout=%ss", live, provider, timeout)
    report = DoctorReport()
    tmux, tmux_available = _check_tmux(report)
    provider_states = _check_providers(report)
    _check_tcd_home(report)
    _check_jobs(report, tmux if tmux_available else None)

    if not live:
        logger.info("doctor: static checks complete exit=%d", report.exit_code)
        return report

    selected = [provider] if provider else list_providers()
    for provider_name in selected:
        prov = provider_states.get(provider_name)
        if prov is None:
            report.add(
                "LIVE_CHECK_SKIPPED",
                "error",
                f"Provider {provider_name!r} is not registered.",
                provider=provider_name,
            )
            continue
        if not tmux_available:
            report.add(
                "LIVE_CHECK_SKIPPED",
                "skipped",
                f"Skipped live check for {provider_name}: tmux is unavailable.",
                provider=provider_name,
            )
            continue
        if not _provider_cli_available(report, provider_name):
            report.add(
                "LIVE_CHECK_SKIPPED",
                "skipped",
                f"Skipped live check for {provider_name}: its CLI is unavailable.",
                provider=provider_name,
            )
            continue
        _run_live_check(report, tmux, prov, timeout)

    logger.info("doctor: checks complete exit=%d", report.exit_code)
    return report


def _check_tmux(report: DoctorReport) -> tuple[TmuxAdapter, bool]:
    """Check tmux availability before trying to reconcile persistent jobs."""
    tmux = TmuxAdapter()
    try:
        tmux.check_tmux()
        path = tmux.tmux
    except TmuxNotFoundError as exc:
        report.add("TMUX", "error", str(exc))
        return tmux, False
    except Exception as exc:
        logger.exception("doctor: tmux check failed")
        report.add("TMUX", "error", f"Unable to check tmux: {exc}")
        return tmux, False

    version, version_error = _get_version(path)
    if version_error:
        report.add(
            "TMUX",
            "warning",
            f"tmux is available at {path}, but its version could not be determined: {version_error}",
            path=path,
            version=None,
        )
    else:
        report.add("TMUX", "pass", f"tmux is available at {path} ({version}).", path=path, version=version)
    return tmux, True


def _check_providers(report: DoctorReport) -> dict[str, Any]:
    """Check every registered provider without embedding a provider list here."""
    providers: dict[str, Any] = {}
    for name in list_providers():
        try:
            prov = get_provider(name)
        except Exception as exc:
            logger.exception("doctor: unable to initialize provider %s", name)
            report.add("CLI", "error", f"Provider {name!r} could not be initialized: {exc}", provider=name)
            continue

        providers[name] = prov
        _check_provider_cli(report, prov)
        report.add(
            "DETECTION_ASSUMPTIONS",
            "pass",
            f"{name} detection assumptions are recorded below.",
            provider=name,
            tui_ready_indicator=getattr(prov, "tui_ready_indicator", None),
            tui_stable_secs=getattr(prov, "tui_stable_secs", 0.0),
            verify_prompt_delivery=getattr(prov, "verify_prompt_delivery", False),
            supports_sandbox=getattr(prov, "supports_sandbox", False),
        )
    return providers


def _check_provider_cli(report: DoctorReport, prov: Any) -> None:
    """Run each provider's native availability check before requesting version."""
    name = getattr(prov, "name", "unknown")
    command = getattr(prov, "cli_command", name)
    try:
        prov.check_cli()
    except FileNotFoundError as exc:
        report.add("CLI", "error", str(exc), provider=name, command=command, version=None)
        return
    except Exception as exc:
        logger.exception("doctor: CLI check failed for provider %s", name)
        report.add(
            "CLI",
            "error",
            f"Unable to check {name} CLI: {exc}",
            provider=name,
            command=command,
            version=None,
        )
        return

    version, version_error = _get_version(command)
    if version_error:
        report.add(
            "CLI",
            "warning",
            f"{name} CLI is available, but its version could not be determined: {version_error}",
            provider=name,
            command=command,
            version=None,
        )
    else:
        report.add(
            "CLI",
            "pass",
            f"{name} CLI is available ({version}).",
            provider=name,
            command=command,
            version=version,
        )


def _provider_cli_available(report: DoctorReport, provider_name: str) -> bool:
    """Find the static CLI result that authorizes a live provider launch."""
    return any(
        result.code == "CLI"
        and result.status in ("pass", "warning")
        and result.details.get("provider") == provider_name
        for result in report.results
    )


def _get_version(command: str) -> tuple[str | None, str | None]:
    """Run a bounded version command and return its first non-empty line."""
    try:
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)

    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        return None, output or f"exited with status {result.returncode}"
    if not output:
        return None, "produced no output"
    return output.splitlines()[0], None


def _check_tcd_home(report: DoctorReport) -> None:
    """Test a real write because access bits do not cover all mounted filesystems."""
    try:
        TCD_HOME.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=TCD_HOME, prefix=".doctor-", delete=True):
            pass
    except OSError as exc:
        report.add("TCD_HOME", "error", f"{TCD_HOME} is not writable: {exc}", path=str(TCD_HOME))
    else:
        report.add("TCD_HOME", "pass", f"{TCD_HOME} is writable.", path=str(TCD_HOME))


def _check_jobs(report: DoctorReport, tmux: TmuxAdapter | None) -> None:
    """Surface running records with no matching tmux session before a timeout hides them."""
    paths = sorted(JOBS_DIR.glob("*.json")) if JOBS_DIR.is_dir() else []
    report.add("JOB_RECORDS", "pass", f"Found {len(paths)} job record(s).", count=len(paths))

    if tmux is None:
        report.add(
            "GHOST_JOBS",
            "skipped",
            "Cannot reconcile running jobs because tmux is unavailable.",
            count=None,
        )
        return

    try:
        sessions = tmux.list_sessions()
    except Exception as exc:
        logger.exception("doctor: could not list tmux sessions")
        report.add(
            "GHOST_JOBS",
            "warning",
            f"Could not reconcile running jobs with tmux sessions: {exc}",
            count=None,
        )
        return

    ghost_ids: list[str] = []
    for path in paths:
        try:
            job = Job.from_json(path.read_text())
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("doctor: ignoring unreadable job record %s: %s", path, exc)
            continue
        if job.status == "running" and job.tmux_session not in sessions:
            ghost_ids.append(job.id)

    if ghost_ids:
        report.add(
            "GHOST_JOBS",
            "warning",
            f"Found {len(ghost_ids)} running job record(s) without tmux sessions; `tcd jobs` will reconcile them automatically.",
            count=len(ghost_ids),
            job_ids=ghost_ids,
        )
    else:
        report.add("GHOST_JOBS", "pass", "No running job records are missing tmux sessions.", count=0, job_ids=[])


def _run_live_check(report: DoctorReport, tmux: TmuxAdapter, prov: Any, timeout: int) -> None:
    """Launch one disposable provider session and always reclaim its resources."""
    name = prov.name
    temp_dir = Path(tempfile.mkdtemp(prefix=f"tcd-doctor-{name}-"))
    job_id = f"doctor-{uuid.uuid4().hex}"
    session = f"tcd-doctor-{name}-{uuid.uuid4().hex[:8]}"
    job = Job(job_id, name, "pending", PROBE_PROMPT, str(temp_dir), session)
    deadline = time.monotonic() + timeout
    created = False
    logger.info("doctor: starting live probe provider=%s session=%s timeout=%ss", name, session, timeout)

    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        command = prov.build_launch_command(job)
        if not tmux.create_session(session, command, str(temp_dir)):
            report.add("LIVE_LAUNCH_FAILED", "error", f"Could not start a live {name} tmux session.", provider=name)
            return
        created = True

        ready, elapsed_ms, _ = wait_for_tui(
            tmux,
            session,
            prov,
            timeout_secs=_remaining_seconds(deadline),
        )
        if not ready:
            report.add(
                "READINESS_DRIFT",
                "error",
                f"{name} did not show its ready indicator within {timeout}s; the provider's readiness indicator may have drifted after an upstream UI change.",
                provider=name,
                tui_ready_indicator=getattr(prov, "tui_ready_indicator", None),
                elapsed_ms=elapsed_ms,
            )
            return

        report.add("LIVE_READINESS", "pass", f"{name} showed its ready indicator in {elapsed_ms}ms.", provider=name, elapsed_ms=elapsed_ms)
        if not tmux.send_text(session, PROBE_PROMPT):
            report.add(
                "DELIVERY_DRIFT",
                "error",
                f"{name} could not accept the delivery probe; prompt delivery may have drifted after an upstream UI change.",
                provider=name,
            )
            return

        delivered = verify_prompt_delivery(
            tmux,
            session,
            job_id,
            PROBE_PROMPT,
            retries=0,
            timeout_secs=_remaining_seconds(deadline),
        )
        if not delivered:
            report.add(
                "DELIVERY_DRIFT",
                "error",
                f"{name} could not confirm the delivery probe; prompt delivery may have drifted after an upstream UI change.",
                provider=name,
            )
            return

        report.add("LIVE_DELIVERY", "pass", f"{name} confirmed the delivery probe.", provider=name)
        logger.info("doctor: live probe passed provider=%s", name)
    except Exception as exc:
        logger.exception("doctor: live check failed for %s", name)
        report.add("LIVE_CHECK_FAILED", "error", f"Live check for {name} failed: {exc}", provider=name)
    finally:
        try:
            killed = tmux.kill_session(session)
            if created and not killed:
                report.add("LIVE_CLEANUP_FAILED", "error", f"Could not kill live tmux session {session}.", provider=name)
        except Exception as exc:
            logger.exception("doctor: failed to clean up tmux session %s", session)
            report.add("LIVE_CLEANUP_FAILED", "error", f"Could not kill live tmux session {session}: {exc}", provider=name)
        _cleanup_job_artifacts(job_id)
        try:
            shutil.rmtree(temp_dir)
        except OSError as exc:
            logger.exception("doctor: failed to remove live temporary directory %s", temp_dir)
            report.add("LIVE_CLEANUP_FAILED", "error", f"Could not remove temporary directory {temp_dir}: {exc}", provider=name)


def _remaining_seconds(deadline: float) -> float:
    """Keep readiness and delivery checks within the provider's total budget."""
    return max(0.0, deadline - time.monotonic())


def _cleanup_job_artifacts(job_id: str) -> None:
    """Remove only doctor-owned files so a probe cannot clutter user job history."""
    for suffix in (".log", ".prompt", ".turn-complete", ".events.jsonl"):
        try:
            (JOBS_DIR / f"{job_id}{suffix}").unlink(missing_ok=True)
        except OSError:
            logger.warning("doctor: unable to remove probe artifact %s%s", job_id, suffix)
