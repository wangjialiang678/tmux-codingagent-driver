"""Offline regressions for AGY's account-subscription path in TCD."""

from __future__ import annotations

import json
import shlex
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from tcd.cli import cli
from tcd.event_log import load_events
from tcd.job import Job, JobManager
from tcd.providers.agy import AgyProvider
from tcd.readiness import observe_delivery

_ENV_NOT_PROVIDED = object()
MODEL = "gemini-4-neon-medium"
API_ENV_NAMES = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GEMINI_BASE_URL",
    "AGY_ADC_AUTH",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_QUOTA_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GCLOUD_PROJECT",
)
TEST_ONLY_SENTINEL = "TCD_TEST_ONLY_FAKE_GOOGLE_CREDENTIAL"


def _agy_args(command: str) -> list[str]:
    launch = command.partition("; echo ")[0]
    tokens = shlex.split(launch)
    if "-c" in tokens:
        tokens = shlex.split(tokens[tokens.index("-c") + 1])
    return tokens[tokens.index("agy"):]


@pytest.fixture
def tcd_harness(tmp_path, monkeypatch):
    """Keep job files local and replace tmux/readiness with in-memory fakes."""
    import tcd.config

    jobs_dir = tmp_path / "jobs"
    locks_dir = tmp_path / "locks"
    monkeypatch.setattr(tcd.config, "TCD_HOME", tmp_path)
    monkeypatch.setattr(tcd.config, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(tcd.config, "LOCKS_DIR", locks_dir)
    monkeypatch.setattr("tcd.job.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("tcd.job.job_json_path", lambda job_id: jobs_dir / f"{job_id}.json")
    monkeypatch.setattr("tcd.job.job_log_path", lambda job_id: jobs_dir / f"{job_id}.log")
    monkeypatch.setattr("tcd.job.job_prompt_path", lambda job_id: jobs_dir / f"{job_id}.prompt")
    monkeypatch.setattr("tcd.job.job_signal_path", lambda job_id: jobs_dir / f"{job_id}.turn-complete")
    monkeypatch.setattr("tcd.cli.job_signal_path", lambda job_id: jobs_dir / f"{job_id}.turn-complete")
    monkeypatch.setattr("tcd.providers.agy.shutil.which", lambda _name: "/fake/bin/agy")

    class FakeTmux:
        def __init__(self, socket=None):
            self.socket = socket
            self.commands = []
            self.session_envs = []
            self.sent_text = []
            self.sent_enter = []

        def check_tmux(self):
            return None

        def create_session(self, session, command, cwd, *, env=_ENV_NOT_PROVIDED):
            self.commands.append((session, command, cwd))
            self.session_envs.append(env)
            return True

        def send_text(self, session, text):
            self.sent_text.append((session, text))
            return True

        def send_enter(self, session):
            self.sent_enter.append(session)
            return True

        def capture_pane(self, _session, **_kwargs):
            return "? for shortcuts"

        def session_exists(self, _session):
            return False

        def kill_session(self, _session):
            return True

    default_tmux = FakeTmux(socket="tcd")
    private_tmuxes = []

    def fake_tmux_adapter(socket=None):
        adapter = FakeTmux(socket=socket)
        private_tmuxes.append(adapter)
        return adapter

    monkeypatch.setattr("tcd.cli._get_tmux", lambda: default_tmux)
    monkeypatch.setattr("tcd.cli.TmuxAdapter", fake_tmux_adapter)
    monkeypatch.setattr("tcd.cli.wait_for_tui", lambda *_args, **_kwargs: (True, 0, False))
    monkeypatch.setattr(
        "tcd.providers.agy.os",
        SimpleNamespace(environ={"PATH": "/fake/bin"}),
    )
    return CliRunner(), private_tmuxes, jobs_dir


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_agy_launch_selects_the_subscription_neon_effort_slug_without_api_models(tmp_path, effort):
    model = f"gemini-4-neon-{effort}"
    job = Job(
        id="sub12345",
        provider="agy",
        status="pending",
        prompt="Use the account's Antigravity subscription.",
        cwd=str(tmp_path),
        tmux_session="tcd-agy-sub12345",
        tmux_socket="tcd-agy-sub12345",
        model=model,
        effort=effort,
    )

    args = _agy_args(AgyProvider().build_launch_command(job))
    settings = json.loads(args[args.index("--settings") + 1])

    assert args[args.index("--model") + 1] == model
    assert args[args.index("--effort") + 1] == effort
    assert settings["modelProvider"] == ""
    assert settings["model"] == ""
    assert settings["customModelsConfig"] == {}
    assert "gemini-api://" not in json.dumps(settings)
    assert "neon-api" not in json.dumps(settings)
    assert not any(value.startswith("gemini-api://") for value in args)


def test_agy_auth_override_is_not_exposed_by_tcd_cli():
    result = CliRunner().invoke(cli, ["start", "--help"])

    assert result.exit_code == 0, result.output
    assert "--agy-auth" not in result.output
    assert "gemini-api-key" not in result.output


def test_tcd_rejects_api_custom_model_without_starting_agy(tcd_harness, tmp_path):
    runner, tmuxes, _jobs_dir = tcd_harness
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = runner.invoke(
        cli,
        [
            "start", "--provider", "agy", "--prompt", "do not use the API custom model",
            "--cwd", str(workspace), "--model", "neon-api", "--json",
        ],
    )

    assert result.exit_code == 1
    assert "not available in subscription mode" in result.output
    assert len(tmuxes) == 1
    assert tmuxes[0].commands == []


def test_start_uses_private_subscription_session_with_cwd_and_clean_environment(
    tcd_harness, tmp_path, monkeypatch
):
    runner, tmuxes, jobs_dir = tcd_harness
    import tcd.providers.agy as agy_module

    process_env = {"PATH": "/fake/bin", **{name: TEST_ONLY_SENTINEL for name in API_ENV_NAMES}}
    monkeypatch.setattr(agy_module, "os", SimpleNamespace(environ=process_env))
    monkeypatch.setattr(agy_module, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(
        agy_module.urllib.request,
        "getproxies",
        lambda: {
            "http": "http://system-http.invalid.test:8080",
            "https": "https://system-https.invalid.test:8443",
        },
    )
    monkeypatch.setattr("tcd.cli.verify_prompt_delivery", lambda *_args, **_kwargs: True)
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()

    result = runner.invoke(
        cli,
        [
            "start", "--provider", "agy", "--prompt", "use my Neon subscription",
            "--cwd", str(workspace), "--model", MODEL, "--effort", "medium", "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(tmuxes) == 1
    tmux = tmuxes[0]
    assert tmux.socket.startswith("tcd-agy-")
    assert len(tmux.commands) == 1
    assert tmux.commands[0][2] == str(workspace)
    args = _agy_args(tmux.commands[0][1])
    settings = json.loads(args[args.index("--settings") + 1])
    assert args[args.index("--model") + 1] == MODEL
    assert settings["modelProvider"] == ""
    assert settings["customModelsConfig"] == {}

    session_env = tmux.session_envs[0]
    assert isinstance(session_env, dict)
    assert session_env["PATH"] == "/fake/bin"
    assert all(name not in session_env for name in API_ENV_NAMES)
    assert session_env["HTTP_PROXY"] == "http://system-http.invalid.test:8080"
    assert session_env["HTTPS_PROXY"] == "https://system-https.invalid.test:8443"
    assert "system-http.invalid.test" not in result.output
    assert "system-https.invalid.test" not in result.output

    saved_job = json.loads(next(jobs_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert saved_job["model"] == MODEL
    assert saved_job["effort"] == "medium"
    assert saved_job.get("agy_auth") is None
    assert saved_job["tmux_socket"] == tmux.socket
    assert saved_job["cwd"] == str(workspace)


def test_ambiguous_prompt_delivery_is_not_resubmitted(tcd_harness, tmp_path, monkeypatch):
    runner, tmuxes, jobs_dir = tcd_harness
    verifier_calls = []

    def ambiguous_delivery(*_args, **kwargs):
        verifier_calls.append(kwargs)
        return False

    monkeypatch.setattr("tcd.cli.verify_prompt_delivery", ambiguous_delivery)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = runner.invoke(
        cli,
        [
            "start", "--provider", "agy", "--prompt", "send exactly once",
            "--cwd", str(workspace), "--model", MODEL, "--effort", "medium", "--json",
        ],
    )

    assert result.exit_code == 3
    assert len(tmuxes) == 1
    assert len(tmuxes[0].sent_text) == 1
    assert len(verifier_calls) == 1
    assert verifier_calls[0]["retries"] == 0
    assert verifier_calls[0]["enter_retries"] == 0
    assert AgyProvider.prompt_delivery_retries == 0
    assert AgyProvider.message_delivery_retries == 0
    assert AgyProvider.delivery_enter_retries == 0
    saved_job = json.loads(next(jobs_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert saved_job["delivery"] == "unconfirmed"
    assert saved_job["turn_count"] == 0


def test_agy_activity_marker_must_be_in_the_live_footer_not_old_visible_text():
    """A transcript mentioning the hotkey must not masquerade as live work."""
    provider = AgyProvider()
    old_turn = (
        "Previous response: the running footer said esc to cancel\n"
        "? for shortcuts"
    )
    echoed_prompt = (
        "If the job is still active, look for esc to cancel in the footer.\n"
        "? for shortcuts"
    )
    assert provider.pane_looks_working(old_turn) is False
    assert provider.pane_looks_working(echoed_prompt) is False

    class Pane:
        def __init__(self, text):
            self.text = text

        def capture_pane(self, _session, **_kwargs):
            return self.text

    for stale_pane in (old_turn, echoed_prompt):
        assert observe_delivery(
            Pane(stale_pane),
            "session",
            markers=provider.working_markers,
            working_check=provider.delivery_activity_check,
            timeout_secs=0,
        ) == "absent"


def test_agy_working_footer_confirms_delivery_when_permission_waits(monkeypatch):
    """A tool permission gate follows delivery; it does not mean completion."""
    provider = AgyProvider()
    pane = (
        "Allow creation of this file?\n"
        "1. Yes, allow creation\n"
        "2. No\n"
        "esc to cancel                  auto · Gemini 4 Neon · low"
    )

    class Pane:
        def capture_pane(self, _session, **_kwargs):
            return pane

    monkeypatch.setattr("tcd.readiness.time.sleep", lambda *_: None)
    assert observe_delivery(
        Pane(),
        "session",
        markers=provider.working_markers,
        working_check=provider.delivery_activity_check,
        timeout_secs=0,
    ) == "running"


def test_agy_start_confirms_live_footer_without_resending_prompt(
    tcd_harness, tmp_path, monkeypatch
):
    """The provider-specific footer confirms one send while the task is active."""
    runner, tmuxes, _jobs_dir = tcd_harness
    from tcd.readiness import verify_prompt_delivery as real_verify

    live_footer = "esc to cancel                  auto · Gemini 4 Neon · medium"

    def verify_with_live_footer(tmux, *args, **kwargs):
        tmux.capture_pane = lambda *_args, **_kwargs: live_footer
        return real_verify(tmux, *args, **kwargs)

    monkeypatch.setattr("tcd.cli.verify_prompt_delivery", verify_with_live_footer)
    monkeypatch.setattr("tcd.readiness.delivery_timeout_secs", lambda _text: 0)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = runner.invoke(
        cli,
        [
            "start", "--provider", "agy", "--prompt", "run exactly once",
            "--cwd", str(workspace), "--model", MODEL, "--effort", "medium", "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["delivery"] == "confirmed"
    assert len(tmuxes) == 1
    assert len(tmuxes[0].sent_text) == 1
    assert not tmuxes[0].sent_enter, "a live footer needs neither a bare Enter nor a prompt resend"
    events = load_events(payload["job_id"])
    assert len([event for event in events if event["event"] == "job.prompt_sent"]) == 1
    assert not [event for event in events if event["event"] == "job.prompt_resend"]


def test_agy_live_footer_is_delivery_evidence_not_completion(
    tmp_path, monkeypatch
):
    provider = AgyProvider()
    job = Job(
        id="liveonly1",
        provider="agy",
        status="running",
        prompt="A live turn",
        cwd=str(tmp_path),
        tmux_session="tcd-agy-liveonly1",
        tmux_socket="tcd-agy-liveonly1",
        model=MODEL,
    )
    pane = "esc to cancel                  auto · Gemini 4 Neon · medium"

    class Pane:
        def session_exists(self, _session):
            return True

        def capture_pane(self, _session, **_kwargs):
            return pane

    monkeypatch.setattr("tcd.providers.agy.job_signal_path", lambda _job_id: tmp_path / "missing-signal")
    monkeypatch.setattr("tcd.providers.agy._tmux_for_job", lambda _job: Pane())
    assert provider.detect_completion(job) is None


def test_system_proxy_credentials_are_ignored_for_subscription_session(monkeypatch):
    import tcd.providers.agy as agy_module

    process_env = {"PATH": "/fake/bin"}
    monkeypatch.setattr(agy_module, "os", SimpleNamespace(environ=process_env))
    monkeypatch.setattr(agy_module, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(
        agy_module.urllib.request,
        "getproxies",
        lambda: {
            "http": "http://proxy-user:proxy-pass@system-http.invalid.test:8080",
            "https": "https://proxy-user:proxy-pass@system-https.invalid.test:8443",
        },
    )

    session_env = AgyProvider().tmux_server_environment()

    assert session_env == {"PATH": "/fake/bin"}
    assert "proxy-user" not in json.dumps(session_env)
    assert "proxy-pass" not in json.dumps(session_env)
    assert "system-http.invalid.test" not in json.dumps(session_env)
    assert "system-https.invalid.test" not in json.dumps(session_env)


@pytest.mark.parametrize("pane_contains_answer", [False, True])
def test_output_collects_full_agy_answer_without_exposing_account_or_dropping_answer(
    tcd_harness, tmp_path, monkeypatch, pane_contains_answer
):
    runner, _tmuxes, _jobs_dir = tcd_harness
    import tcd.collector as collector_module

    manager = JobManager()
    job = manager.create_job(
        "agy",
        "USER_PROMPT_SENTINEL: explain the test fixture",
        str(tmp_path),
        model=MODEL,
        effort="medium",
    )
    job.status = "running"
    # `tcd check` advances the count after saving the completed answer, so the
    # old turn's request marker remains in the pane.
    job.turn_count = 1
    job.turn_state = "idle"
    job.last_agent_message = "STALE_JOB_FIELD_SENTINEL"
    manager.save_job(job)
    req_id = f"{job.id}-0-1234567890"
    account_sentinel = "ACCOUNT_SENTINEL@example.invalid"
    first_answer_line = "FULL_ANSWER_SENTINEL: first paragraph is intact"
    second_answer_line = "FULL_ANSWER_SENTINEL: second paragraph is intact"
    full_answer = f"{first_answer_line}\n{second_answer_line}"
    signal_path = tmp_path / f"{job.id}.turn-complete"
    signal_path.write_text(
        json.dumps({
            "state": "idle",
            "provider": "agy",
            "lastAgentMessage": full_answer,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("tcd.providers.agy.job_signal_path", lambda _job_id: signal_path)
    pane_lines = [
        "Antigravity CLI startup banner",
        f"Signed in as {account_sentinel}",
        f"TCD_REQ:{req_id}",
        "USER_PROMPT_SENTINEL: explain the test fixture",
        "IMPORTANT: output the completion marker at the end",
        f"TCD_DONE:{req_id}",
    ]
    if pane_contains_answer:
        pane_lines.extend([first_answer_line, second_answer_line, f"TCD_DONE:{req_id}"])
    else:
        pane_lines.append("? for shortcuts")
    pane = "\n".join(pane_lines)

    class FakeTmux:
        def __init__(self):
            self.capture_calls = []

        def session_exists(self, _session):
            return True

        def capture_pane(self, session, **kwargs):
            self.capture_calls.append((session, kwargs))
            return pane

    fake_tmux = FakeTmux()
    monkeypatch.setattr("tcd.providers.agy._tmux_for_job", lambda _job: fake_tmux)
    monkeypatch.setattr(
        collector_module.TmuxAdapter,
        "for_job",
        classmethod(lambda _cls, _job: fake_tmux),
    )

    default_result = runner.invoke(cli, ["output", job.id])
    full_result = runner.invoke(cli, ["output", job.id, "--full"])

    for result in (default_result, full_result):
        assert result.exit_code == 0, result.output
        assert first_answer_line in result.output
        assert second_answer_line in result.output
        assert result.output.count(first_answer_line) == 1
        assert result.output.count(second_answer_line) == 1
        assert account_sentinel not in result.output
        assert "STALE_JOB_FIELD_SENTINEL" not in result.output
        assert "(no output available)" not in result.output
    assert "Antigravity CLI startup banner" not in default_result.output
    assert any(kwargs.get("start_line") == "-" for _session, kwargs in fake_tmux.capture_calls)
