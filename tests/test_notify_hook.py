"""Tests for notify_hook."""

from __future__ import annotations

import json

from tcd.notify_hook import handle_notify


def _write_rollout(
    sessions_dir, thread_id: str, *, session_id: str, thread_source: str, parent: str | None = None
):
    day = sessions_dir / "2026" / "09" / "14"
    day.mkdir(parents=True, exist_ok=True)
    source = "cli"
    if parent:
        source = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1}}}
    entry = {
        "timestamp": "2026-09-13T17:38:00Z",
        "type": "session_meta",
        "payload": {
            "id": thread_id,
            "session_id": session_id,
            "thread_source": thread_source,
            "source": source,
        },
    }
    path = day / f"rollout-2026-09-14T01-38-00-{thread_id}.jsonl"
    path.write_text(json.dumps(entry) + "\n")


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


def test_subagent_payload_only_updates_audit_and_counter(tmp_path, monkeypatch):
    """0.153.4 payload thread-id resolves to rollout subagent metadata."""
    jobs = tmp_path / "jobs"
    sessions = tmp_path / "codex" / "sessions"
    jobs.mkdir()
    monkeypatch.setattr("tcd.notify_hook._jobs_dir", lambda: jobs)
    monkeypatch.setattr("tcd.notify_hook._codex_sessions_dir", lambda: sessions)

    job_id = "fixture1"
    root_id = "root-thread-redacted"
    child_id = "child-thread-redacted"
    (jobs / f"{job_id}.json").write_text(json.dumps({
        "id": job_id,
        "turn_count": 0,
        "subagent_turns": 0,
        "turn_state": "working",
        "last_agent_message": None,
    }))
    _write_rollout(
        sessions,
        child_id,
        session_id=root_id,
        thread_source="subagent",
        parent=root_id,
    )
    payload = json.dumps({
        "type": "agent-turn-complete",
        "thread-id": child_id,
        "turn-id": "child-turn-redacted",
        "cwd": "/workspace/redacted",
        "client": "codex-tui",
        "input-messages": ["read-only audit"],
        "last-assistant-message": "child summary, not delivery",
    })

    handle_notify(job_id, payload)

    updated = json.loads((jobs / f"{job_id}.json").read_text())
    assert updated["turn_count"] == 0
    assert updated["subagent_turns"] == 1
    assert updated["turn_state"] == "working"
    assert updated["last_agent_message"] is None
    assert not (jobs / f"{job_id}.turn-complete").exists()
    assert not (jobs / f"{job_id}.last-message.md").exists()
    assert (jobs / f"{job_id}.notify.jsonl").read_text() == payload + "\n"


def test_root_payload_with_rollout_keeps_control_group_behavior(tmp_path, monkeypatch):
    """No-subagent jobs still record exactly one ordinary root completion."""
    jobs = tmp_path / "jobs"
    sessions = tmp_path / "codex" / "sessions"
    jobs.mkdir()
    monkeypatch.setattr("tcd.notify_hook._jobs_dir", lambda: jobs)
    monkeypatch.setattr("tcd.notify_hook._codex_sessions_dir", lambda: sessions)

    job_id = "control1"
    root_id = "root-control-redacted"
    (jobs / f"{job_id}.json").write_text(json.dumps({
        "id": job_id, "turn_count": 0, "turn_state": "working"
    }))
    _write_rollout(
        sessions, root_id, session_id=root_id, thread_source="user"
    )
    payload = json.dumps({
        "type": "agent-turn-complete",
        "thread-id": root_id,
        "turn-id": "root-turn-redacted",
        "last-assistant-message": "root delivery",
    })

    handle_notify(job_id, payload)

    updated = json.loads((jobs / f"{job_id}.json").read_text())
    assert updated["turn_count"] == 1
    assert updated.get("subagent_turns", 0) == 0
    assert updated["turn_state"] == "idle"
    assert updated["last_agent_message"] == "root delivery"


def test_replay_5865411a_timeline_has_no_intermediate_false_idle(tmp_path, monkeypatch):
    """Replay three child completions followed by the real root completion."""
    jobs = tmp_path / "jobs"
    sessions = tmp_path / "codex" / "sessions"
    jobs.mkdir()
    monkeypatch.setattr("tcd.notify_hook._jobs_dir", lambda: jobs)
    monkeypatch.setattr("tcd.notify_hook._codex_sessions_dir", lambda: sessions)

    job_id = "5865411a"
    root_id = "root-5865411a-redacted"
    children = [
        ("child-screens-redacted", "2026-09-13T17:41:50.281Z"),
        ("child-q3q4-redacted", "2026-09-13T17:42:13.702Z"),
        ("child-q1q6-redacted", "2026-09-13T17:42:15.149Z"),
    ]
    (jobs / f"{job_id}.json").write_text(json.dumps({
        "id": job_id,
        "turn_count": 0,
        "subagent_turns": 0,
        "turn_state": "working",
        "last_agent_message": None,
    }))
    _write_rollout(sessions, root_id, session_id=root_id, thread_source="user")

    for child_id, completed_at in children:
        _write_rollout(
            sessions,
            child_id,
            session_id=root_id,
            thread_source="subagent",
            parent=root_id,
        )
        handle_notify(job_id, json.dumps({
            "type": "agent-turn-complete",
            "thread-id": child_id,
            "turn-id": f"turn-{child_id}",
            "last-assistant-message": f"readonly summary at {completed_at}",
        }))
        intermediate = json.loads((jobs / f"{job_id}.json").read_text())
        assert intermediate["turn_count"] == 0
        assert intermediate["turn_state"] == "working"
        assert intermediate["last_agent_message"] is None
        assert not (jobs / f"{job_id}.turn-complete").exists()

    handle_notify(job_id, json.dumps({
        "type": "agent-turn-complete",
        "thread-id": root_id,
        "turn-id": "root-turn-complete-redacted",
        "last-assistant-message": "final root delivery",
    }))
    final = json.loads((jobs / f"{job_id}.json").read_text())
    assert final["turn_count"] == 1
    assert final["subagent_turns"] == 3
    assert final["turn_state"] == "idle"
    assert final["last_agent_message"] == "final root delivery"
    audit_lines = (jobs / f"{job_id}.notify.jsonl").read_text().splitlines()
    assert len(audit_lines) == 4
    assert all(json.loads(line)["type"] == "agent-turn-complete" for line in audit_lines)


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
