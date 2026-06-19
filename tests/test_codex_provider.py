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


def test_build_launch_command_disables_auto_update(provider, job):
    # The launch-time auto-updater would otherwise exit before the agent starts.
    assert "check_for_update_on_startup=false" in provider.build_launch_command(job)


def test_build_launch_command_pre_trusts_cwd(provider, job):
    cmd = provider.build_launch_command(job)
    assert "trust_level=" in cmd and "projects." in cmd


def _write_mcp_config(home):
    (home / "config.toml").write_text(
        '[mcp_servers.playwright]\ncommand = "npx"\n\n'
        '[mcp_servers.context7]\ncommand = "npx"\n\n'
        '[mcp_servers.node_repl]\ncommand = "node"\n\n'
        '[mcp_servers.node_repl.env]\nFOO = "bar"\n',
        encoding="utf-8",
    )


def test_build_launch_command_disables_mcp_servers(provider, job, tmp_path, monkeypatch):
    home = tmp_path / "codex_home"
    home.mkdir()
    _write_mcp_config(home)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("TCD_CODEX_MCP_KEEP", raising=False)  # use default keep-list
    cmd = provider.build_launch_command(job)
    assert "mcp_servers.playwright.enabled=false" in cmd
    assert "mcp_servers.node_repl.enabled=false" in cmd
    # context7 is on the default keep-list -> NOT disabled.
    assert "mcp_servers.context7.enabled=false" not in cmd
    # node_repl.env is a sub-table, not a separate server.
    assert "mcp_servers.env.enabled=false" not in cmd
    assert cmd.count("enabled=false") == 2


def test_mcp_keep_env_override(provider, job, tmp_path, monkeypatch):
    home = tmp_path / "codex_home"
    home.mkdir()
    _write_mcp_config(home)
    monkeypatch.setenv("CODEX_HOME", str(home))
    # Keep playwright instead of context7.
    monkeypatch.setenv("TCD_CODEX_MCP_KEEP", "playwright")
    cmd = provider.build_launch_command(job)
    assert "mcp_servers.playwright.enabled=false" not in cmd
    assert "mcp_servers.context7.enabled=false" in cmd
    # Empty keep-list disables everything.
    monkeypatch.setenv("TCD_CODEX_MCP_KEEP", "")
    cmd = provider.build_launch_command(job)
    assert cmd.count("enabled=false") == 3  # playwright, context7, node_repl


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
