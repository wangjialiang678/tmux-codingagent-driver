"""Antigravity CLI (agy) provider."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from tcd.config import job_log_path, job_signal_path
from tcd.job import Job
from tcd.marker_detector import build_marker_prompt, scan_for_context_limit
from tcd.output_cleaner import strip_ansi
from tcd.provider import CompletionResult, Provider, register_provider
from tcd.tmux_adapter import TmuxAdapter

logger = logging.getLogger(__name__)

MODEL_RE = re.compile(r"^[a-zA-Z0-9._:/-]+$")
EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
REQ_RE = re.compile(r"TCD_REQ:(\S+)")
DONE_RE = re.compile(r"^\s*[│┃┆|>❯›•]*\s*TCD_DONE:(\S+)\s*$", re.MULTILINE)
ACCOUNT_BANNER_RE = re.compile(
    r"^\s*[│┃┆|>❯›•]*\s*(?:signed in as|authenticated as|google account)\s*:?\s*.*$",
    re.IGNORECASE | re.MULTILINE,
)

# Keep API-key, custom-endpoint, and ADC-based routes out of agy's private
# tmux server. Values are never read or logged; the user's normal CLI login
# remains available through Antigravity's own credential store.
AUTH_ENV_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GEMINI_BASE_URL",
    "AGY_ADC_AUTH",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_CLOUD_QUOTA_PROJECT",
    "GCLOUD_PROJECT",
)


def _tmux_for_job(job: Job) -> TmuxAdapter:
    """Select the recorded server, with a legacy default-server fallback."""
    return TmuxAdapter.for_job(job)


def _last_completed_response(pane_text: str, expected_prefix: str) -> tuple[str, str] | None:
    """Return the latest response only when its exact request has two DONEs.

    The prompt itself contains one DONE marker as an instruction. Requiring a
    second, matching marker prevents that echoed instruction from completing a
    job before the agent has returned. Unknown or stale request IDs stay
    inconclusive.
    """
    request_matches = list(REQ_RE.finditer(pane_text))
    if not request_matches:
        return None
    request = request_matches[-1]
    req_id = request.group(1)
    if not req_id.startswith(expected_prefix):
        return None

    done_matches = [m for m in DONE_RE.finditer(pane_text, request.end()) if m.group(1) == req_id]
    if len(done_matches) < 2:
        return None

    # The first marker is the instruction in the prompt, the last is the
    # agent's completion marker. Keep any response text between those lines.
    first_end = pane_text.find("\n", done_matches[0].end())
    if first_end < 0:
        first_end = done_matches[0].end()
    second_start = done_matches[-1].start()
    response = pane_text[first_end + 1:second_start].strip()
    return req_id, response


def _completed_idle_signal(job: Job) -> dict | None:
    """Read this job's latest idle signal, if it belongs to AGY.

    `tcd check` advances the job turn counter after completion, so the
    completed turn's TCD_REQ no longer has the current turn prefix. The signal
    file is cleared before a follow-up send and is therefore the safest source
    for that just-completed response.
    """
    if job.turn_state != "idle":
        return None
    try:
        data = json.loads(job_signal_path(job.id).read_text())
    except (json.JSONDecodeError, OSError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("provider") != "agy" or data.get("state") != "idle":
        return None
    return data


def _completed_signal_response(job: Job) -> str | None:
    """Return the response saved by this job's latest idle marker check."""
    data = _completed_idle_signal(job)
    if data is None:
        return None
    response = data.get("lastAgentMessage")
    return response.strip() if isinstance(response, str) and response.strip() else None


def _expected_turn_prefix(job: Job) -> str:
    """Get the request prefix for the active or most recently completed turn."""
    turn = job.turn_count
    if _completed_idle_signal(job) is not None and turn > 0:
        turn -= 1
    return f"{job.id}-{turn}-"


