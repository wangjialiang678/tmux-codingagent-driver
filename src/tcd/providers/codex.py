"""Codex CLI provider."""

from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tcd.config import job_log_path, job_signal_path
from tcd.job import Job
from tcd.provider import CompletionResult, Provider, register_provider
from tcd.tmux_adapter import TmuxAdapter

logger = logging.getLogger(__name__)

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
MODEL_RE = re.compile(r"^[a-zA-Z0-9._:/-]+$")


# ---------------------------------------------------------------------------
# Structured Codex output (ported from tmux-bridge/output.py)
# ---------------------------------------------------------------------------


@dataclass
class CodexOutput:
    """Parsed output from Codex CLI's NDJSON event stream.

    Provides structured access to thread metadata, agent messages,
    modified files, and token usage.
    """

    thread_id: str | None = None
    agent_messages: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)
    summary: str = ""
    raw_events: list[dict] = field(default_factory=list)


def parse_codex_ndjson(text: str) -> CodexOutput:
    """Parse Codex CLI's NDJSON output into structured data.

    Handles event types:
    - thread.started → thread_id
    - item.completed (agent_message) → messages
    - item.completed (apply_patch) → files_modified
    - event_msg (token_count) → token usage
    """
    output = CodexOutput()

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue

        output.raw_events.append(event)
        etype = event.get("type", "")

        if etype == "thread.started":
            output.thread_id = event.get("thread_id")

        elif etype == "item.completed":
            item = event.get("item", {})
            itype = item.get("type", "")
            if itype == "agent_message":
                text_content = item.get("text", "")
                if text_content:
                    output.agent_messages.append(text_content)
            elif itype == "function_call" and item.get("name") == "apply_patch":
                args_str = item.get("arguments", "")
                file_path = _extract_file_from_patch(args_str)
                if file_path and file_path not in output.files_modified:
                    output.files_modified.append(file_path)

        elif etype == "event_msg":
            if "token_count" in event:
                tc = event["token_count"]
                if isinstance(tc, dict):
                    output.tokens = {
                        "input": tc.get("input_tokens", 0),
                        "output": tc.get("output_tokens", 0),
                    }

        elif etype == "message" and "content" in event:
            output.agent_messages.append(event["content"])

    if output.agent_messages:
        output.summary = output.agent_messages[-1][:500]

    return output


def _extract_file_from_patch(args_str: str) -> str | None:
    """Extract file path from an apply_patch function call arguments."""
    if not args_str:
        return None
    try:
        args = json.loads(args_str)
        if isinstance(args, dict):
            return args.get("file", args.get("path"))
    except json.JSONDecodeError:
        match = re.search(r'"(?:file|path)"\s*:\s*"([^"]+)"', args_str)
        return match.group(1) if match else None
    return None


