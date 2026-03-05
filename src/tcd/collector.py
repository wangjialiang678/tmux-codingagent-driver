"""Response collector — multi-strategy fallback for gathering AI output."""

from __future__ import annotations

import logging

from tcd.config import job_log_path
from tcd.job import Job
from tcd.output_cleaner import clean_output
from tcd.provider import get_provider
from tcd.tmux_adapter import TmuxAdapter

logger = logging.getLogger(__name__)


class ResponseCollector:
    """Collect AI responses with fallback: session file → capture-pane → log file."""

    def __init__(self, tmux: TmuxAdapter | None = None) -> None:
        self.tmux = tmux or TmuxAdapter()

    def collect(self, job: Job, *, raw: bool = False) -> str | None:
        """Collect the response for a job.

        Tries three strategies in order:
        1. Provider-specific session file parsing
        2. tmux capture-pane (if session still alive)
        3. Script log file fallback

        If raw=True, skip cleaning.
        """
        result = self._try_provider_parse(job)
        if result is None:
            result = self._try_capture_pane(job)
        if result is None:
            result = self._try_log_file(job)

        if result is not None and not raw:
            result = clean_output(result)

        return result

    def collect_full(self, job: Job) -> str | None:
        """Collect full scrollback output (cleaned)."""
        result = self._try_capture_pane(job, full=True)
        if result is None:
            result = self._try_log_file(job)
        if result is not None:
            result = clean_output(result)
        return result

    def collect_raw(self, job: Job) -> str | None:
        """Collect raw output (no cleaning)."""
        return self.collect(job, raw=True)

    def _try_provider_parse(self, job: Job) -> str | None:
        """Strategy 1: Provider-specific session file."""
        try:
            provider = get_provider(job.provider)
            result = provider.parse_response(job)
            if result:
                logger.debug("Got response from provider parse for job %s", job.id)
            return result
        except Exception as exc:
            logger.debug("Provider parse failed for job %s: %s", job.id, exc)
            return None

    def _try_capture_pane(self, job: Job, *, full: bool = False) -> str | None:
        """Strategy 2: tmux capture-pane."""
        if not self.tmux.session_exists(job.tmux_session):
            return None
        start_line = "-" if full else "-500"
        result = self.tmux.capture_pane(job.tmux_session, start_line=start_line)
        if result:
            logger.debug("Got response from capture-pane for job %s", job.id)
        return result

    def _try_log_file(self, job: Job) -> str | None:
        """Strategy 3: Script log file."""
        log_path = job_log_path(job.id)
        if not log_path.exists():
            return None
        try:
            content = log_path.read_text(errors="replace")
            if content:
                logger.debug("Got response from log file for job %s", job.id)
            return content
        except OSError as exc:
            logger.error("Failed to read log for job %s: %s", job.id, exc)
            return None
