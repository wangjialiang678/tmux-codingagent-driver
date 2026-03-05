"""Idle detection: consecutive capture-pane snapshots with no change."""

from __future__ import annotations

import time

from tcd.tmux_adapter import TmuxAdapter


class IdleDetector:
    """Detect when a tmux session has stopped producing output.

    Compares capture-pane snapshots at *poll_interval* intervals.
    If the content stays the same for *idle_threshold* seconds,
    the session is considered idle.
    """

    def __init__(
        self,
        tmux: TmuxAdapter | None = None,
        idle_threshold: float = 20.0,
        poll_interval: float = 2.0,
    ) -> None:
        self._tmux = tmux or TmuxAdapter()
        self.idle_threshold = idle_threshold
        self.poll_interval = poll_interval

    def is_idle(self, session: str) -> bool:
        """Non-blocking single check: take two snapshots and compare.

        Returns True only if the pane content has not changed for
        *idle_threshold* seconds.  This method performs a quick two-sample
        comparison (one snapshot now, sleep *poll_interval*, another snapshot)
        and checks timestamps.
        """
        # Take first snapshot
        snap1 = self._capture(session)
        if snap1 is None:
            return False  # session gone, not "idle"

        time.sleep(self.poll_interval)

        snap2 = self._capture(session)
        if snap2 is None:
            return False

        return snap1 == snap2

    def wait_for_idle(
        self,
        session: str,
        timeout: float = 300.0,
    ) -> bool:
        """Block until the session is idle or *timeout* expires.

        Returns True if idle was detected, False on timeout.
        """
        deadline = time.time() + timeout
        last_content: str | None = None
        stable_since: float | None = None

        while time.time() < deadline:
            current = self._capture(session)
            if current is None:
                return False  # session gone

            if current == last_content:
                if stable_since is None:
                    stable_since = time.time() - self.poll_interval
                elapsed = time.time() - stable_since
                if elapsed >= self.idle_threshold:
                    return True
            else:
                last_content = current
                stable_since = None

            time.sleep(self.poll_interval)

        return False  # timeout

    def _capture(self, session: str) -> str | None:
        return self._tmux.capture_pane(session)