@register_provider
class CodexProvider(Provider):
    """Adapter for the Codex CLI (codex)."""

    name = "codex"
    cli_command = "codex"
    tui_ready_indicator = "›"

    def check_cli(self) -> None:
        if shutil.which(self.cli_command) is None:
            raise FileNotFoundError(
                f"{self.cli_command} not found in PATH. "
                f"Install from: https://github.com/openai/codex"
            )

    def build_launch_command(self, job: Job) -> str:
        """Build: script -q <log> codex -c notify=[hook] -a never ..."""
        log_file = str(job_log_path(job.id))

        # Path to our notify hook script
        notify_hook = str(
            Path(__file__).parent.parent / "notify_hook.py"
        )

        parts = [self.cli_command]

        # notify hook config (use json.dumps to safely escape paths)
        notify_list = json.dumps([sys.executable, str(notify_hook), job.id])
        notify_cfg = f"notify={notify_list}"
        parts.append(f"-c '{notify_cfg}'")

        # auto-approve all operations
        parts.append("-a never")

        # sandbox mode (default: danger-full-access)
        sandbox = job.sandbox or "danger-full-access"
        parts.append(f"-s {sandbox}")

        # model override
        if job.model:
            if not MODEL_RE.fullmatch(job.model):
                raise ValueError(f"Invalid model name: {job.model!r}")
            model_cfg = f"model={json.dumps(job.model)}"
            parts.append(f"-c {shlex.quote(model_cfg)}")

        inner_cmd = " ".join(parts)

        # Wrap with script for log persistence + read to keep session alive
        script_cmd = TmuxAdapter.build_script_command(log_file, inner_cmd)
        return f'{script_cmd}; echo "\\n\\n[tcd: session complete]"; read'

    def build_prompt_wrapper(self, message: str, req_id: str) -> str:
        """Codex uses notify-hook, no marker wrapping needed."""
        return message

    def detect_completion(self, job: Job) -> CompletionResult | None:
        """Check signal file written by notify hook."""
        signal_path = job_signal_path(job.id)
        if signal_path.exists():
            try:
                data = json.loads(signal_path.read_text())
                tokens = self._extract_tokens(job)
                return CompletionResult(
                    state="idle",
                    last_agent_message=data.get("lastAgentMessage"),
                    turn_id=data.get("turnId"),
                    tokens=tokens,
                )
            except (json.JSONDecodeError, OSError):
                return CompletionResult(state="idle")
        return None  # inconclusive

    def _extract_tokens(self, job: Job) -> dict[str, int] | None:
        """Try to extract token usage from the Codex NDJSON session file."""
        try:
            output = self.parse_response_structured(job)
            if output and output.tokens:
                return output.tokens
        except Exception:
            logger.debug("Failed to extract tokens for job %s", job.id)
        return None

    def parse_response(self, job: Job) -> str | None:
        """Try to parse response from Codex JSONL session file."""
        session_path = self._find_session_file(job)
        if session_path is None:
            return None
        try:
            return self._parse_jsonl(session_path)
        except Exception as exc:
            logger.warning("Failed to parse Codex session %s: %s", session_path, exc)
            return None

    def parse_response_structured(self, job: Job) -> CodexOutput | None:
        """Parse response into a structured CodexOutput with full metadata.

        Use this instead of parse_response() when you need thread_id,
        files_modified, token counts, etc.
        """
        session_path = self._find_session_file(job)
        if session_path is None:
            return None
        try:
            content = session_path.read_text(errors="replace")
            return parse_codex_ndjson(content)
        except Exception as exc:
            logger.warning("Failed to parse Codex session %s: %s", session_path, exc)
            return None

    def get_session_log_path(self, job: Job) -> Path | None:
        return self._find_session_file(job)

    def _find_session_file(self, job: Job) -> Path | None:
        """Find the Codex session file by scanning ~/.codex/sessions/.

        Strategy: find the most recently modified .jsonl file created after the job.
        """
        if not CODEX_SESSIONS_DIR.exists():
            return None

        # Also try to extract session ID from script log
        session_id = self._extract_session_id(job)
        if session_id:
            # Search by session ID
            for p in CODEX_SESSIONS_DIR.rglob("*.jsonl"):
                if session_id in p.name or session_id in str(p):
                    return p

        # Fallback: find newest .jsonl modified after job creation
        candidates: list[Path] = []
        for p in CODEX_SESSIONS_DIR.rglob("*.jsonl"):
            candidates.append(p)

        if not candidates:
            return None

        # Return the most recently modified
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def _extract_session_id(self, job: Job) -> str | None:
        """Try to extract session ID from the script log."""
        log_path = job_log_path(job.id)
        if not log_path.exists():
            return None
        try:
            content = log_path.read_text(errors="replace")
            match = re.search(r"session id:\s*([0-9a-f-]{8,})", content, re.IGNORECASE)
            if match:
                return match.group(1)
        except OSError:
            pass
        return None

    @staticmethod
    def _parse_jsonl(path: Path) -> str | None:
        """Parse Codex JSONL session file for the last agent message."""
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
                # Look for agent_message type entries
                if isinstance(entry, dict):
                    msg_type = entry.get("type", "")
                    if "agent_message" in msg_type or "message" in msg_type:
                        content = entry.get("content") or entry.get("message") or entry.get("text")
                        if content and isinstance(content, str):
                            last_message = content
        except OSError:
            return None
        return last_message
