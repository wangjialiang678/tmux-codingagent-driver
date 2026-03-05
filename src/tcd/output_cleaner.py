"""Clean ANSI escape sequences and TUI noise from terminal output.

Also provides multi-strategy JSON extraction (ported from tmux-bridge,
originally inspired by MCO parsing.py).
"""

from __future__ import annotations

import json
import re

# -- ANSI cleaning patterns --

# CSI sequences: ESC [ ... final_byte
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# OSC sequences: ESC ] ... (ST or BEL)
_ANSI_OSC = re.compile(r"\x1b\].*?(?:\x1b\\|\x07)")
# DCS sequences: ESC P ... ST  (ported from tmux-bridge)
_ANSI_DCS = re.compile(r"\x1bP.*?\x1b\\")
# Other ESC sequences
_ANSI_ESC = re.compile(r"\x1b[^[\]P].?")
# Carriage return (cursor reset, not followed by newline)
_CR = re.compile(r"\r(?!\n)")

# TUI noise patterns
_NOISE_PATTERNS = [
    re.compile(r".*esc to interrupt.*", re.IGNORECASE),
    re.compile(r".*% context left.*", re.IGNORECASE),
    re.compile(r".*background terminal running.*", re.IGNORECASE),
    re.compile(r".*\[codex-agent:.*", re.IGNORECASE),
    re.compile(r".*\[tcd:.*"),
    re.compile(r".*\[tmux-bridge:.*"),
    re.compile(r".*Checking for updates.*", re.IGNORECASE),
    re.compile(r".*A new version.*", re.IGNORECASE),
    re.compile(r"^\s*\d+\.\s*(Skip|Update|Dismiss).*", re.IGNORECASE),
    re.compile(r"^\s*Press Enter to close.*", re.IGNORECASE),
    re.compile(r"^\s*$"),  # blank lines
]

# TCD marker patterns
_MARKER_PATTERNS = [
    re.compile(r"TCD_REQ:[a-f0-9-]+"),
    re.compile(r"TCD_DONE:[a-f0-9-]+"),
    re.compile(r".*请在回复完成后.*TCD_DONE.*"),
]


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text.

    Handles CSI, OSC, DCS, and other ESC sequences.
    Also simulates carriage-return line overwriting.
    """
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_OSC.sub("", text)
    text = _ANSI_DCS.sub("", text)
    text = _ANSI_ESC.sub("", text)
    # Strip lone \r (not part of \r\n) using regex, then simulate overwriting.
    # First: remove \r that is NOT followed by \n (the _CR regex handles this).
    # Then handle overwriting on each resulting line.
    text = _CR.sub("\x00CR\x00", text)  # mark lone \r with sentinel
    lines = []
    for line in text.split("\n"):
        if "\x00CR\x00" in line:
            parts = line.split("\x00CR\x00")
            line = parts[-1]
        lines.append(line)
    return "\n".join(lines)


def remove_noise_lines(text: str) -> str:
    """Remove TUI noise lines."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        if any(p.match(line) for p in _NOISE_PATTERNS):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def remove_markers(text: str) -> str:
    """Remove TCD protocol markers."""
    for p in _MARKER_PATTERNS:
        text = p.sub("", text)
    return text


def dedup_lines(text: str) -> str:
    """Remove consecutive duplicate lines."""
    lines = text.splitlines()
    if not lines:
        return text
    result = [lines[0]]
    for line in lines[1:]:
        if line != result[-1]:
            result.append(line)
    return "\n".join(result)


def clean_output(text: str) -> str:
    """Full cleaning pipeline: ANSI → noise → markers → dedup → strip."""
    text = strip_ansi(text)
    text = remove_noise_lines(text)
    text = remove_markers(text)
    text = dedup_lines(text)
    return text.strip()


# ---------------------------------------------------------------------------
# Multi-strategy JSON extraction (ported from tmux-bridge/output.py,
# originally inspired by MCO parsing.py)
# ---------------------------------------------------------------------------


def extract_json_payloads(text: str) -> list[dict]:
    """Extract JSON objects from mixed text using a 4-layer strategy.

    Strategy 1: json.JSONDecoder.raw_decode() scanning
    Strategy 2: Markdown ```json ... ``` code blocks
    Strategy 3: Line-by-line json.loads()
    Strategy 4: Recursive string unwrap (JSON-in-JSON)
    """
    seen: set[str] = set()
    results: list[dict] = []

    def _add(obj: dict) -> None:
        key = json.dumps(obj, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            results.append(obj)

    def _add_any(obj: object) -> None:
        if isinstance(obj, dict):
            _add(obj)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    _add(item)

    # Strategy 1: raw_decode scanning
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        brace = text.find("{", idx)
        bracket = text.find("[", idx)
        if brace == -1 and bracket == -1:
            break
        start = min(p for p in (brace, bracket) if p >= 0)
        try:
            obj, end = decoder.raw_decode(text, start)
            _add_any(obj)
            idx = end
        except (json.JSONDecodeError, ValueError):
            idx = start + 1

    # Strategy 2: Markdown code blocks
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL):
        block = match.group(1).strip()
        try:
            _add_any(json.loads(block))
        except json.JSONDecodeError:
            pass

    # Strategy 3: Line-by-line
    for line in text.split("\n"):
        line = line.strip()
        if not line or not (line.startswith("{") or line.startswith("[")):
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                _add(obj)
        except json.JSONDecodeError:
            pass

    # Strategy 4: Recursive string unwrap
    for obj in list(results):
        for _key, val in obj.items():
            if isinstance(val, str) and val.startswith(("{", "[")):
                try:
                    inner = json.loads(val)
                    if isinstance(inner, dict):
                        _add(inner)
                except json.JSONDecodeError:
                    pass

    return results
