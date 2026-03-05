"""tmux operation primitives."""

from __future__ import annotations

import enum
import logging
import platform
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT = 10  # seconds
LONG_PROMPT_THRESHOLD = 5000  # characters
MAX_SENDKEYS_BYTES = 4096  # tmux send-keys single-call limit (from NTM)


class CaptureDepth(enum.IntEnum):
    """Semantic constants for capture-pane line counts.

    Ported from tmux-bridge (inspired by NTM).
    Use these instead of magic numbers when calling capture_pane().
    """

    STATUS = 20        # Quick status polling (e.g. completion check)
    HEALTH = 50        # Error analysis / health check
    CONTEXT = 500      # Comprehensive output capture (default)
    CHECKPOINT = 2000  # Session recovery / full dump
    FULL = -1          # Entire scrollback history


class TmuxError(Exception):
    """Raised when a tmux operation fails."""


class TmuxNotFoundError(TmuxError):
    """Raised when tmux is not installed."""



def _run(args: list[str], *, check: bool = True, timeout: int = SUBPROCESS_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with timeout and capture output."""
    logger.debug("Running: %s", " ".join(args))
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


class TmuxAdapter:
    """Thin wrapper around tmux CLI commands."""

    def __init__(self) -> None:
        self._tmux = shutil.which("tmux")

    def check_tmux(self) -> None:
        """Raise TmuxNotFoundError if tmux is not installed."""
        if self._tmux is None:
            hint = "brew install tmux" if platform.system() == "Darwin" else "apt install tmux"
            raise TmuxNotFoundError(
                f"tmux not found. Install with: {hint}"
            )

    @property
    def tmux(self) -> str:
        self.check_tmux()
        assert self._tmux is not None
        return self._tmux

    def create_session(self, name: str, cmd: str, cwd: str) -> bool:
        """Create a detached tmux session running *cmd* in *cwd*.

        Returns True on success, False on failure.
        """
        try:
            _run([
                self.tmux, "new-session",
                "-d",             # detached
                "-s", name,       # session name
                "-c", cwd,        # working directory
                cmd,              # shell command
            ])
            logger.info("Created tmux session: %s", name)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("Failed to create session %s: %s", name, exc)
            return False

    def session_exists(self, name: str) -> bool:
        """Check whether a tmux session with *name* exists."""
        try:
            _run([self.tmux, "has-session", "-t", name])
            return True
        except subprocess.CalledProcessError:
            return False

    def send_keys(self, session: str, text: str) -> bool:
        """Inject *text* into the tmux session via send-keys -l + Enter.

        Uses ``-l`` (literal) so tmux does not interpret key names.
        Text is chunked at UTF-8 rune boundaries (max 4096 bytes per chunk)
        to avoid splitting multi-byte characters (ported from tmux-bridge).
        For text >= LONG_PROMPT_THRESHOLD, use send_long_text() instead.
        """
        try:
            for chunk in _utf8_chunks(text, MAX_SENDKEYS_BYTES):
                _run([self.tmux, "send-keys", "-t", session, "-l", chunk])
            time.sleep(0.2)  # let TUI process input before Enter
            _run([self.tmux, "send-keys", "-t", session, "Enter"])
            logger.debug("Sent %d chars to session %s", len(text), session)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("send_keys failed for %s: %s", session, exc)
            return False

    def send_enter(self, session: str) -> bool:
        """Send just the Enter key to a tmux session."""
        try:
            _run([self.tmux, "send-keys", "-t", session, "Enter"])
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("send_enter failed for %s: %s", session, exc)
            return False

    def send_long_text(self, session: str, text: str) -> bool:
        """Inject long text via load-buffer + paste-buffer + Enter.

        Uses ``-p`` (bracketed paste) so Ink-based TUIs (Claude Code, etc.)
        treat newlines as line breaks rather than submit actions.
        """
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                f.write(text)
                tmp_path = f.name

            _run([self.tmux, "load-buffer", tmp_path])
            _run([self.tmux, "paste-buffer", "-p", "-t", session])
            time.sleep(0.5)  # let TUI process pasted content
            _run([self.tmux, "send-keys", "-t", session, "Enter"])

            logger.debug("Sent %d chars (long) to session %s", len(text), session)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.error("send_long_text failed for %s: %s", session, exc)
            return False
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def send_text(self, session: str, text: str) -> bool:
        """Auto-select send_keys or send_long_text.

        Uses send_long_text (bracketed paste) for long text *or* text
        containing newlines — Ink-based TUIs (Claude Code, Codex) handle
        multi-line paste correctly, whereas send-keys -l sends each
        newline as an Enter keystroke which may trigger premature submit.
        """
        if len(text) >= LONG_PROMPT_THRESHOLD or "\n" in text:
            return self.send_long_text(session, text)
        return self.send_keys(session, text)

    def capture_pane(
        self,
        session: str,
        *,
        start_line: str = "-",
        depth: CaptureDepth | int | None = None,
    ) -> str | None:
        """Capture pane content from a tmux session.

        Args:
            session: tmux session name.
            start_line: Raw tmux -S value (legacy, default ``"-"`` = full).
            depth: Semantic capture depth. When provided, overrides *start_line*.
                   Use ``CaptureDepth.STATUS`` for quick checks,
                   ``CaptureDepth.FULL`` (or ``-1``) for entire scrollback.

        Returns the captured text or None if the session doesn't exist.
        """
        if depth is not None:
            start_line = "-" if depth == CaptureDepth.FULL else str(-abs(depth))

        try:
            result = _run(
                [self.tmux, "capture-pane", "-t", session, "-p", "-S", start_line],
                timeout=SUBPROCESS_TIMEOUT,
            )
            return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("capture_pane failed for %s: %s", session, exc)
            return None

    def kill_session(self, session: str) -> bool:
        """Kill a tmux session."""
        try:
            _run([self.tmux, "kill-session", "-t", session])
            logger.info("Killed tmux session: %s", session)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("kill_session failed for %s: %s", session, exc)
            return False

    @staticmethod
    def build_script_command(log_file: str, inner_cmd: str) -> str:
        """Build a platform-aware `script` command for terminal recording.

        macOS:  script -q <file> <cmd>
        Linux:  script -q -c <cmd> <file>
        """
        log_file_q = shlex.quote(log_file)
        if platform.system() == "Darwin":
            return f"script -q {log_file_q} {inner_cmd}"
        else:
            return f"script -q -c {shlex.quote(inner_cmd)} {log_file_q}"


def _utf8_chunks(text: str, max_bytes: int = MAX_SENDKEYS_BYTES) -> list[str]:
    """Split text into chunks that fit within *max_bytes* each.

    Ported from tmux-bridge/transport.py (inspired by NTM).
    Splits at UTF-8 character boundaries so multi-byte characters
    (e.g. CJK, emoji) are never cut in half.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return [text]

    chunks: list[str] = []
    offset = 0
    while offset < len(encoded):
        end = min(offset + max_bytes, len(encoded))
        # Walk back to a UTF-8 character boundary
        # Continuation bytes have the form 0b10xxxxxx (0x80-0xBF)
        if end < len(encoded):
            while end > offset and (encoded[end] & 0xC0) == 0x80:
                end -= 1
        chunks.append(encoded[offset:end].decode("utf-8", errors="replace"))
        offset = end
    return chunks
