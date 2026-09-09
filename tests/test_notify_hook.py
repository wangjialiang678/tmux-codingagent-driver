"""Tests for notify_hook."""

from __future__ import annotations

import json

from tcd.notify_hook import handle_notify


def test_handle_agent_turn_complete(tmp_path, monkeypatch):
    monkeypatch.setattr("tcd.notify_hook._jobs_dir", lambda: tmp_path)

    # Create a fake job.json
    job_id = "test1234"
    job_data = {"id": job_id, "turn_count": 0, "turn_state": "working"}
    (tmp_path / f"{job_id}.json").write_text(json.dumps(job_data))

    payload = json.dumps({
        "type": "agent-turn-complete",
        "turn-id": "turn-42",
        "last-assistant-message": "I finished the task.",
    })
    handle_notify(job_id, payload)

    # Signal file should exist
    signal_path = tmp_path / f"{job_id}.turn-complete"
    assert signal_path.exists()
    signal = json.loads(signal_path.read_text())
    assert signal["turnId"] == "turn-42"
    assert signal["lastAgentMessage"] == "I finished the task."

    # Job should be updated
    updated = json.loads((tmp_path / f"{job_id}.json").read_text())
    assert updated["turn_count"] == 1
    assert updated["turn_state"] == "idle"


def test_handle_ignores_other_event_types(tmp_path, monkeypatch):
    monkeypatch.setattr("tcd.notify_hook._jobs_dir", lambda: tmp_path)
    job_id = "test5678"

    payload = json.dumps({"type": "some-other-event"})
    handle_notify(job_id, payload)

    # No signal file
    assert not (tmp_path / f"{job_id}.turn-complete").exists()


def test_handle_invalid_payload(tmp_path, monkeypatch):
    monkeypatch.setattr("tcd.notify_hook._jobs_dir", lambda: tmp_path)
    # Should not crash
    handle_notify("xyz", "not-json")


def test_handle_truncates_long_message(tmp_path, monkeypatch):
    monkeypatch.setattr("tcd.notify_hook._jobs_dir", lambda: tmp_path)
    job_id = "trunc123"
    (tmp_path / f"{job_id}.json").write_text(json.dumps({"id": job_id, "turn_count": 0}))

    long_msg = "A" * 1000
    payload = json.dumps({
        "type": "agent-turn-complete",
        "turn-id": "t1",
        "last-assistant-message": long_msg,
    })
    handle_notify(job_id, payload)

    signal = json.loads((tmp_path / f"{job_id}.turn-complete").read_text())
    assert len(signal["lastAgentMessage"]) == 500
    full_path = tmp_path / f"{job_id}.last-message.md"
    assert full_path.read_text() == long_msg
    updated = json.loads((tmp_path / f"{job_id}.json").read_text())
    assert updated["last_agent_message"] == long_msg[:500]
    assert updated["last_agent_message_path"] == str(full_path)


def test_title_generation_turn_is_ignored(tmp_path, monkeypatch):
    """Codex's async title turn ({"title": ...}) must not bump turn_count or overwrite the last message."""
    import json as _json
    from tcd import notify_hook as nh
    monkeypatch.setattr(nh, "_jobs_dir", lambda: tmp_path)
    job_id = "abc12345"
    (tmp_path / f"{job_id}.json").write_text(_json.dumps({"id": job_id, "turn_count": 1, "turn_state": "idle"}))
    (tmp_path / f"{job_id}.last-message.md").write_text("[DONE:X]\nreal reply\n[DONE:X]\n")
    nh.handle_notify(job_id, _json.dumps({"type": "agent-turn-complete", "turn-id": "t2", "last-assistant-message": '{"title":"验证监工脚本"}'}))
    data = _json.loads((tmp_path / f"{job_id}.json").read_text())
    assert data["turn_count"] == 1
    assert (tmp_path / f"{job_id}.last-message.md").read_text().startswith("[DONE:X]")
    assert not (tmp_path / f"{job_id}.turn-complete").exists()