@register_provider
class AgyProvider(Provider):
    """Adapter for Google's Antigravity CLI.

    Each job uses the locally logged-in Antigravity CLI account. API-key,
    custom-endpoint, and ADC environment routes are removed from the isolated
    job server so inherited shell variables cannot silently change billing.
    """

    name = "agy"
    cli_command = "agy"
    # Verified in the live 1.5.12 TUI footer after login. Readiness polling
    # checks trust-dialog phrases first, so this footer cannot authorize a
    # prompt while a visible directory confirmation is still open.
    tui_ready_indicator = "? for shortcuts"
    # This marker is a footer, not an editable composer prefix.
    delivery_composer_indicator = ""
    tui_stable_secs = 2.0
    # The real 1.5.12 run did not render a verified in-progress status string.
    # Do not treat speculative footer text as delivery evidence.
    working_markers: tuple[str, ...] = ()
    # AGY can accept a prompt even when its rendered echo does not match the
    # generic verifier. Never submit the same request again on that ambiguity.
    prompt_delivery_retries = 0
    message_delivery_retries = 0
    delivery_enter_retries = 0
    require_tui_ready = True
    supports_effort = True

    def check_cli(self) -> None:
        if shutil.which(self.cli_command) is None:
            raise FileNotFoundError(
                "agy not found in PATH. Install Antigravity CLI from: "
                "https://antigravity.google/docs/install?tab=cli"
            )

    def tmux_server_environment(self) -> dict[str, str] | None:
        """Scope AGY auth and proxy environment to its private tmux server.

        Existing per-scheme or ALL_PROXY environment values take precedence.
        System proxy URLs containing credentials are ignored; proxy values are
        passed only via subprocess environment, never command arguments/logs.
        API-key, custom-endpoint, and ADC variables are removed without reading
        their values. The isolated server is necessary so this environment does
        not alter other TCD providers or existing tmux sessions.
        """
        child_env = os.environ.copy()
        for name in AUTH_ENV_KEYS:
            child_env.pop(name, None)

        if sys.platform == "darwin":
            system_proxies = urllib.request.getproxies()
            for scheme in ("http", "https"):
                if any(
                    child_env.get(name)
                    for name in (
                        f"{scheme}_proxy",
                        f"{scheme.upper()}_PROXY",
                        "all_proxy",
                        "ALL_PROXY",
                    )
                ):
                    continue

                value = system_proxies.get(scheme, "")
                try:
                    proxy = urllib.parse.urlsplit(value)
                    port = proxy.port
                except ValueError:
                    continue
                if (
                    proxy.scheme in {"http", "https"}
                    and proxy.hostname
                    and port
                    and proxy.username is None
                    and proxy.password is None
                ):
                    child_env[f"{scheme.upper()}_PROXY"] = value

        return child_env

    def build_launch_command(self, job: Job) -> str:
        """Build an interactive session that uses the local CLI account."""
        log_file = str(job_log_path(job.id))
        parts = [self.cli_command]
        model = job.model
        effort = job.effort
        # These overrides apply only to this CLI session: clear any saved API
        # provider/custom model selection, while leaving the user's settings
        # file and logged-in account untouched.
        session_settings = json.dumps(
            {
                "modelProvider": "",
                "model": "",
                "customModelsConfig": {},
            },
            separators=(",", ":"),
        )
        parts.extend(["--settings", shlex.quote(session_settings)])

        if model == "neon-api":
            raise ValueError(
                "'neon-api' is a custom model identifier and is not available in subscription mode; "
                "select a built-in model with --model or omit --model."
            )

        if model:
            if not MODEL_RE.fullmatch(model):
                raise ValueError(f"Invalid model name: {model!r}")
            parts.extend(["--model", shlex.quote(model)])

        if effort:
            level = effort.strip().lower()
            if level not in EFFORT_LEVELS:
                raise ValueError(
                    f"Invalid effort level: {effort!r}; "
                    f"expected one of {', '.join(sorted(EFFORT_LEVELS))}"
                )
            parts.extend(["--effort", level])

        inner_cmd = " ".join(parts)
        script_cmd = TmuxAdapter.build_script_command(log_file, inner_cmd)
        return f'{script_cmd}; echo "\\n\\n[tcd: session complete]"; read'

    def build_prompt_wrapper(self, message: str, req_id: str) -> str:
        return build_marker_prompt(message, req_id)

    def delivery_completion_check(self, tmux: TmuxAdapter, job: Job, req_id: str) -> bool:
        """Confirm a delivery if this exact request already emitted its DONE marker."""
        pane = tmux.capture_pane(job.tmux_session)
        if not pane:
            return False
        completed = _last_completed_response(pane, f"{job.id}-{job.turn_count}-")
        return completed is not None and completed[0] == req_id

    def detect_completion(self, job: Job) -> CompletionResult | None:
        """Recognize explicit completion only; do not infer idle from silence."""
        signal_path = job_signal_path(job.id)
        if signal_path.exists():
            try:
                data = json.loads(signal_path.read_text())
                state = data.get("state")
                if state in {"idle", "context_limit"}:
                    return CompletionResult(
                        state=state,
                        last_agent_message=data.get("lastAgentMessage"),
                    )
            except (json.JSONDecodeError, OSError, TypeError):
                logger.exception("Failed to read agy signal file for job %s", job.id)

        tmux = _tmux_for_job(job)
        if not tmux.session_exists(job.tmux_session):
            return None
        pane = tmux.capture_pane(job.tmux_session)
        if not pane:
            return None

        if scan_for_context_limit("\n".join(pane.splitlines()[-50:])):
            self._write_signal(job, "context_limit")
            return CompletionResult(state="context_limit")

        expected_prefix = f"{job.id}-{job.turn_count}-"
        completed = _last_completed_response(pane, expected_prefix)
        if completed is None:
            return None

        _req_id, response = completed
        self._write_signal(job, "idle", response)
        return CompletionResult(state="idle", last_agent_message=response or None)

    def parse_response(self, job: Job) -> str | None:
        completed_response = _completed_signal_response(job)
        if completed_response:
            return completed_response
        expected_prefix = _expected_turn_prefix(job)
        tmux = _tmux_for_job(job)
        if tmux.session_exists(job.tmux_session):
            pane = tmux.capture_pane(job.tmux_session)
            if pane:
                completed = _last_completed_response(pane, expected_prefix)
                if completed is not None and completed[1]:
                    return completed[1]

        # The tmux scrollback is bounded by the user's server settings; the
        # script transcript provides a second source if the pane has rotated.
        log_path = job_log_path(job.id)
        try:
            with log_path.open("rb") as log_file:
                log_file.seek(0, os.SEEK_END)
                size = log_file.tell()
                log_file.seek(max(0, size - 16 * 1024 * 1024))
                transcript = log_file.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        completed = _last_completed_response(strip_ansi(transcript), expected_prefix)
        return completed[1] or None if completed is not None else None

    def sanitize_full_output(self, job: Job, output: str) -> str:
        """Keep full scrollback useful without exposing the CLI account banner.

        Some terminal renderings include the signed-in account in the startup
        banner. Remove that line, then restore the exact completed response if
        TUI cleanup or alternate-screen rendering omitted it from the pane.
        """
        output = ACCOUNT_BANNER_RE.sub("", output).strip()
        try:
            response = self.parse_response(job)
        except Exception:
            # Account-banner removal must still apply if a tmux/session probe
            # fails while trying to recover an answer omitted by the pane.
            logger.debug("Could not recover parsed full-output response for job %s", job.id)
            response = None
        if response and response not in output:
            output = f"{output}\n\n{response}".strip()
        return output

    def get_session_log_path(self, job: Job) -> Path | None:
        log_path = job_log_path(job.id)
        return log_path if log_path.exists() else None

    def _write_signal(self, job: Job, state: str, last_agent_message: str | None = None) -> None:
        try:
            job_signal_path(job.id).write_text(json.dumps({
                "state": state,
                "provider": "agy",
                "lastAgentMessage": last_agent_message,
            }))
        except OSError as exc:
            logger.error("Failed to write agy signal file: %s", exc)
