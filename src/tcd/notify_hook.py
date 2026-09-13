"""Codex notify hook script.

Called by Codex CLI when an agent turn completes:
    python3 notify_hook.py <job_id> <json_payload>

Writes a signal file and updates the job JSON.
"""

from __future__ import annotations

import json
import logging
import os
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


def _last_message_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.last-message.md"


def _notify_log_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.notify.jsonl"


def _codex_sessions_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    return (Path(codex_home) if codex_home else Path.home() / ".codex") / "sessions"


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace *path* atomically so readers never see a partial message."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _append_raw_payload(job_id: str, raw_payload: str) -> None:
    """Append the exact callback payload for later wire-format diagnosis."""
    path = _notify_log_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(raw_payload)
        if not raw_payload.endswith("\n"):
            f.write("\n")


def _thread_metadata(thread_id: str) -> dict | None:
    """Read the session metadata for *thread_id* from its Codex rollout.

    Codex 0.153.4's legacy notify payload exposes ``thread-id`` but not the
    parent/session id.  The rollout named with that thread id contains the
    missing ``thread_source`` and ``source.subagent.thread_spawn`` metadata.
    """
    if not thread_id:
        return None
    try:
        matches = _codex_sessions_dir().rglob(f"rollout-*-{thread_id}.jsonl")
        for path in matches:
            with path.open(encoding="utf-8", errors="replace") as f:
                for _ in range(20):
                    line = f.readline()
                    if not line:
                        break
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") != "session_meta":
                        continue
                    metadata = entry.get("payload")
                    if isinstance(metadata, dict) and metadata.get("id") == thread_id:
                        return metadata
    except OSError:
        logger.debug("Could not inspect Codex rollout for thread %s", thread_id)
    return None


def _is_subagent_payload(payload: dict) -> bool | None:
    """Classify a notify callback, returning None when identity is unknown."""
    thread_id = payload.get("thread-id") or payload.get("thread_id")
    session_id = payload.get("session-id") or payload.get("session_id")
    parent_id = payload.get("parent-thread-id") or payload.get("parent_thread_id")
    thread_source = payload.get("thread-source") or payload.get("thread_source")

    if parent_id:
        return True
    if thread_source:
        return thread_source == "subagent"
    if thread_id and session_id:
        return thread_id != session_id

    metadata = _thread_metadata(str(thread_id or ""))
    if metadata is None:
        return None
    if metadata.get("thread_source") == "subagent":
        return True
    source = metadata.get("source")
    if isinstance(source, dict) and isinstance(source.get("subagent"), dict):
        return True
    if metadata.get("id") and metadata.get("session_id"):
        return metadata["id"] != metadata["session_id"]
    if metadata.get("thread_source") == "user":
        return False
    return None


def _is_title_generation(message: str) -> bool:
    """True for Codex's title-generation turn: a bare JSON object whose only key is ``title``."""
    text = (message or "").strip()
    if not text.startswith("{") or len(text) > 400:
        return False
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(data, dict) and set(data.keys()) == {"title"}


def handle_notify(job_id: str, raw_payload: str) -> None:
    """Process a Codex notify-hook callback."""
    try:
        _append_raw_payload(job_id, raw_payload)
    except OSError:
        logger.warning("Could not append raw notify payload for job %s", job_id)

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
    full_last_msg = payload.get("last-assistant-message", "")
    is_subagent = _is_subagent_payload(payload)
    if is_subagent:
        _update_subagent_turns(job_id)
        logger.info(
            "Notify hook: job %s subagent turn ignored (thread_id=%s, turn_id=%s)",
            job_id,
            payload.get("thread-id", ""),
            turn_id,
        )
        return
    if is_subagent is None and payload.get("thread-id"):
        logger.warning(
            "Notify hook: job %s could not classify thread %s; preserving legacy root-turn behavior",
            job_id,
            payload.get("thread-id"),
        )
    if _is_title_generation(full_last_msg):
        # Codex generates a session title in a separate, asynchronous agent turn whose
        # only output is {"title": "..."}. It can complete *after* the real reply and
        # would otherwise overwrite the last message and bump turn_count (2026-09-09:
        # watcher saw turn 2 = title JSON, real reply lost). Record it, count nothing.
        try:
            from tcd.event_log import emit
            emit(job_id, "job.title_generated", turn_id=turn_id)
        except Exception:
            logger.debug("title event not recorded for job %s", job_id)
        logger.info("Notify hook: job %s title-generation turn ignored", job_id)
        return
    last_msg = full_last_msg[:500] if full_last_msg else ""

    # Keep the historical 500-character fields below for compatibility, and
    # place the authoritative full response beside the job record.
    full_message_path = _last_message_path(job_id)
    _atomic_write_text(full_message_path, full_last_msg)

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
    _update_job(job_id, turn_id, last_msg, timestamp, full_message_path)

    logger.info("Notify hook: job %s turn complete (turn_id=%s)", job_id, turn_id)


def _update_job(
    job_id: str,
    turn_id: str,
    last_msg: str,
    timestamp: str,
    full_message_path: Path,
) -> None:
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
        data["last_agent_message_path"] = str(full_message_path)
        # Atomic write
        import os
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=job_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, job_path)
    except Exception as exc:
        logger.error("Failed to update job %s: %s", job_id, exc)


def _update_subagent_turns(job_id: str) -> None:
    """Count a child completion without changing the root turn's state."""
    job_path = _job_json_path(job_id)
    if not job_path.exists():
        logger.warning("Job file not found: %s", job_path)
        return

    try:
        data = json.loads(job_path.read_text())
        data["subagent_turns"] = data.get("subagent_turns", 0) + 1
        _atomic_write_text(job_path, json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as exc:
        logger.error("Failed to update subagent count for job %s: %s", job_id, exc)


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
