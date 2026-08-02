"""Provider abstract base class and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from tcd.job import Job


@dataclass
class CompletionResult:
    """Result of a completion detection check."""
    state: str  # "idle" | "working" | "context_limit"
    last_agent_message: str | None = None
    turn_id: str | None = None
    tokens: dict[str, int] | None = None


# Pane substrings that mean the CLI hit a fatal provider-side error and will
# make no progress: the agent looks "working" while burning the whole timeout.
# Observed with Codex (bad model id -> 400; upstream 503 -> reconnect loop),
# but the shapes are generic enough to be worth checking for every provider.
_PROVIDER_ERROR_SIGNS: tuple[tuple[str, str], ...] = (
    ("invalid_request_error", "provider rejected the request (check --model / credentials)"),
    ("model requires", "the selected model is not available to this account"),
    ("model_not_found", "the selected model does not exist"),
    ("insufficient_quota", "the provider account is out of quota"),
    ("authentication_error", "provider authentication failed"),
    ("auth error:", "provider authentication failed"),
    ("service unavailable", "the provider API is unavailable"),
    ("internal_server_error", "the provider API returned an internal error"),
    ("rate limit", "the provider is rate limiting this account"),
)


class Provider(ABC):
    """Base class for AI CLI adapters."""

    name: str
    cli_command: str
    # Substring to detect when the TUI is ready to accept input.
    # Override in subclasses. None means just use a fixed delay.
    tui_ready_indicator: str | None = None
    # After the readiness indicator appears, require the pane to remain
    # unchanged for this many seconds before declaring the TUI ready. Guards
    # against indicators that appear in a startup banner before the TUI can
    # actually accept input, which silently swallows the injected prompt.
    #
    # This defaults to a safe value: every TUI agent has a window between
    # "banner drawn" and "input accepted", so a new provider should have to opt
    # *out* of the protection rather than rediscover the bug.
    tui_stable_secs: float = 2.0
    # After injecting the prompt, verify the agent actually received it and
    # resend if it was dropped. Verification is provider-agnostic (it looks for
    # the prompt echoed back), so it is on by default for the same reason.
    verify_prompt_delivery: bool = True
    # Whether `--sandbox` means anything to this CLI. When False, tcd rejects
    # the flag instead of accepting it and silently dropping it.
    supports_sandbox: bool = False

    def detect_provider_error(self, pane: str) -> str | None:
        """Return a human-readable reason if the pane shows a fatal API error.

        Returning a reason does not stop the job; it surfaces a warning so the
        caller does not wait out a 60-minute timeout on a job that cannot make
        progress.
        """
        low = pane.lower()
        for needle, reason in _PROVIDER_ERROR_SIGNS:
            if needle in low:
                return reason
        return None

    @abstractmethod
    def build_launch_command(self, job: Job) -> str:
        """Build the shell command to start the AI CLI."""

    @abstractmethod
    def build_prompt_wrapper(self, message: str, req_id: str) -> str:
        """Wrap the user prompt (e.g. add completion markers)."""

    @abstractmethod
    def detect_completion(self, job: Job) -> CompletionResult | None:
        """Detect whether the current turn is complete.

        Returns a CompletionResult or None if detection is inconclusive.
        """

    @abstractmethod
    def parse_response(self, job: Job) -> str | None:
        """Parse the AI response from session/log files."""

    @abstractmethod
    def get_session_log_path(self, job: Job) -> Path | None:
        """Return the AI's native session file path, if available."""


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_registry: dict[str, type[Provider]] = {}


def register_provider(cls: type[Provider]) -> type[Provider]:
    """Class decorator to register a provider by its `name` attribute."""
    _registry[cls.name] = cls
    return cls


def get_provider(name: str) -> Provider:
    """Look up and instantiate a provider by name."""
    cls = _registry.get(name)
    if cls is None:
        available = ", ".join(sorted(_registry)) or "(none)"
        raise ValueError(f"Unknown provider: {name!r}. Available: {available}")
    return cls()


def list_providers() -> list[str]:
    """Return sorted list of registered provider names."""
    return sorted(_registry)
