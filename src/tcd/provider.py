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


class Provider(ABC):
    """Base class for AI CLI adapters."""

    name: str
    cli_command: str
    # Substring to detect when the TUI is ready to accept input.
    # Override in subclasses. None means just use a fixed delay.
    tui_ready_indicator: str | None = None

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
