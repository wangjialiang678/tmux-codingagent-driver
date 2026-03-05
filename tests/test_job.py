"""Tests for Job dataclass and JobManager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcd.config import JOBS_DIR, job_json_path, job_log_path, job_signal_path
from tcd.job import Job, JobManager


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    """Use a temporary directory for jobs."""
    monkeypatch.setattr("tcd.config.TCD_HOME", tmp_path)
    monkeypatch.setattr("tcd.config.JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr("tcd.job.JOBS_DIR", tmp_path / "jobs")
    # Patch path functions
    monkeypatch.setattr("tcd.job.job_json_path", lambda jid: tmp_path / "jobs" / f"{jid}.json")
    monkeypatch.setattr("tcd.job.job_log_path", lambda jid: tmp_path / "jobs" / f"{jid}.log")
    monkeypatch.setattr("tcd.job.job_prompt_path", lambda jid: tmp_path / "jobs" / f"{jid}.prompt")
    monkeypatch.setattr("tcd.job.job_signal_path", lambda jid: tmp_path / "jobs" / f"{jid}.turn-complete")
    return JobManager()


class TestJobDataclass:
    def test_roundtrip(self):
        job = Job(
            id="abcd1234",
            provider="codex",
            status="pending",
            prompt="hello",
            cwd="/tmp",
            tmux_session="tcd-codex-abcd1234",
        )
        d = job.to_dict()
        j2 = Job.from_dict(d)
        assert j2.id == "abcd1234"
        assert j2.provider == "codex"
        assert j2.status == "pending"

    def test_json_roundtrip(self):
        job = Job(
            id="abcd1234",
            provider="codex",
            status="running",
            prompt="do stuff",
            cwd="/tmp",
            tmux_session="tcd-codex-abcd1234",
            turn_count=2,
        )
        text = job.to_json()
        parsed = json.loads(text)
        assert parsed["id"] == "abcd1234"
        assert parsed["turn_count"] == 2
        j2 = Job.from_json(text)
        assert j2.turn_count == 2

    def test_total_tokens_default(self):
        job = Job(
            id="x", provider="codex", status="pending",
            prompt="y", cwd="/tmp", tmux_session="s",
        )
        assert job.total_tokens == {"input": 0, "output": 0}

    def test_total_tokens_roundtrip(self):
        job = Job(
            id="x", provider="codex", status="pending",
            prompt="y", cwd="/tmp", tmux_session="s",
            total_tokens={"input": 5000, "output": 3000},
        )
        text = job.to_json()
        j2 = Job.from_json(text)
        assert j2.total_tokens == {"input": 5000, "output": 3000}

    def test_from_dict_ignores_unknown_keys(self):
        d = {
            "id": "x",
            "provider": "codex",
            "status": "pending",
            "prompt": "y",
            "cwd": "/tmp",
            "tmux_session": "s",
            "unknown_field": 42,
        }
        job = Job.from_dict(d)
        assert job.id == "x"


class TestJobManager:
    def test_create_and_load(self, manager: JobManager):
        job = manager.create_job("codex", "test prompt", "/tmp")
        assert len(job.id) == 8
        assert job.provider == "codex"
        assert job.status == "pending"
        assert job.tmux_session.startswith("tcd-codex-")

        loaded = manager.load_job(job.id)
        assert loaded is not None
        assert loaded.id == job.id
        assert loaded.prompt == "test prompt"

    def test_load_nonexistent(self, manager: JobManager):
        assert manager.load_job("nonexistent") is None

    def test_save_atomic(self, manager: JobManager):
        job = manager.create_job("codex", "p", "/tmp")
        job.status = "running"
        job.turn_count = 3
        manager.save_job(job)
        loaded = manager.load_job(job.id)
        assert loaded is not None
        assert loaded.status == "running"
        assert loaded.turn_count == 3

    def test_list_jobs(self, manager: JobManager):
        manager.create_job("codex", "p1", "/tmp")
        manager.create_job("codex", "p2", "/tmp")
        jobs = manager.list_jobs()
        assert len(jobs) == 2

    def test_list_jobs_filter(self, manager: JobManager):
        j1 = manager.create_job("codex", "p1", "/tmp")
        j2 = manager.create_job("codex", "p2", "/tmp")
        j1.status = "completed"
        manager.save_job(j1)
        assert len(manager.list_jobs(status_filter="completed")) == 1
        assert len(manager.list_jobs(status_filter="pending")) == 1

    def test_clean_jobs(self, manager: JobManager):
        j1 = manager.create_job("codex", "p1", "/tmp")
        j2 = manager.create_job("codex", "p2", "/tmp")
        j1.status = "completed"
        manager.save_job(j1)
        cleaned = manager.clean_jobs()
        assert cleaned == 1
        assert manager.load_job(j1.id) is None
        assert manager.load_job(j2.id) is not None

    def test_clean_all(self, manager: JobManager):
        manager.create_job("codex", "p1", "/tmp")
        manager.create_job("codex", "p2", "/tmp")
        cleaned = manager.clean_jobs(include_running=True)
        assert cleaned == 2
