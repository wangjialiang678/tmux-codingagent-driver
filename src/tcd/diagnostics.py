"""Rule-based diagnostics for job health."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from tcd.event_log import load_events
from tcd.job import Job

logger = logging.getLogger(__name__)

_WRITE_INTENT_KEYWORDS = ["修改", "修复", "fix", "edit", "write", "create", "save"]
_PERMISSION_PHRASES = ["Operation not permitted", "Permission denied", "read-only"]


@dataclass
class Warning:
    code: str
    message: str
    severity: Literal["info", "warn", "error"]


def _time_diff(ts1: str, ts2: str) -> float:
    """Return difference in seconds between two ISO timestamps."""
    try:
        dt1 = datetime.fromisoformat(ts1)
        dt2 = datetime.fromisoformat(ts2)
        return (dt2 - dt1).total_seconds()
    except (ValueError, TypeError):
        return 0.0


def _elapsed_seconds(job: Job) -> int:
    """Return seconds since job start time."""
    start = job.started_at or job.created_at
    if not start:
        return 0

    try:
        dt = datetime.fromisoformat(start)
        return int((datetime.now(timezone.utc) - dt).total_seconds())
    except (ValueError, TypeError):
        return 0


def diagnose(job: Job, pane_tail: str | None = None) -> list[Warning]:
    """Run diagnostic rules against a job's current state.

    This function is best-effort and must never raise.
    """
    warnings: list[Warning] = []

    # R1: Sandbox mismatch
    try:
        if job.sandbox in (None, "workspace-write"):
            prompt_lower = (job.prompt or "").lower()
            if any(keyword in prompt_lower for keyword in _WRITE_INTENT_KEYWORDS):
                warnings.append(
                    Warning(
                        code="SANDBOX_MISMATCH",
                        message=(
                            "Prompt contains write intent but "
                            f"sandbox={job.sandbox or 'workspace-write'}"
                        ),
                        severity="warn",
                    )
                )
    except Exception:
        logger.exception("R1 diagnostics failed for job %s", getattr(job, "id", "unknown"))

    # R2: Stall detection
    try:
        events = load_events(job.id)
        check_events = [entry for entry in events if entry.get("event") == "job.checked"]
        if len(check_events) >= 4:
            recent = check_events[-4:]
            if all(entry.get("state") == "working" for entry in recent):
                span = _time_diff(str(recent[0].get("ts", "")), str(recent[-1].get("ts", "")))
                if span > 60:
                    warnings.append(
                        Warning(
                            code="STALL",
                            message=f"No state change in {span:.0f}s ({len(recent)} checks)",
                            severity="warn",
                        )
                    )
    except Exception:
        logger.exception("R2 diagnostics failed for job %s", getattr(job, "id", "unknown"))

    # R3: Permission error in pane output
    try:
        if pane_tail:
            pane_lower = pane_tail.lower()
            for phrase in _PERMISSION_PHRASES:
                if phrase.lower() in pane_lower:
                    warnings.append(
                        Warning(
                            code="PERMISSION_ERROR",
                            message=f"Found '{phrase}' in pane output",
                            severity="error",
                        )
                    )
                    break
    except Exception:
        logger.exception("R3 diagnostics failed for job %s", getattr(job, "id", "unknown"))

    # R4: Turn-0 stuck
    try:
        if job.turn_count == 0 and job.turn_state == "working":
            elapsed = _elapsed_seconds(job)
            if elapsed > 120:
                warnings.append(
                    Warning(
                        code="TURN0_STUCK",
                        message=f"Still on turn 0 after {elapsed}s",
                        severity="warn",
                    )
                )
    except Exception:
        logger.exception("R4 diagnostics failed for job %s", getattr(job, "id", "unknown"))

    return warnings
