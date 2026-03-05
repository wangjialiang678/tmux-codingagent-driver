"""Job data structure and persistence."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from tcd.config import (
    JOBS_DIR,
    ensure_dirs,
    job_events_path,
    job_json_path,
    job_log_path,
    job_prompt_path,
    job_signal_path,
)

logger = logging.getLogger(__name__)


def _generate_id() -> str:
    """Generate an 8-character hex job ID."""
    return os.urandom(4).hex()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    provider: str
    status: Literal["pending", "running", "completed", "failed"]
    prompt: str
    cwd: str
    tmux_session: str
    model: str | None = None
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    result: str | None = None
    error: str | None = None
    turn_count: int = 0
    turn_state: Literal["working", "idle", "context_limit"] | None = None
    last_agent_message: str | None = None
    timeout_minutes: int = 60
    sandbox: str | None = None
    total_tokens: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> Job:
        # Filter out unknown keys for forward compatibility
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})

    @classmethod
    def from_json(cls, text: str) -> Job:
        return cls.from_dict(json.loads(text))


class JobManager:
    """Create, load, save, list, and clean jobs."""

    def __init__(self) -> None:
        ensure_dirs()

    def create_job(
        self,
        provider: str,
        prompt: str,
        cwd: str,
        *,
        model: str | None = None,
        timeout_minutes: int = 60,
        sandbox: str | None = None,
    ) -> Job:
        job_id = _generate_id()
        tmux_session = f"tcd-{provider}-{job_id}"
        job = Job(
            id=job_id,
            provider=provider,
            status="pending",
            prompt=prompt,
            cwd=cwd,
            tmux_session=tmux_session,
            model=model,
            timeout_minutes=timeout_minutes,
            sandbox=sandbox,
        )
        self.save_job(job)
        logger.info("Created job %s (provider=%s)", job_id, provider)
        return job

    def save_job(self, job: Job) -> None:
        """Atomic write: write to temp file then rename."""
        ensure_dirs()
        target = job_json_path(job.id)
        fd, tmp_path = tempfile.mkstemp(dir=JOBS_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(job.to_json())
            os.replace(tmp_path, target)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def load_job(self, job_id: str) -> Job | None:
        """Load a job from its JSON file. Returns None if not found."""
        path = job_json_path(job_id)
        if not path.exists():
            return None
        try:
            return Job.from_json(path.read_text())
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.error("Failed to load job %s: %s", job_id, exc)
            return None

    def list_jobs(self, *, status_filter: str | None = None) -> list[Job]:
        """List all jobs, optionally filtered by status."""
        ensure_dirs()
        jobs: list[Job] = []
        for p in JOBS_DIR.glob("*.json"):
            job = self.load_job(p.stem)
            if job is not None:
                if status_filter is None or job.status == status_filter:
                    jobs.append(job)
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def clean_jobs(self, *, include_running: bool = False) -> int:
        """Remove completed/failed jobs and their associated files.

        Returns the number of jobs cleaned.
        """
        removable = {"completed", "failed"}
        if include_running:
            removable.add("running")
            removable.add("pending")

        cleaned = 0
        for job in self.list_jobs():
            if job.status in removable:
                self._remove_job_files(job.id)
                cleaned += 1
        return cleaned

    @staticmethod
    def _remove_job_files(job_id: str) -> None:
        for path_fn in (job_json_path, job_log_path, job_prompt_path, job_signal_path, job_events_path):
            path_fn(job_id).unlink(missing_ok=True)
