"""Shared TUI-readiness and prompt-delivery helpers.

Both the CLI (``tcd start``) and the SDK (:class:`tcd.sdk.TCD`) need to wait
for an agent's TUI to become ready and then inject the prompt reliably. The
logic used to be duplicated in both places, which let them drift apart. It now
lives here so a single fix applies everywhere.

Two failure modes this guards against (observed with Codex):

* The readiness indicator (``›``) shows up in a startup banner *before* the TUI
  can accept input — MCP servers are still loading. Injecting the prompt that
  early silently drops the keystrokes. ``wait_for_tui`` therefore optionally
  waits for the pane to stop changing (``prov.tui_stable_secs``) before
  declaring the TUI ready.
* The prompt can still be swallowed on a slow start. ``verify_prompt_delivery``
  confirms the agent actually received it and resends if not (enabled via
  ``prov.verify_prompt_delivery``).
"""

from __future__ import annotations

import time
from math import ceil

from tcd.event_log import emit

# Substrings that indicate a directory/trust confirmation dialog. "Do you
# trust" matches Codex's current "Do you trust the contents of this directory?"
# as well as the older "Do you trust the files in this folder" wording; the
# default-highlighted option is "Yes" so a bare Enter confirms.
TRUST_PHRASES = (
    "Do you trust",
    "Yes, I trust this folder",
    "Enter to confirm",
)

# Transient pane substrings that prove a turn is actively running.  Never add
# persistent status-bar text here: Codex renders ``tokens used`` even while a
# pasted prompt is still waiting in the composer.
WORKING_MARKERS = ("esc to interrupt", "esc to cancel", "working (")

# Persistent status-bar text is useful for UI diagnostics, but cannot prove a
# prompt was submitted.  Kept separately to make that distinction explicit.
STATUS_BAR_MARKERS = ("tokens used",)


def wait_for_tui(
    tmux,
    session: str,
    prov,
    *,
    timeout_secs: float = 90.0,
) -> tuple[bool, int, bool]:
    """Wait for the agent TUI to be ready, handling trust dialogs.

    Returns ``(tui_ready, elapsed_ms, trust_handled)``.
    """
    indicator = getattr(prov, "tui_ready_indicator", None)
    stable_secs = getattr(prov, "tui_stable_secs", 0.0) or 0.0
    trust_handled = False
    tui_ready = False
    wait_started = time.time()
    prev_pane: str | None = None
    stable_since: float | None = None

    # Keep the default startup window unchanged while letting doctor enforce
    # an independent budget for every provider it probes.
    deadline = wait_started + max(0.0, timeout_secs)
    while time.time() < deadline:
        time.sleep(min(0.5, max(0.0, deadline - time.time())))
        # Visible screen only (-S 0). Capturing full scrollback would keep
        # matching a *dismissed* trust dialog that lingers in history, so the
        # loop would re-confirm it forever and never become ready.
        pane = tmux.capture_pane(session, start_line="0")
        if pane is None:
            continue

        if any(phrase in pane for phrase in TRUST_PHRASES):
            tmux.send_enter(session)
            trust_handled = True
            stable_since = None
            prev_pane = None
            time.sleep(min(2.0, max(0.0, deadline - time.time())))
            continue

        if trust_handled and "restarting" in pane.lower():
            stable_since = None
            prev_pane = None
            time.sleep(min(1.0, max(0.0, deadline - time.time())))
            continue

        if indicator and indicator in pane:
            if stable_secs <= 0:
                if trust_handled:
                    time.sleep(1)
                tui_ready = True
                break
            # Stability gating: only ready once the pane stops changing.
            if pane == prev_pane:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= stable_secs:
                    tui_ready = True
                    break
            else:
                stable_since = None
            prev_pane = pane
    else:
        # Fallback: wait a bit and proceed anyway.
        time.sleep(min(2.0, max(0.0, deadline - time.time())))

    elapsed_ms = int((time.time() - wait_started) * 1000)
    return tui_ready, elapsed_ms, trust_handled


# How many bare Enters to try when the prompt is visibly pasted but no turn
# is running. Cheap and side-effect free: an extra Enter on an empty composer
# is a no-op, whereas a resent prompt duplicates text.
ENTER_RETRIES = 3


def delivery_timeout_secs(sent_text: str) -> float:
    """Return the observation budget: 4s + 1s/KiB, capped at 20s."""
    return min(20.0, 4.0 + len(sent_text.encode("utf-8")) / 1024.0)


def _composer_has_text(
    pane: str,
    tui_ready_indicator: str | None,
    composer_placeholder: str | tuple[str, ...] | None,
) -> bool:
    """Whether the last composer line contains non-placeholder text."""
    if not tui_ready_indicator:
        return False
    composer_line: str | None = None
    for line in pane.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(tui_ready_indicator):
            composer_line = stripped[len(tui_ready_indicator):].strip()
    if not composer_line:
        return False
    placeholders = (
        composer_placeholder
        if isinstance(composer_placeholder, tuple)
        else (composer_placeholder,) if composer_placeholder else ()
    )
    lowered = composer_line.casefold()
    return not any(lowered.startswith(item.casefold()) for item in placeholders)


