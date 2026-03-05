"""Tests for rule-based diagnostics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tcd.diagnostics import Warning, _elapsed_seconds, _time_diff, diagnose
from tcd.job import Job


def _job(**overrides) -> Job:
    data = {
        "id": "job123",
        "provider": "codex",
        "status": "running",
        "prompt": "Read project docs",
        "cwd": "/tmp",
        "tmux_session": "tcd-codex-job123",
        "sandbox": "danger-full-access",
        "turn_count": 1,
        "turn_state": "idle",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    data.update(overrides)
    return Job(**data)


def _no_events(monkeypatch) -> None:
    monkeypatch.setattr("tcd.diagnostics.load_events", lambda _job_id: [])


def test_warning_dataclass_fields():
    w = Warning(code="CODE", message="hello", severity="warn")
    assert w.code == "CODE"
    assert w.message == "hello"
    assert w.severity == "warn"


def test_time_diff_helper():
    t1 = "2026-03-05T00:00:00+00:00"
    t2 = "2026-03-05T00:01:30+00:00"
    assert _time_diff(t1, t2) == 90


def test_elapsed_seconds_helper():
    started = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
    elapsed = _elapsed_seconds(_job(started_at=started))
    assert 35 <= elapsed <= 55


def test_diagnose_empty_events_and_no_pane_returns_empty(monkeypatch):
    _no_events(monkeypatch)
    job = _job()
    assert diagnose(job, pane_tail=None) == []


def test_r1_sandbox_mismatch_triggers(monkeypatch):
    _no_events(monkeypatch)
    job = _job(sandbox=None, prompt="Please fix this bug")

    warnings = diagnose(job)
    assert any(w.code == "SANDBOX_MISMATCH" for w in warnings)


def test_r1_sandbox_mismatch_not_triggered(monkeypatch):
    _no_events(monkeypatch)
    job = _job(sandbox="danger-full-access", prompt="Please fix this bug")

    warnings = diagnose(job)
    assert not any(w.code == "SANDBOX_MISMATCH" for w in warnings)


def test_r2_stall_triggers(monkeypatch):
    events = [
        {"event": "job.checked", "state": "working", "ts": "2026-03-05T00:00:00+00:00"},
        {"event": "job.checked", "state": "working", "ts": "2026-03-05T00:00:30+00:00"},
        {"event": "job.checked", "state": "working", "ts": "2026-03-05T00:01:05+00:00"},
        {"event": "job.checked", "state": "working", "ts": "2026-03-05T00:01:20+00:00"},
    ]
    monkeypatch.setattr("tcd.diagnostics.load_events", lambda _job_id: events)

    warnings = diagnose(_job())
    assert any(w.code == "STALL" for w in warnings)


def test_r2_stall_not_triggered(monkeypatch):
    events = [
        {"event": "job.checked", "state": "working", "ts": "2026-03-05T00:00:00+00:00"},
        {"event": "job.checked", "state": "working", "ts": "2026-03-05T00:00:10+00:00"},
        {"event": "job.checked", "state": "working", "ts": "2026-03-05T00:00:20+00:00"},
        {"event": "job.checked", "state": "working", "ts": "2026-03-05T00:00:30+00:00"},
    ]
    monkeypatch.setattr("tcd.diagnostics.load_events", lambda _job_id: events)

    warnings = diagnose(_job())
    assert not any(w.code == "STALL" for w in warnings)


def test_r3_permission_error_triggers(monkeypatch):
    _no_events(monkeypatch)
    pane_tail = "line1\nPermission denied\nline3"

    warnings = diagnose(_job(), pane_tail=pane_tail)
    assert any(w.code == "PERMISSION_ERROR" and w.severity == "error" for w in warnings)


def test_r3_permission_error_not_triggered(monkeypatch):
    _no_events(monkeypatch)
    pane_tail = "all good\nno failures"

    warnings = diagnose(_job(), pane_tail=pane_tail)
    assert not any(w.code == "PERMISSION_ERROR" for w in warnings)


def test_r4_turn0_stuck_triggers(monkeypatch):
    _no_events(monkeypatch)
    started = (datetime.now(timezone.utc) - timedelta(seconds=130)).isoformat()
    job = _job(turn_count=0, turn_state="working", started_at=started)

    warnings = diagnose(job)
    assert any(w.code == "TURN0_STUCK" for w in warnings)


def test_r4_turn0_stuck_not_triggered(monkeypatch):
    _no_events(monkeypatch)
    started = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    job = _job(turn_count=0, turn_state="working", started_at=started)

    warnings = diagnose(job)
    assert not any(w.code == "TURN0_STUCK" for w in warnings)


def test_diagnose_never_raises_on_internal_error(monkeypatch):
    monkeypatch.setattr("tcd.diagnostics.load_events", lambda _job_id: (_ for _ in ()).throw(RuntimeError("boom")))
    warnings = diagnose(_job())
    assert warnings == []
