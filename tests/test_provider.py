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


# ---------------------------------------------------------------------------
# Fatal provider-side errors
#
# A bad model id or an upstream outage leaves the CLI printing errors while
# tcd reports state="working" — burning the whole timeout on a job that cannot
# make progress. Real panes observed in production are used as fixtures.
# ---------------------------------------------------------------------------

BAD_MODEL_PANE = (
    "⚠ Model metadata for `gpt-5.6-luna` not found. Defaulting to fallback metadata;\n"
    '■ {"type":"error","status":400,"error":\n'
    '{"type":"invalid_request_error","message":"The \'gpt-5.6-luna\' model requires a\n'
)

OUTAGE_PANE = (
    "■ unexpected status 503 Service Unavailable: Service Unavailable, url:\n"
    "https://chatgpt.com/backend-api/codex/responses, cf-ray: a20a139efd20d43a-SIN,\n"
    "auth error: 503, auth error code: biscuit_baker_service_me_circuit_open\n"
    "• Reconnecting... 5/5 (47s • esc to interrupt)\n"
)

HEALTHY_PANE = (
    "• Edited src/tcd/worktree.py (+80 lines)\n"
    "• Ran uv run pytest tests/test_worktree.py -v\n"
    "12 passed in 4.46s\n"
    "• Working (2m 18s • esc to interrupt)\n"
)


@pytest.mark.parametrize("provider_name", ["codex", "claude", "gemini"])
@pytest.mark.parametrize("pane", [BAD_MODEL_PANE, OUTAGE_PANE], ids=["bad-model", "outage"])
def test_detect_provider_error_flags_fatal_panes(provider_name, pane):
    """Every provider gets the check, not just the one it was found on."""
    assert get_provider(provider_name).detect_provider_error(pane) is not None


@pytest.mark.parametrize("provider_name", ["codex", "claude", "gemini"])
def test_detect_provider_error_ignores_healthy_pane(provider_name):
    assert get_provider(provider_name).detect_provider_error(HEALTHY_PANE) is None


# ---------------------------------------------------------------------------
# Launch hardening is a base-class default, not a per-provider rediscovery
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider_name", ["codex", "claude", "gemini"])
def test_providers_inherit_prompt_delivery_hardening(provider_name):
    prov = get_provider(provider_name)
    assert prov.tui_stable_secs > 0, "TUI readiness race is unguarded"
    assert prov.verify_prompt_delivery is True, "dropped prompts go undetected"


def test_only_codex_claims_sandbox_support():
    assert get_provider("codex").supports_sandbox is True
    assert get_provider("claude").supports_sandbox is False
    assert get_provider("gemini").supports_sandbox is False


# ---------------------------------------------------------------------------
# Working markers gate prompt re-injection
#
# verify_prompt_delivery re-sends the prompt when it sees no sign of a running
# turn. Markers that never match therefore duplicate the task rather than
# protect it, so each provider must carry markers its own TUI actually prints.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider_name", ["codex", "claude", "gemini"])
def test_providers_declare_working_markers(provider_name):
    markers = get_provider(provider_name).working_markers
    assert markers, "no markers means every running turn looks like a dropped prompt"
    assert all(m == m.lower() for m in markers), "markers are matched against a lowercased pane"


def test_gemini_uses_its_own_running_marker():
    """Verified against the installed bundle: Gemini prints 'esc to cancel'."""
    assert "esc to cancel" in get_provider("gemini").working_markers
    assert "esc to interrupt" not in get_provider("gemini").working_markers


def test_default_markers_cover_both_spellings():
    """A new provider must not inherit a Codex-only assumption."""
    defaults = Provider.working_markers
    assert "esc to interrupt" in defaults
    assert "esc to cancel" in defaults


def test_verify_prompt_delivery_uses_provider_markers():
    from tcd.readiness import verify_prompt_delivery

    class FakeTmux:
        def __init__(self):
            self.sends = 0

        def capture_pane(self, session, **kwargs):
            # A running Gemini turn, with the echoed prompt already scrolled off.
            return "gemini is thinking (12s • esc to cancel)"

        def send_text(self, session, text):
            self.sends += 1
            return True

    tmux = FakeTmux()
    confirmed = verify_prompt_delivery(
        tmux, "sess", "job1", "do the thing",
        markers=get_provider("gemini").working_markers,
    )
    assert confirmed is True
    assert tmux.sends == 0, "a running turn must not trigger a re-send"


# ---------------------------------------------------------------------------
# PROVIDER_ERROR must not fire on an agent that merely *mentions* an error
# ---------------------------------------------------------------------------

WORKING_ON_RETRY_LOGIC_PANE = (
    "• Edited src/client.py (+40 lines)\n"
    "  42 +    # retry when the provider returns a rate limit\n"
    "  43 +    if response.status == 503:  # service unavailable\n"
    "  44 +        raise AuthenticationError('auth error: check the token')\n"
    "• Ran pytest tests/test_retry.py -q\n"
    "  3 passed in 0.4s\n"
    "• Working (2m 10s • esc to interrupt)\n"
)


@pytest.mark.parametrize("provider_name", ["codex", "claude", "gemini"])
def test_detect_provider_error_ignores_agent_written_error_handling(provider_name):
    """The agent writing retry code puts these words on screen legitimately."""
    assert get_provider(provider_name).detect_provider_error(WORKING_ON_RETRY_LOGIC_PANE) is None


def test_detect_provider_error_ignores_prose_mentions():
    prov = get_provider("codex")
    assert prov.detect_provider_error("I hit a rate limit last week, retrying now") is None
    assert prov.detect_provider_error("the docs mention service unavailable errors") is None


def test_detect_provider_error_still_catches_the_real_envelopes():
    prov = get_provider("codex")
    assert prov.detect_provider_error(BAD_MODEL_PANE) is not None
    assert prov.detect_provider_error(OUTAGE_PANE) is not None