def observe_delivery(
    tmux,
    session: str,
    *,
    markers: tuple[str, ...] = WORKING_MARKERS,
    tui_ready_indicator: str | None = None,
    composer_placeholder: str | tuple[str, ...] | None = None,
    timeout_secs: float = 4.0,
) -> str:
    """Return ``running``, ``pasted`` or ``absent`` after watching the pane.

    Composer text is checked before activity markers on every capture.  A pane
    can contain a stale marker from the previous turn while the new message is
    visibly stuck in the input box.
    """
    timeout_secs = max(0.0, timeout_secs)
    deadline = time.time() + timeout_secs
    rounds = max(1, ceil(timeout_secs / 0.5))
    lowered_markers = tuple(marker.casefold() for marker in markers)
    for _ in range(rounds):
        remaining = max(0.0, deadline - time.time())
        if timeout_secs and remaining <= 0:
            break
        if timeout_secs:
            time.sleep(min(0.5, remaining))
        pane = tmux.capture_pane(session, start_line="0")
        if pane is None:
            continue
        if _composer_has_text(pane, tui_ready_indicator, composer_placeholder):
            return "pasted"
        lowered_pane = pane.casefold()
        if any(marker in lowered_pane for marker in lowered_markers):
            return "running"
    return "absent"


def _verify_delivery(
    tmux,
    session: str,
    job_id: str,
    sent_text: str,
    *,
    event_subject: str,
    max_resends: int,
    markers: tuple[str, ...],
    tui_ready_indicator: str | None,
    composer_placeholder: str | tuple[str, ...] | None,
    timeout_secs: float | None,
    event_data: dict | None = None,
) -> bool:
    """Shared bounded Enter/re-send state machine for start and send."""
    budget = delivery_timeout_secs(sent_text) if timeout_secs is None else timeout_secs
    extra = dict(event_data or {})
    state = observe_delivery(
        tmux,
        session,
        markers=markers,
        tui_ready_indicator=tui_ready_indicator,
        composer_placeholder=composer_placeholder,
        timeout_secs=budget,
    )
    via = "first"
    enter_attempt = 0
    resend_attempt = 0

    while True:
        if state == "running":
            attempt = enter_attempt if via == "enter" else resend_attempt
            emit(
                job_id,
                f"job.{event_subject}_confirmed",
                attempt=attempt,
                via=via,
                **extra,
            )
            return True

        if state == "pasted":
            if enter_attempt >= ENTER_RETRIES:
                break
            enter_attempt += 1
            emit(
                job_id,
                f"job.{event_subject}_enter_retry",
                attempt=enter_attempt,
                **extra,
            )
            tmux.send_enter(session)
            via = "enter"
        else:  # absent: the only state in which resending is safe
            if resend_attempt >= max_resends:
                break
            resend_attempt += 1
            emit(
                job_id,
                f"job.{event_subject}_resend",
                attempt=resend_attempt,
                **extra,
            )
            tmux.send_text(session, sent_text)
            via = "resend"

        state = observe_delivery(
            tmux,
            session,
            markers=markers,
            tui_ready_indicator=tui_ready_indicator,
            composer_placeholder=composer_placeholder,
            timeout_secs=budget,
        )

    emit(job_id, f"job.{event_subject}_unconfirmed", **extra)
    return False


def verify_prompt_delivery(
    tmux,
    session: str,
    job_id: str,
    sent_text: str,
    *,
    retries: int = 2,
    markers: tuple[str, ...] = WORKING_MARKERS,
    tui_ready_indicator: str | None = None,
    composer_placeholder: str | tuple[str, ...] | None = None,
    timeout_secs: float | None = None,
) -> bool:
    """Confirm the agent actually started the turn; recover if it did not.

    Delivery has two distinct failure modes and they need different cures:

    * **absent** — nothing landed; the keystrokes were swallowed during TUI
      init. Cure: resend the whole prompt.
    * **pasted-but-not-submitted** — the prompt sits in the composer (its text
      is visible in the pane) but no turn is running, because the trailing
      Enter was eaten while the TUI was still digesting a bracketed paste.
      Cure: send a bare Enter. Resending the whole prompt here would duplicate
      the text inside the composer, which is how this used to fail silently
      (2026-09-07: Codex 0.153.4, prompt visible, turn_count stuck at 0 for
      46 min, tcd reporting delivery as confirmed).

    Only a provider *working marker* proves a turn is running; echoed prompt
    text proves nothing but that the paste landed.

    *markers* must match the provider's own TUI (``prov.working_markers``).
    Markers that never match turn this guard into a duplicate-submission bug:
    a turn that is running fine looks dropped, so the prompt is re-sent and the
    task runs more than once.
    """
    return _verify_delivery(
        tmux,
        session,
        job_id,
        sent_text,
        event_subject="prompt",
        max_resends=max(0, retries),
        markers=markers,
        tui_ready_indicator=tui_ready_indicator,
        composer_placeholder=composer_placeholder,
        timeout_secs=timeout_secs,
    )


def verify_message_delivery(
    tmux,
    session: str,
    job_id: str,
    sent_text: str,
    *,
    markers: tuple[str, ...] = WORKING_MARKERS,
    tui_ready_indicator: str | None = None,
    composer_placeholder: str | tuple[str, ...] | None = None,
    timeout_secs: float | None = None,
    req_id: str | None = None,
    turn: int | None = None,
) -> bool:
    """Confirm a follow-up, allowing at most one whole-message resend."""
    event_data = {}
    if req_id is not None:
        event_data["req_id"] = req_id
    if turn is not None:
        event_data["turn"] = turn
    return _verify_delivery(
        tmux,
        session,
        job_id,
        sent_text,
        event_subject="message",
        max_resends=1,
        markers=markers,
        tui_ready_indicator=tui_ready_indicator,
        composer_placeholder=composer_placeholder,
        timeout_secs=timeout_secs,
        event_data=event_data,
    )
