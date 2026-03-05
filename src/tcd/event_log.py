"""Append-only event logging for job lifecycle tracking."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tcd import config
from tcd.job import _now_iso

logger = logging.getLogger(__name__)


def job_events_path(job_id: str) -> Path:
    return config.JOBS_DIR / f"{job_id}.events.jsonl"


def emit(job_id: str, event: str, **data) -> None:
    """Append one JSONL event entry. This function must never raise."""
    try:
        entry = {"ts": _now_iso(), "event": event, **data}
        path = job_events_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Failed to emit event %s for job %s", event, job_id)


def load_events(job_id: str, event_filter: str | None = None) -> list[dict]:
    """Load and parse all events from a job event log."""
    path = job_events_path(job_id)
    if not path.exists():
        return []

    events: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping invalid event JSON for job %s", job_id)
                    continue
                if not isinstance(event, dict):
                    continue
                if event_filter is None or event.get("event") == event_filter:
                    events.append(event)
    except OSError:
        logger.exception("Failed to load events for job %s", job_id)
        return []

    return events
