"""Small recovery hooks for prompt submission edge cases."""

from __future__ import annotations

import logging
import time
from typing import Any

from tcd.event_log import emit
from tcd.job import Job

logger = logging.getLogger(__name__)

QUEUED_RETRY_DELAY = 0.3


def retry_queued_message_submission(
    tmux: Any,
    job: Job,
    provider: Any,
    req_id: str,
    *,
    delay: float = QUEUED_RETRY_DELAY,
) -> bool:
    """Send one extra Enter when a provider reports the follow-up is queued.

    Claude Code can occasionally accept pasted text but leave it in its queued
    input state after the first Enter. Providers opt in by exposing
    ``has_queued_message_notice(pane)``.
    """
    if not hasattr(type(provider), "has_queued_message_notice"):
        return False
    detector = provider.has_queued_message_notice

    if delay > 0:
        time.sleep(delay)

    try:
        pane = tmux.capture_pane(job.tmux_session, start_line="-20") or ""
    except Exception:
        logger.warning("send %s: failed to inspect pane for queued message", job.id, exc_info=True)
        return False

    try:
        queued = bool(detector(pane))
    except Exception:
        logger.warning("send %s: queued-message detector failed", job.id, exc_info=True)
        return False

    if not queued:
        return False

    success = bool(tmux.send_enter(job.tmux_session))
    emit(
        job.id,
        "job.message_submit_retry",
        reason="queued_message_notice",
        success=success,
        req_id=req_id,
        turn=job.turn_count,
    )
    if success:
        logger.info("send %s: queued follow-up detected; sent extra Enter", job.id)
    else:
        logger.warning("send %s: queued follow-up detected but extra Enter failed", job.id)
    return success
