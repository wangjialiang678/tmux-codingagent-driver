"""tmux-codingagent-driver: Drive AI CLI tools via tmux."""

__version__ = "0.3.3"

# Auto-register providers on import
import tcd.providers.codex  # noqa: F401
import tcd.providers.claude  # noqa: F401
import tcd.providers.gemini  # noqa: F401

from tcd.sdk import TCD  # noqa: F401
