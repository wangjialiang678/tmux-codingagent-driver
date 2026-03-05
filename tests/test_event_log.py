"""Tests for job event logging."""

from __future__ import annotations

import pytest

from tcd.config import job_events_path as config_job_events_path
from tcd.event_log import emit, job_events_path, load_events
from tcd.job import JobManager


@pytest.fixture()
def tmp_jobs(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr("tcd.config.TCD_HOME", tmp_path)
    monkeypatch.setattr("tcd.config.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("tcd.job.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("tcd.job.job_json_path", lambda jid: jobs_dir / f"{jid}.json")
    monkeypatch.setattr("tcd.job.job_log_path", lambda jid: jobs_dir / f"{jid}.log")
    monkeypatch.setattr("tcd.job.job_prompt_path", lambda jid: jobs_dir / f"{jid}.prompt")
    monkeypatch.setattr("tcd.job.job_signal_path", lambda jid: jobs_dir / f"{jid}.turn-complete")
    monkeypatch.setattr("tcd.job.job_events_path", lambda jid: jobs_dir / f"{jid}.events.jsonl")
    return jobs_dir


def test_job_events_path(tmp_jobs):
    assert config_job_events_path("abc12345") == tmp_jobs / "abc12345.events.jsonl"
    assert job_events_path("abc12345") == tmp_jobs / "abc12345.events.jsonl"


def test_emit_and_load_events(tmp_jobs):
    emit("job1", "job.created", provider="codex")
    emit("job1", "job.checked", state="working")

    events = load_events("job1")
    assert len(events) == 2
    assert events[0]["event"] == "job.created"
    assert events[0]["provider"] == "codex"
    assert "ts" in events[0]
    assert events[1]["event"] == "job.checked"
    assert events[1]["state"] == "working"


def test_load_events_with_filter(tmp_jobs):
    emit("job2", "job.checked", state="working")
    emit("job2", "job.checked", state="idle")
    emit("job2", "job.message_sent", turn=1)

    checked = load_events("job2", event_filter="job.checked")
    assert len(checked) == 2
    assert all(e["event"] == "job.checked" for e in checked)


def test_emit_and_load_unicode(tmp_jobs):
    emit("job3", "job.message_sent", text="修复 bug ✅")

    events = load_events("job3")
    assert len(events) == 1
    assert events[0]["text"] == "修复 bug ✅"


def test_emit_never_raises(tmp_jobs, monkeypatch):
    bad_parent = tmp_jobs / "not-a-directory"
    bad_parent.write_text("x", encoding="utf-8")
    monkeypatch.setattr("tcd.event_log.job_events_path", lambda _: bad_parent / "job.events.jsonl")

    emit("job4", "job.created", provider="codex")


def test_clean_removes_event_file(tmp_jobs):
    mgr = JobManager()
    job = mgr.create_job("codex", "test prompt", "/tmp")
    emit(job.id, "job.created", provider="codex")
    assert job_events_path(job.id).exists()

    job.status = "completed"
    mgr.save_job(job)
    cleaned = mgr.clean_jobs()

    assert cleaned == 1
    assert not job_events_path(job.id).exists()
    assert mgr.load_job(job.id) is None
