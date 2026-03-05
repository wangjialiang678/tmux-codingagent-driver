"""Claude Code CLI provider."""

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

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
IDLE_THRESHOLD = 20.0  # seconds
MODEL_RE = re.compile(r"^[a-zA-Z0-9._:/-]+$")


@register_provider
class ClaudeProvider(Provider):
    """Adapter for Claude Code CLI (claude)."""

    name = "claude"
    cli_command = "claude"
    tui_ready_indicator = "❯"

    def check_cli(self) -> None:
        if shutil.which(self.cli_command) is None:
            raise FileNotFoundError(
                f"{self.cli_command} not found in PATH. "
                f"Install from: https://docs.anthropic.com/en/docs/claude-code"
            )

    def build_launch_command(self, job: Job) -> str:
        """Build: script -q <log> claude --dangerously-skip-permissions ..."""
        log_file = str(job_log_path(job.id))

        parts = [self.cli_command, "--dangerously-skip-permissions"]

        if job.model:
            if not MODEL_RE.fullmatch(job.model):
                raise ValueError(f"Invalid model name: {job.model!r}")
            parts.extend(["-m", shlex.quote(job.model)])

        inner_cmd = " ".join(parts)
        script_cmd = TmuxAdapter.build_script_command(log_file, inner_cmd)
        # Unset CLAUDECODE to allow launching from inside a Claude Code session
        return f'unset CLAUDECODE; {script_cmd}; echo "\\n\\n[tcd: session complete]"; read'

    def build_prompt_wrapper(self, message: str, req_id: str) -> str:
        """Wrap with TCD_REQ/TCD_DONE markers."""
        return build_marker_prompt(message, req_id)

    def detect_completion(self, job: Job) -> CompletionResult | None:
        """Check signal file → marker scan → idle detection."""
        # Strategy 1: signal file (written by marker scanner in check/wait)
        signal_path = job_signal_path(job.id)
        if signal_path.exists():
            try:
                data = json.loads(signal_path.read_text())
                return CompletionResult(
                    state=data.get("state", "idle"),
                    last_agent_message=data.get("lastAgentMessage"),
                )
            except (json.JSONDecodeError, OSError):
                return CompletionResult(state="idle")

        # Strategy 2: marker scan via capture-pane
        tmux = TmuxAdapter()
        if tmux.session_exists(job.tmux_session):
            pane = tmux.capture_pane(job.tmux_session)
            if pane:
                # Check context limit first
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
        """Try to parse response from Claude JSONL session files."""
        session_path = self._find_session_file(job)
        if session_path is None:
            return None
        try:
            return self._parse_jsonl(session_path)
        except Exception as exc:
            logger.warning("Failed to parse Claude session %s: %s", session_path, exc)
            return None

    def get_session_log_path(self, job: Job) -> Path | None:
        return self._find_session_file(job)

    def _write_signal(self, job: Job, state: str) -> None:
        """Write the turn-complete signal file."""
        signal_path = job_signal_path(job.id)
        try:
            signal_path.write_text(json.dumps({
                "state": state,
                "provider": "claude",
            }))
        except OSError as exc:
            logger.error("Failed to write signal file: %s", exc)

    def _find_session_file(self, job: Job) -> Path | None:
        """Find the Claude session file by scanning ~/.claude/projects/."""
        if not CLAUDE_PROJECTS_DIR.exists():
            return None

        # Scan for the most recently modified .jsonl file
        candidates: list[Path] = []
        for p in CLAUDE_PROJECTS_DIR.rglob("*.jsonl"):
            candidates.append(p)

        if not candidates:
            return None

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    @staticmethod
    def _parse_jsonl(path: Path) -> str | None:
        """Parse Claude JSONL session file for the last assistant message."""
        last_message = None
        try:
            for line in path.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    msg_type = entry.get("type", "")
                    role = entry.get("role", "")
                    # Claude JSONL uses "assistant" role
                    if role == "assistant" or "assistant" in msg_type:
                        content = entry.get("content")
                        if isinstance(content, list):
                            # Claude uses content blocks: [{"type": "text", "text": "..."}]
                            texts = [
                                b.get("text", "")
                                for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            ]
                            if texts:
                                last_message = "\n".join(texts)
                        elif isinstance(content, str) and content:
                            last_message = content
        except OSError:
            return None
        return last_message
