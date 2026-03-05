"""Tests for provider base class and registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from tcd.provider import (
    CompletionResult,
    Provider,
    _registry,
    get_provider,
    list_providers,
    register_provider,
)


# A concrete test provider
class _DummyProvider(Provider):
    name = "dummy"
    cli_command = "dummy-cli"

    def build_launch_command(self, job):
        return "dummy-cli run"

    def build_prompt_wrapper(self, message, req_id):
        return message

    def detect_completion(self, job):
        return CompletionResult(state="idle")

    def parse_response(self, job):
        return "dummy response"

    def get_session_log_path(self, job):
        return None


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure dummy provider is registered and cleaned up."""
    _registry.pop("dummy", None)
    register_provider(_DummyProvider)
    yield
    _registry.pop("dummy", None)


def test_register_and_get():
    p = get_provider("dummy")
    assert isinstance(p, _DummyProvider)
    assert p.name == "dummy"


def test_get_unknown_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("nonexistent")


def test_list_providers():
    names = list_providers()
    assert "dummy" in names


def test_completion_result():
    r = CompletionResult(state="idle", last_agent_message="done")
    assert r.state == "idle"
    assert r.last_agent_message == "done"
