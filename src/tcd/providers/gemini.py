"""Gemini CLI provider."""

from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
from pathlib import Path

from tcd.config import job_log_path, job_signal_path
from tcd.idle_detector import IdleDetector
from tcd.job import Job
from tcd.marker_detector import build_marker_prompt, scan_for_context_limit, scan_for_marker
from tcd.provider import CompletionResult, Provider, register_provider
from tcd.tmux_adapter import TmuxAdapter

logger = logging.getLogger(__name__)

IDLE_THRESHOLD = 15.0  # seconds (Gemini is typically faster)
MODEL_RE = re.compile(r"^[a-zA-Z0-9._:/-]+$")
REQ_RE = re.compile(r"TCD_REQ:(\S+)")
DONE_RE = re.compile(r"TCD_DONE:(\S+)")


@register_provider
class GeminiProvider(Provider):
    """Adapter for Gemini CLI (gemini)."""

    name = "gemini"
    cli_command = "gemini"
    tui_ready_indicator = "Type your message"

    def check_cli(self) -> None:
        if shutil.which(self.cli_command) is None:
            raise FileNotFoundError(
                f"{self.cli_command} not found in PATH. "
                f"Install from: https://github.com/google-gemini/gemini-cli"
            )

    def build_launch_command(self, job: Job) -> str:
        """Build: script -q <log> gemini --yolo [-m model]"""
        log_file = str(job_log_path(job.id))

        parts = [self.cli_command, "--yolo"]

        if job.model:
            if not MODEL_RE.fullmatch(job.model):
                raise ValueError(f"Invalid model name: {job.model!r}")
            parts.extend(["-m", shlex.quote(job.model)])

        inner_cmd = " ".join(parts)
        script_cmd = TmuxAdapter.build_script_command(log_file, inner_cmd)
        return f'{script_cmd}; echo "\\n\\n[tcd: session complete]"; read'

    def build_prompt_wrapper(self, message: str, req_id: str) -> str:
        """Wrap with TCD_REQ/TCD_DONE markers."""
        return build_marker_prompt(message, req_id)

    def detect_completion(self, job: Job) -> CompletionResult | None:
        """Check signal file → marker scan → idle detection."""
        # Strategy 1: signal file
        signal_path = job_signal_path(job.id)
        if signal_path.exists():
            try:
                data = json.loads(signal_path.read_text())
                return CompletionResult(
                    state=data.get("state", "idle"),
                    last_agent_message=data.get("lastAgentMessage"),
                )
            except (json.JSONDecodeError, OSError, TypeError):
                logger.exception("Failed to read Gemini signal file for job %s", job.id)
                return CompletionResult(state="idle")

        # Strategy 2: marker scan via capture-pane
        tmux = TmuxAdapter()
        if tmux.session_exists(job.tmux_session):
            pane = tmux.capture_pane(job.tmux_session)
            if pane:
                # Check context limit
                if scan_for_context_limit(pane):
                    self._write_signal(job, "context_limit")
                    return CompletionResult(state="context_limit")

                # Check for TCD_DONE marker
                req_id = f"{job.id}-{job.turn_count}-"
                if scan_for_marker(pane, req_id):
                    self._write_signal(job, "idle")
                    return CompletionResult(state="idle")

                # Strategy 3: idle detection (quick check)
                detector = IdleDetector(tmux=tmux, idle_threshold=IDLE_THRESHOLD, poll_interval=2.0)
                if detector.is_idle(job.tmux_session):
                    self._write_signal(job, "idle")
                    return CompletionResult(state="idle")

        return None  # inconclusive

    def parse_response(self, job: Job) -> str | None:
        """Parse response from capture-pane (Gemini has no standard session file)."""
        tmux = TmuxAdapter()
        if tmux.session_exists(job.tmux_session):
            pane = tmux.capture_pane(job.tmux_session)
            if pane:
                return self._extract_response(pane)
        return None

    def get_session_log_path(self, job: Job) -> Path | None:
        """Gemini relies on capture-pane + script log; no native session file."""
        log_path = job_log_path(job.id)
        return log_path if log_path.exists() else None

    def _write_signal(self, job: Job, state: str) -> None:
        """Write the turn-complete signal file."""
        import json
        signal_path = job_signal_path(job.id)
        try:
            signal_path.write_text(json.dumps({
                "state": state,
                "provider": "gemini",
            }))
        except OSError as exc:
            logger.error("Failed to write signal file: %s", exc)

    @staticmethod
    def _extract_response(pane_text: str) -> str | None:
        """Extract the last response block from pane text."""
        return _extract_between_markers(pane_text)


def _extract_between_markers(text: str) -> str | None:
    """Extract Gemini response between the echoed and final TCD_DONE markers."""
    lines = text.splitlines()
    last_req_idx: int | None = None
    last_req_id: str | None = None

    for i, line in enumerate(lines):
        match = REQ_RE.search(line)
        if match:
            last_req_idx = i
            last_req_id = match.group(1)

    if last_req_idx is None or last_req_id is None:
        return None

    done_indices: list[int] = []
    for i in range(last_req_idx + 1, len(lines)):
        match = DONE_RE.search(lines[i])
        if match and match.group(1) == last_req_id:
            done_indices.append(i)

    # We expect two DONE markers for a completed turn:
    # one from the prompt wrapper, one from Gemini's final response line.
    if len(done_indices) < 2:
        return None

    between = lines[done_indices[0] + 1:done_indices[-1]]
    result = "\n".join(between).strip()
    if not result:
        return None
    return result
