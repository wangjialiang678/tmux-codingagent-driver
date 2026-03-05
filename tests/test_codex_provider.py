"""Tests for CodexProvider."""

from __future__ import annotations

import json

import pytest

from tcd.job import Job
from tcd.providers.codex import CodexProvider


@pytest.fixture()
def provider():
    return CodexProvider()


@pytest.fixture()
def job():
    return Job(
        id="abcd1234",
        provider="codex",
        status="running",
        prompt="say hello",
        cwd="/tmp",
        tmux_session="tcd-codex-abcd1234",
    )


def test_name():
    p = CodexProvider()
    assert p.name == "codex"
    assert p.cli_command == "codex"


def test_build_launch_command(provider, job):
    cmd = provider.build_launch_command(job)
    assert "codex" in cmd
    assert "-a never" in cmd
    assert "notify_hook.py" in cmd
    assert job.id in cmd
    assert "script" in cmd
    assert "[tcd: session complete]" in cmd
    assert "read" in cmd


def test_build_launch_command_with_model(provider, job):
    job.model = "gpt-5"
    cmd = provider.build_launch_command(job)
    assert "gpt-5" in cmd


def test_build_launch_command_rejects_invalid_model(provider, job):
    job.model = 'gpt-5; rm -rf /'
    with pytest.raises(ValueError, match="Invalid model name"):
        provider.build_launch_command(job)


def test_build_prompt_wrapper_passthrough(provider):
    msg = "do something"
    assert provider.build_prompt_wrapper(msg, "req-1") == msg


def test_detect_completion_no_signal(provider, job, tmp_path, monkeypatch):
    monkeypatch.setattr("tcd.providers.codex.job_signal_path", lambda jid: tmp_path / f"{jid}.turn-complete")
    result = provider.detect_completion(job)
    assert result is None


def test_detect_completion_with_signal(provider, job, tmp_path, monkeypatch):
    signal_path = tmp_path / f"{job.id}.turn-complete"
    signal_data = {"turnId": "t1", "lastAgentMessage": "done", "timestamp": "2026-01-01T00:00:00Z"}
    signal_path.write_text(json.dumps(signal_data))
    monkeypatch.setattr("tcd.providers.codex.job_signal_path", lambda jid: signal_path)
    result = provider.detect_completion(job)
    assert result is not None
    assert result.state == "idle"
    assert result.last_agent_message == "done"


def test_detect_completion_with_tokens(provider, job, tmp_path, monkeypatch):
    """Phase 4: detect_completion extracts tokens from NDJSON session."""
    signal_path = tmp_path / f"{job.id}.turn-complete"
    signal_data = {"turnId": "t1", "lastAgentMessage": "done", "timestamp": "2026-01-01T00:00:00Z"}
    signal_path.write_text(json.dumps(signal_data))
    monkeypatch.setattr("tcd.providers.codex.job_signal_path", lambda jid: signal_path)

    from tcd.providers.codex import CodexOutput
    mock_output = CodexOutput(tokens={"input": 5000, "output": 3000})
    monkeypatch.setattr(provider, "parse_response_structured", lambda job: mock_output)

    result = provider.detect_completion(job)
    assert result is not None
    assert result.state == "idle"
    assert result.tokens == {"input": 5000, "output": 3000}


def test_detect_completion_no_tokens_available(provider, job, tmp_path, monkeypatch):
    """Phase 4: tokens is None when NDJSON session has no token data."""
    signal_path = tmp_path / f"{job.id}.turn-complete"
    signal_data = {"turnId": "t1", "lastAgentMessage": "done", "timestamp": "2026-01-01T00:00:00Z"}
    signal_path.write_text(json.dumps(signal_data))
    monkeypatch.setattr("tcd.providers.codex.job_signal_path", lambda jid: signal_path)
    monkeypatch.setattr(provider, "parse_response_structured", lambda job: None)

    result = provider.detect_completion(job)
    assert result is not None
    assert result.tokens is None


def test_registered():
    from tcd.provider import get_provider
    p = get_provider("codex")
    assert isinstance(p, CodexProvider)
