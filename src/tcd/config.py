"""Global configuration and path constants."""

from __future__ import annotations

from pathlib import Path

TCD_HOME = Path.home() / ".tcd"
JOBS_DIR = TCD_HOME / "jobs"
LOG_FILE = TCD_HOME / "tcd.log"

DEFAULT_TIMEOUT_MINUTES = 60
TMUX_SESSION_PREFIX = "tcd"


def ensure_dirs() -> None:
    """Create required directories if they don't exist."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def job_json_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def job_log_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.log"


def job_prompt_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.prompt"


def job_signal_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.turn-complete"


def job_events_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.events.jsonl"
