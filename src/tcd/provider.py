"""Provider abstract base class and registry."""

from __future__ import annotations

import re
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


# Patterns that mean the CLI hit a fatal provider-side error and will make no
# progress: the agent looks "working" while burning the whole timeout. Observed
# with Codex (bad model id -> 400; upstream 503 -> reconnect loop), but the
# shapes are generic enough to check for every provider.
#
# These match the machine-emitted error envelopes, not prose. Plain substrings
# like "rate limit" or "service unavailable" also appear when an agent is
# *working on* retry logic or reading a log file, and a false PROVIDER_ERROR
# telling the caller to kill a healthy job is worse than no warning at all.
_PROVIDER_ERROR_SIGNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r'"type"\s*:\s*"invalid_request_error"'),
        "provider rejected the request (check --model / credentials)",
    ),
    (
        re.compile(r'"type"\s*:\s*"authentication_error"'),
        "provider authentication failed",
    ),
    (
        re.compile(r'"(?:code|type)"\s*:\s*"(?:model_not_found|insufficient_quota)"'),
        "the selected model is unavailable to this account",
    ),
    (
        re.compile(r"^\s*[■✗]?\s*unexpected status (5\d{2}|4\d{2})\b", re.MULTILINE),
        "the provider API returned an error status",
    ),
    (
        re.compile(r"^\s*auth error:\s*\d{3}\b", re.MULTILINE),
        "provider authentication failed",
    ),
    (
        re.compile(r"\bReconnecting\.\.\.\s*(\d+)/\1\b"),
        "the provider connection is exhausted (final reconnect attempt)",
    ),
    (
        re.compile(r"Model metadata for `[^`]+` not found"),
        "the configured model id is not recognised by the provider",
    ),
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
    # Pane substrings proving a turn is running. Used as the second, more
    # durable half of delivery verification: the echoed prompt scrolls away,
    # this does not. Getting it wrong is worse than having no verification —
    # a running turn reads as "prompt dropped" and the prompt is re-sent, so
    # the task executes twice. The default carries every spelling seen in the
    # wild; a provider should narrow it only against its actual TUI.
    working_markers: tuple[str, ...] = (
        "esc to interrupt",
        "esc to cancel",
        "tokens used",
        "working (",
    )
    # Whether `--sandbox` means anything to this CLI. When False, tcd rejects
    # the flag instead of accepting it and silently dropping it.
    supports_sandbox: bool = False

    def detect_provider_error(self, pane: str) -> str | None:
        """Return a human-readable reason if the pane shows a fatal API error.

        Returning a reason does not stop the job; it surfaces a warning so the
        caller does not wait out a 60-minute timeout on a job that cannot make
        progress.
        """
        for pattern, reason in _PROVIDER_ERROR_SIGNS:
            if pattern.search(pane):
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
