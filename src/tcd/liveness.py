"""Is this job actually making progress, and if not, why?

Two questions, deliberately kept apart:

1. **Is it moving?** Answered from evidence outside our own bookkeeping: the
   mtime of the terminal log the agent's output is written into. A ``state``
   field we computed ourselves can only ever tell us that we are
   self-consistent. (The event log's mtime is reported too, but does not vote:
   we write it on every poll, so it would only ever witness our own polling.)
2. **Why is it not moving?** Answered by classifying the last screen the agent
   drew. The screen text is the agent's own output, not our inference.

Neither answer is a verdict on its own: a silent job sitting at an idle prompt
may simply have just finished, and a keypress prompt that appeared two seconds
ago is not yet stuck. :func:`assess` crosses the two and returns a verdict plus
the raw evidence behind it.

``unknown`` is a first-class state. A screen we cannot classify must be
reported as unclassified *with its text attached*, never folded into
"working". On 2026-09-17 six Codex jobs sat dead at a usage-limit notice for
an hour while ``state`` said ``working``: the pane had simply stopped saying
``Working (``, and nothing owned the question "so what is it saying instead?".

Extending this: add feature patterns to the tables below. They are ordered and
each carries the human-readable reason reported to the caller. Matching stays
deliberately loose (several spellings per condition) because provider wording
changes without notice.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from tcd.config import job_events_path, job_log_path
from tcd.provider import PROVIDER_ERROR_SIGNS

# The five mutually exclusive screen states. `unknown` is not a failure of the
# classifier to be fixed away — it is the honest answer for an unseen screen.
PANE_STATES = ("working", "awaiting_input", "blocked", "idle_prompt", "unknown")

# How much of the pane tail to classify. The whole scrollback would match
# anything an agent ever printed (including prose *about* rate limits); the
# live status area is what tells us the current state.
DEFAULT_TAIL_LINES = 25

# How long a job may stay quiet before silence itself is notable. A job running
# a full test suite legitimately prints nothing for a long time, while a
# read-only analysis job should not — so this is a parameter, not a constant
# with a "right" value. Callers should raise it for test-heavy packages.
DEFAULT_SILENCE_SECONDS = int(os.environ.get("TCD_SILENCE_SECONDS", "600"))


# ---------------------------------------------------------------------------
# Feature tables — extend these, not the logic
# ---------------------------------------------------------------------------

# Live activity indicators across the CLIs tcd drives. This is the default for
# `working_markers` and it is deliberately a *superset*: these strings only ever
# make the classifier more cautious (they veto a "it stopped" verdict, see
# `classify_pane`), so casting the net wide costs nothing and a caller that
# forgets to pass its provider's markers still gets the guard.
#
# A safety check must not default to off. The first version of this module took
# `working_markers=()`, which meant the contradiction guard — the whole second
# line of defence — silently did nothing unless the caller remembered to opt in.
# A caller that genuinely wants no activity detection can still say so
# explicitly with `working_markers=()`.
DEFAULT_WORKING_MARKERS: tuple[str, ...] = (
    "working (",
    "esc to interrupt",
    "esc to cancel",
    "waiting for agents",
)

# The focused row of a Codex-style option list: "› 1. Switch to ...".
_CHOICE_POINTER_RE = re.compile(r"^\s*[›❯▸▶]\s*\d+\.\s+\S", re.MULTILINE)

# The screen is waiting for a human keypress. Nothing resumes on its own here,
# whatever the timers say. tcd never answers these: choosing costs money,
# changes the model, or grants a permission, and that is the driver's call.
AWAITING_INPUT_SIGNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"press\s+enter\s+to\s+confirm", re.IGNORECASE),
        "a prompt is waiting for Enter to confirm a choice",
    ),
    (
        re.compile(r"press\s+enter\s+to\s+(?:continue|select|accept|retry)", re.IGNORECASE),
        "a prompt is waiting for Enter",
    ),
    # Requiring the pointer glyph keeps ordinary numbered lists in agent output
    # from reading as a menu; classify_pane additionally requires a second
    # option, since a menu never has only one.
    (
        _CHOICE_POINTER_RE,
        "a numbered choice list has focus and is waiting for a selection",
    ),
    (
        re.compile(r"\bdo you trust\b", re.IGNORECASE),
        "the directory-trust dialog is open",
    ),
    (
        re.compile(r"\[y/n\]|\(y/n\)|\[Y/n\]|\[y/N\]"),
        "a yes/no confirmation is open",
    ),
    (
        re.compile(r"\b(?:select|choose)\s+an?\s+option\b", re.IGNORECASE),
        "an option picker is open",
    ),
    # "approve this" was dropped: an agent writes it about pull requests.
    (
        re.compile(r"\ballow\s+(?:this\s+)?command\b", re.IGNORECASE),
        "a permission approval dialog is open",
    ),
)

# A menu always has at least two options. Requiring a second one keeps a prompt
# that merely *starts* with "1." (a numbered task list pasted into the composer)
# from reading as an open picker.
_SECOND_OPTION_RE = re.compile(r"^\s*\d+\.\s+\S", re.MULTILINE)

# The agent has been told it cannot continue. Time does not fix these either,
# but unlike `awaiting_input` there may be nothing to press — someone has to
# buy capacity, fix credentials, or wait out a reset window.
BLOCKED_SIGNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"you'?ve\s+hit\s+your\s+usage\s+limit", re.IGNORECASE),
        "the account's usage limit is exhausted",
    ),
    (
        re.compile(r"\b(?:hit|reached|exceeded)\s+(?:your\s+|the\s+)?usage\s+limit\b", re.IGNORECASE),
        "the account's usage limit is exhausted",
    ),
    (
        re.compile(r"\busage\s+limit\s+(?:reached|exceeded|hit)\b", re.IGNORECASE),
        "the account's usage limit is exhausted",
    ),
    (
        re.compile(r"purchase\s+more\s+credits|out\s+of\s+credits|insufficient\s+credits", re.IGNORECASE),
        "the account is out of credits",
    ),
    (
        re.compile(r"\bquota\s+(?:exceeded|exhausted|reached)\b", re.IGNORECASE),
        "the account quota is exhausted",
    ),
    (
        re.compile(r"\brate\s+limit(?:s)?\s+(?:exceeded|reached|hit)\b", re.IGNORECASE),
        "the provider rate limit was hit",
    ),
    # A bare status number is not a feature. `429` alone matched Codex's own
    # folded-transcript chrome — "… +429 lines (ctrl + t to view transcript)" —
    # and reported a job that was actively working as blocked (2026-09-18).
    # A number only counts when something on the line says it IS a status.
    (
        re.compile(r"\b(?:HTTP\s*)?(?:status|code|error)\W{0,4}429\b", re.IGNORECASE),
        "the provider returned HTTP 429 (rate limited)",
    ),
    (
        re.compile(r"\b429\s+too\s+many\s+requests\b", re.IGNORECASE),
        "the provider returned HTTP 429 (rate limited)",
    ),
    (
        re.compile(r"\b(?:insufficient_quota|rate_limit_exceeded)\b"),
        "the provider returned a quota/rate-limit error code",
    ),
    # "giving up" and "max retries" on their own are things an agent says about
    # its own work ("giving up on the regex approach"). Anchor them to the
    # shape a retry loop actually prints when it quits.
    (
        re.compile(r"\bmax(?:imum)?\s+retries\s+(?:exceeded|reached|hit)\b", re.IGNORECASE),
        "the provider connection was abandoned after retries",
    ),
    (
        re.compile(r"\bgiving\s+up\s+after\s+\d+", re.IGNORECASE),
        "the provider connection was abandoned after retries",
    ),
    # "please log in" appears in any docs or web page the agent happens to read.
    # Only the CLI's own logged-out notice counts.
    (
        re.compile(r"\bsession\s+expired\b|\bnot\s+logged\s+in\b|\bcodex\s+login\b", re.IGNORECASE),
        "the provider session expired and needs re-authentication",
    ),
)

# Fatal API envelopes already catalogued for `tcd check`'s PROVIDER_ERROR
# warning are blocked screens too — one table, two consumers.
_BLOCKED_SIGNS_ALL: tuple[tuple[re.Pattern[str], str], ...] = BLOCKED_SIGNS + PROVIDER_ERROR_SIGNS

# When the block lifts. This is the single most useful thing on the screen for
# whoever has to decide "wait or pay", so it is extracted rather than left for
# a human to squint at.
RECOVERY_TIME_SIGNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"try\s+again\s+(?:at|on|after)\s+(?P<when>[^.\n]+?)\s*(?:\.|$)", re.IGNORECASE),
    re.compile(r"(?:resets?|renews?|available\s+again)\s+(?:at|on|in)\s+(?P<when>[^.\n]+?)\s*(?:\.|$)", re.IGNORECASE),
    re.compile(r"try\s+again\s+in\s+(?P<when>[^.\n]+?)\s*(?:\.|$)", re.IGNORECASE),
)

# An empty composer: the turn is over as far as the screen is concerned. That
# is not the same as the task being done, which only the contract can answer.
IDLE_PROMPT_SIGNS: tuple[re.Pattern[str], ...] = (
    # `>` alone is a blank markdown quote line, so only the TUI's own glyphs.
    re.compile(r"^\s*[›❯]\s*$", re.MULTILINE),
)


@dataclass
class PaneVerdict:
    """Classification of the last screen, with the lines that decided it."""

    state: str
    reason: str | None = None
    evidence: list[str] = field(default_factory=list)
    recover_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "pane_state": self.state,
            "reason": self.reason,
            "evidence": self.evidence,
            "recover_at": self.recover_at,
        }


@dataclass
class Liveness:
    """Movement signals crossed with the screen state."""

    pane: PaneVerdict
    verdict: str
    advice: str
    silence_s: int | None = None
    event_silence_s: int | None = None
    silence_threshold_s: int = DEFAULT_SILENCE_SECONDS
    pane_hash: str | None = None
    pane_tail: str = ""

    @property
    def silent(self) -> bool:
        """Quiet for longer than the caller's threshold.

        Only the job log votes. The event log is reported alongside it for
        context but deliberately does *not* count: `tcd check` emits a
        `job.checked` event on every poll, so polling a dead job would keep
        resetting its own evidence and prove it alive. A signal we write
        ourselves cannot witness us.

        Unknown silence (no log file yet) is not silence either: absence of a
        signal must not manufacture a verdict.
        """
        return self.silence_s is not None and self.silence_s >= self.silence_threshold_s

    @property
    def needs_human(self) -> bool:
        return self.verdict in _NEEDS_HUMAN

    def to_dict(self) -> dict:
        d = self.pane.to_dict()
        d.update(
            {
                "verdict": self.verdict,
                "advice": self.advice,
                "silent": self.silent,
                "silence_s": self.silence_s,
                "event_silence_s": self.event_silence_s,
                "silence_threshold_s": self.silence_threshold_s,
                "pane_hash": self.pane_hash,
                "needs_human": self.needs_human,
            }
        )
        # The point of `unknown` is that a person reads the screen, so the
        # screen has to travel with the verdict.
        if self.pane.state == "unknown":
            d["pane_tail"] = self.pane_tail
        return d


# ---------------------------------------------------------------------------
# Screen classification
# ---------------------------------------------------------------------------

def _tail(pane: str | None, tail_lines: int) -> str:
    lines = (pane or "").splitlines()
    return "\n".join(lines[-tail_lines:])


def _matching_lines(text: str, pattern: re.Pattern[str], limit: int = 3) -> list[str]:
    hits = [ln.strip() for ln in text.splitlines() if ln.strip() and pattern.search(ln)]
    if not hits:  # multi-line patterns can match across the joined text
        hits = [ln.strip() for ln in text.splitlines() if ln.strip()][-limit:]
    return hits[:limit]


def extract_recovery_time(text: str) -> str | None:
    """Pull "try again at <when>" out of a blocked screen, if it says."""
    for pattern in RECOVERY_TIME_SIGNS:
        m = pattern.search(text)
        if m:
            when = (m.group("when") or "").strip(" .,;")
            if when:
                return when
    return None


def classify_pane(
    pane: str | None,
    *,
    working_markers: Sequence[str] = DEFAULT_WORKING_MARKERS,
    composer_placeholders: Sequence[str] = (),
    tail_lines: int = DEFAULT_TAIL_LINES,
) -> PaneVerdict:
    """Sort the last screen into exactly one of :data:`PANE_STATES`.

    ``working_markers`` defaults to :data:`DEFAULT_WORKING_MARKERS` so the
    contradiction guard is on for every caller; pass a provider's own markers to
    narrow it, or ``()`` to turn activity detection off deliberately.
    ``composer_placeholders`` has no default on purpose: a default there would
    let idle screens read as ``idle_prompt`` instead of ``unknown``, which
    *reduces* how loudly an unrecognised screen is reported. Defaults may make
    the classifier more cautious, never less.

    Order matters, and it is not the obvious one. ``blocked`` and
    ``awaiting_input`` are tested *before* ``working``, because both can sit on
    screen while a stale spinner line is still drawn — and both mean no further
    progress without a person, which is the opposite of what "working" tells a
    caller. Cross-checking with silence (see :func:`assess`) is what keeps a
    freshly-drawn prompt from being called stuck.
    """
    text = _tail(pane, tail_lines)
    if not text.strip():
        return PaneVerdict("unknown", "the pane is empty", [])

    lowered = text.casefold()
    working_line = next(
        (m for m in working_markers if m and m.casefold() in lowered), None
    )

    def contradicted(kind: str, reason: str, pattern: re.Pattern[str]) -> PaneVerdict:
        """A "it stopped" sign drawn next to a live activity indicator.

        Both cannot be true, so we say so instead of picking the scarier one.
        `unknown` carries the screen out to a person, which is the right answer
        when our own signals disagree — on 2026-09-18 a working job was called
        `blocked` because a feature string matched TUI chrome, and the live
        `Working (…)` line two rows below was never consulted.
        """
        return PaneVerdict(
            "unknown",
            f"contradictory screen: a {kind} sign ({reason}) next to {working_line!r} — read it",
            _matching_lines(text, pattern) + [ln.strip() for ln in text.splitlines()
                                              if working_line.casefold() in ln.casefold()][:1],
        )

    for pattern, reason in _BLOCKED_SIGNS_ALL:
        if pattern.search(text):
            if working_line:
                return contradicted("blocked", reason, pattern)
            return PaneVerdict(
                "blocked",
                reason,
                _matching_lines(text, pattern),
                extract_recovery_time(text),
            )

    for pattern, reason in AWAITING_INPUT_SIGNS:
        if pattern.search(text):
            if pattern is _CHOICE_POINTER_RE and len(_SECOND_OPTION_RE.findall(text)) < 2:
                continue  # a lone "1." is a list, not a menu
            if working_line:
                return contradicted("awaiting-input", reason, pattern)
            return PaneVerdict("awaiting_input", reason, _matching_lines(text, pattern))

    if working_line:
        return PaneVerdict("working", f"the status area shows {working_line!r}", [])

    for placeholder in composer_placeholders:
        if placeholder and placeholder.casefold() in lowered:
            return PaneVerdict("idle_prompt", "the composer is back to its placeholder", [])

    for pattern in IDLE_PROMPT_SIGNS:
        if pattern.search(text):
            return PaneVerdict("idle_prompt", "the composer is empty", [])

    return PaneVerdict(
        "unknown",
        "the screen matches no known state — read it before assuming anything",
        [ln.strip() for ln in text.splitlines() if ln.strip()][-5:],
    )


# ---------------------------------------------------------------------------
# Movement signals
# ---------------------------------------------------------------------------

def file_silence_s(path: Path, *, now: float | None = None) -> int | None:
    """Seconds since *path* was last written, or None if it does not exist."""
    import time

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return max(0, int((now if now is not None else time.time()) - mtime))


def pane_fingerprint(pane: str | None) -> str | None:
    if not pane:
        return None
    return hashlib.sha256(pane.encode("utf-8", "replace")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Crossing the two
# ---------------------------------------------------------------------------

# (silent, pane_state) -> (verdict, advice). Written out in full rather than as
# nested ifs so that adding a state means adding rows, and so the table can be
# read against the situations it is meant to cover.
_DECISION: dict[tuple[bool, str], tuple[str, str]] = {
    (False, "working"): ("progressing", "Working and producing output; keep waiting."),
    (True, "working"): (
        "stalled",
        "The status area still claims activity but nothing has been written for a while; "
        "look at the pane before assuming it will finish.",
    ),
    (False, "awaiting_input"): (
        "awaiting_input_fresh",
        "A prompt just appeared. Do NOT answer it automatically — re-check shortly, "
        "and hand it to a person if it is still there.",
    ),
    (True, "awaiting_input"): (
        "awaiting_input",
        "Stuck on a prompt that needs a keypress; it will never resume on its own. "
        "Hand it to a person — answering it can change the model or spend money.",
    ),
    (False, "blocked"): (
        "blocked",
        "The agent reports it cannot continue. Read the reason; a person has to act.",
    ),
    (True, "blocked"): (
        "blocked",
        "The agent reports it cannot continue and has gone quiet. A person has to act.",
    ),
    (False, "idle_prompt"): (
        "turn_just_ended",
        "The composer is idle and output is recent — likely a turn that just finished. "
        "Judge completion from the contract and the deliverables, not from this.",
    ),
    (True, "idle_prompt"): (
        "idle_silent",
        "Idle and quiet: either the task is done or it stopped half-way. "
        "Check the final report and the deliverables.",
    ),
    (False, "unknown"): (
        "unknown_active",
        "The screen is unrecognised but still changing. Read the attached pane text.",
    ),
    (True, "unknown"): (
        "unknown_silent",
        "The screen is unrecognised and nothing has been written for a while. "
        "Do not report this as running — read the attached pane text.",
    ),
}

_NEEDS_HUMAN = {"awaiting_input", "blocked", "unknown_silent", "stalled"}


def assess(
    job_id: str,
    pane: str | None,
    *,
    working_markers: Sequence[str] = DEFAULT_WORKING_MARKERS,
    composer_placeholders: Sequence[str] = (),
    silence_threshold_s: int = DEFAULT_SILENCE_SECONDS,
    tail_lines: int = DEFAULT_TAIL_LINES,
    now: float | None = None,
) -> Liveness:
    """Answer "is it progressing" from the log mtime and the screen together."""
    verdict = classify_pane(
        pane,
        working_markers=working_markers,
        composer_placeholders=composer_placeholders,
        tail_lines=tail_lines,
    )
    live = Liveness(
        pane=verdict,
        verdict="",
        advice="",
        silence_s=file_silence_s(job_log_path(job_id), now=now),
        event_silence_s=file_silence_s(job_events_path(job_id), now=now),
        silence_threshold_s=silence_threshold_s,
        pane_hash=pane_fingerprint(pane),
        pane_tail=_tail(pane, tail_lines),
    )
    live.verdict, live.advice = _DECISION[(live.silent, verdict.state)]
    return live


def warning_for(live: Liveness) -> tuple[str, str, str] | None:
    """Render a liveness verdict as a (code, message, severity) warning."""
    if live.verdict in ("progressing", "turn_just_ended"):
        return None
    code = {
        "blocked": "BLOCKED",
        "awaiting_input": "AWAITING_INPUT",
        "awaiting_input_fresh": "AWAITING_INPUT",
        "stalled": "NO_PROGRESS",
        "idle_silent": "NO_PROGRESS",
        "unknown_active": "UNKNOWN_PANE",
        "unknown_silent": "UNKNOWN_PANE",
    }[live.verdict]
    severity = "error" if live.needs_human else "warn"
    bits = [live.advice]
    if live.pane.reason:
        bits.insert(0, live.pane.reason)
    if live.pane.recover_at:
        bits.append(f"try again at {live.pane.recover_at}")
    if live.silence_s is not None:
        bits.append(f"log silent for {live.silence_s}s")
    return code, " — ".join(bits), severity
