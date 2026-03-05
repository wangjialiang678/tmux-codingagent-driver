"""Marker protocol detection for TCD_REQ/TCD_DONE."""

from __future__ import annotations

import re

# How many lines from the end to scan (avoid reading full output)
SCAN_TAIL_LINES = 50

# Patterns
_DONE_RE = re.compile(r"TCD_DONE:(\S+)")
_CONTEXT_LIMIT_KEYWORDS = [
    "context window is full",
    "context limit",
    "conversation is too long",
    "maximum context length",
    "token limit",
    "out of context",
]


def build_marker_prompt(message: str, req_id: str) -> str:
    """Wrap a user message with TCD_REQ/TCD_DONE markers.

    The wrapped prompt asks the AI to output TCD_DONE:{req_id} at the end.
    """
    return (
        f"TCD_REQ:{req_id}\n"
        f"{message}\n"
        f"IMPORTANT: When you have fully completed your response, "
        f"output this exact line at the very end:\n"
        f"TCD_DONE:{req_id}"
    )


def scan_for_marker(text: str, req_id: str) -> bool:
    """Check whether TCD_DONE:{req_id} appears in the tail of *text*."""
    # Only scan last N lines for performance
    lines = text.splitlines()
    tail = "\n".join(lines[-SCAN_TAIL_LINES:])
    if req_id.endswith("-"):
        # Prefix mode for {job_id}-{turn_count}- when timestamp is unknown.
        pattern = rf"^TCD_DONE:{re.escape(req_id)}\d+\s*$"
    else:
        # Full req_id mode must match a complete marker line.
        pattern = rf"^TCD_DONE:{re.escape(req_id)}\s*$"
    return re.search(pattern, tail, re.MULTILINE) is not None


def scan_for_context_limit(text: str) -> bool:
    """Check whether the text contains context-limit indicators."""
    lower = text.lower()
    return any(kw in lower for kw in _CONTEXT_LIMIT_KEYWORDS)


def extract_done_req_id(text: str) -> str | None:
    """Extract the req_id from the last TCD_DONE marker in text."""
    lines = text.splitlines()
    tail = "\n".join(lines[-SCAN_TAIL_LINES:])
    matches = _DONE_RE.findall(tail)
    return matches[-1] if matches else None
