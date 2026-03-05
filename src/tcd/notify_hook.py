"""Codex notify hook script.

Called by Codex CLI when an agent turn completes:
    python3 notify_hook.py <job_id> <json_payload>

Writes a signal file and updates the job JSON.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Direct imports to keep this script self-contained
# (it's invoked by Codex as a subprocess, not via tcd)

logger = logging.getLogger(__name__)


def _tcd_home() -> Path:
    return Path.home() / ".tcd"


def _jobs_dir() -> Path:
    return _tcd_home() / "jobs"


def _signal_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.turn-complete"


def _job_json_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.json"


def handle_notify(job_id: str, raw_payload: str) -> None:
    """Process a Codex notify-hook callback."""
    try:
        payload = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid payload for job %s: %r", job_id, raw_payload)
        return

    event_type = payload.get("type", "")
    if event_type != "agent-turn-complete":
        logger.debug("Ignoring event type: %s", event_type)
        return

    timestamp = datetime.now(timezone.utc).isoformat()

    # Extract data from payload
    turn_id = payload.get("turn-id", "")
    last_msg = payload.get("last-assistant-message", "")
    if last_msg and len(last_msg) > 500:
        last_msg = last_msg[:500]

    # Write signal file
    signal_data = {
        "turnId": turn_id,
        "lastAgentMessage": last_msg,
        "timestamp": timestamp,
    }
    signal_path = _signal_path(job_id)
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(json.dumps(signal_data, ensure_ascii=False))

    # Update job.json
    _update_job(job_id, turn_id, last_msg, timestamp)

    logger.info("Notify hook: job %s turn complete (turn_id=%s)", job_id, turn_id)


def _update_job(job_id: str, turn_id: str, last_msg: str, timestamp: str) -> None:
    """Update the job JSON with turn completion info."""
    job_path = _job_json_path(job_id)
    if not job_path.exists():
        logger.warning("Job file not found: %s", job_path)
        return

    try:
        data = json.loads(job_path.read_text())
        data["turn_count"] = data.get("turn_count", 0) + 1
        data["turn_state"] = "idle"
        data["last_agent_message"] = last_msg or None
        # Atomic write
        import os
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=job_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, job_path)
    except Exception as exc:
        logger.error("Failed to update job %s: %s", job_id, exc)


def main() -> None:
    """Entry point when called by Codex."""
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <job_id> <json_payload>", file=sys.stderr)
        sys.exit(1)

    job_id = sys.argv[1]
    raw_payload = sys.argv[2]
    handle_notify(job_id, raw_payload)


if __name__ == "__main__":
    main()
