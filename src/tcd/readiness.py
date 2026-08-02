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

# Pane substrings that prove a turn is running (prompt was received).
WORKING_MARKERS = ("esc to interrupt", "tokens used", "working (")


def wait_for_tui(tmux, session: str, prov) -> tuple[bool, int, bool]:
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

    # 0.5s per iteration; allow up to 90s for slow MCP startup.
    for _ in range(180):
        time.sleep(0.5)
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
            time.sleep(2)
            continue

        if trust_handled and "restarting" in pane.lower():
            stable_since = None
            prev_pane = None
            time.sleep(1)
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
        time.sleep(2)

    elapsed_ms = int((time.time() - wait_started) * 1000)
    return tui_ready, elapsed_ms, trust_handled


def verify_prompt_delivery(
    tmux,
    session: str,
    job_id: str,
    sent_text: str,
    *,
    retries: int = 2,
    markers: tuple[str, ...] = WORKING_MARKERS,
) -> bool:
    """Confirm the agent received the prompt; resend if it was dropped.

    Looks for evidence in the pane that the prompt landed: a snippet of the
    prompt echoed back, or a 'turn running' marker. If neither shows up within
    a few seconds the keystrokes were likely swallowed during TUI init — resend
    (up to ``retries`` times). Returns whether delivery was confirmed.

    *markers* must match the provider's own TUI (``prov.working_markers``).
    Markers that never match turn this guard into a duplicate-submission bug:
    a turn that is running fine looks dropped, so the prompt is re-sent and the
    task runs more than once.
    """
    snippet = ""
    for line in sent_text.splitlines():
        line = line.strip()
        if line:
            snippet = line[:16]
            break

    def landed() -> bool:
        for _ in range(8):  # ~4s of observation
            time.sleep(0.5)
            pane = tmux.capture_pane(session, start_line="0")
            if pane is None:
                continue
            pane_low = pane.lower()
            if snippet and snippet in pane:
                return True
            if any(m in pane_low for m in markers):
                return True
        return False

    for attempt in range(retries + 1):
        if landed():
            if attempt:
                emit(job_id, "job.prompt_confirmed", attempt=attempt)
            return True
        if attempt < retries:
            emit(job_id, "job.prompt_resend", attempt=attempt + 1)
            tmux.send_text(session, sent_text)

    emit(job_id, "job.prompt_unconfirmed")
    return False
