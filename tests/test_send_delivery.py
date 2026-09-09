"""CLI delivery confirmation tests without starting real tmux sessions."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tcd.cli import cli
from tcd.event_log import emit, load_events
from tcd.job import JobManager


@pytest.fixture()
def env(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr("tcd.config.TCD_HOME", tmp_path)
    monkeypatch.setattr("tcd.config.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("tcd.job.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("tcd.job.job_json_path", lambda jid: jobs_dir / f"{jid}.json")
    monkeypatch.setattr("tcd.job.job_log_path", lambda jid: jobs_dir / f"{jid}.log")
    monkeypatch.setattr("tcd.job.job_prompt_path", lambda jid: jobs_dir / f"{jid}.prompt")
    monkeypatch.setattr("tcd.job.job_signal_path", lambda jid: jobs_dir / f"{jid}.turn-complete")
    monkeypatch.setattr("tcd.cli.job_signal_path", lambda jid: jobs_dir / f"{jid}.turn-complete")
    monkeypatch.setattr("tcd.readiness.time.sleep", lambda *_: None)
    return jobs_dir


class FakeProvider:
    name = "codex"
    tui_ready_indicator = "›"
    composer_placeholder = "Ask Codex to do anything"
    working_markers = ("esc to interrupt", "working (")
    verify_prompt_delivery = True
    supports_sandbox = True

    def check_cli(self):
        return None

    def build_launch_command(self, job):
        return "fake"

    def build_prompt_wrapper(self, message, req_id):
        return message


class FakeTmux:
    def __init__(self, pane="› Ask Codex to do anything", *, on_enter=None, on_text=None):
        self.pane = pane
        self.on_enter = on_enter
        self.on_text = on_text
        self.enters = 0
        self.texts = []
        self.killed = False

    def check_tmux(self): return None
    def create_session(self, session, cmd, cwd): return True
    def session_exists(self, session): return True
    def capture_pane(self, session, **kwargs): return self.pane
    def send_enter(self, session):
        self.enters += 1
        if self.on_enter is not None:
            self.pane = self.on_enter
        return True
    def send_text(self, session, text):
        self.texts.append(text)
        if self.on_text is not None:
            self.pane = self.on_text
        return True
    def kill_session(self, session):
        self.killed = True
        return True


def _running_job():
    mgr = JobManager()
    job = mgr.create_job("codex", "first", "/tmp")
    job.status = "running"
    job.turn_state = "idle"
    mgr.save_job(job)
    return job


def _wire(monkeypatch, tmux):
    monkeypatch.setattr("tcd.cli._get_tmux", lambda: tmux)
    monkeypatch.setattr("tcd.cli.get_provider", lambda _name: FakeProvider())


def test_start_unconfirmed_exits_three_and_keeps_session(env, monkeypatch):
    tmux = FakeTmux(pane="› prompt remains")
    _wire(monkeypatch, tmux)
    monkeypatch.setattr("tcd.cli.wait_for_tui", lambda *a, **k: (True, 1, False))

    result = CliRunner().invoke(cli, ["start", "-p", "codex", "-m", "prompt remains", "-d", "/tmp"])

    assert result.exit_code == 3
    assert "Job started but prompt NOT confirmed" in result.output
    job = JobManager().list_jobs()[0]
    assert job.status == "running"
    assert job.delivery == "unconfirmed"
    assert tmux.killed is False
    assert load_events(job.id, "job.prompt_unconfirmed")


def test_send_enter_swallowed_then_confirmed(env, monkeypatch):
    job = _running_job()
    tmux = FakeTmux(
        pane="› follow up\n100% context left · tokens used",
        on_enter="Working (1s - esc to interrupt)\n› Ask Codex to do anything",
    )
    _wire(monkeypatch, tmux)

    result = CliRunner().invoke(cli, ["send", job.id, "follow up", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["delivery"] == "confirmed"
    assert tmux.enters >= 1
    assert load_events(job.id, "job.message_enter_retry")
    assert load_events(job.id, "job.message_confirmed")


def test_send_absent_resends_once_then_confirms(env, monkeypatch):
    job = _running_job()
    tmux = FakeTmux(pane="› Ask Codex to do anything")
    original_send = tmux.send_text

    def swallowed_once(session, text):
        result = original_send(session, text)
        if len(tmux.texts) == 2:
            tmux.pane = "Working (1s - esc to interrupt)"
        return result

    tmux.send_text = swallowed_once
    _wire(monkeypatch, tmux)

    result = CliRunner().invoke(cli, ["send", job.id, "follow up"])

    assert result.exit_code == 0
    assert len(tmux.texts) == 2  # original send + one bounded recovery resend
    assert load_events(job.id, "job.message_confirmed")


def test_send_unconfirmed_exits_three_and_emits_event(env, monkeypatch):
    job = _running_job()
    tmux = FakeTmux(pane="› stuck follow up")
    _wire(monkeypatch, tmux)

    result = CliRunner().invoke(cli, ["send", job.id, "stuck follow up"])

    assert result.exit_code == 3
    assert JobManager().load_job(job.id).delivery == "unconfirmed"
    assert load_events(job.id, "job.message_unconfirmed")


def test_nudge_sends_one_enter_and_observes(env, monkeypatch):
    job = _running_job()
    tmux = FakeTmux(pane="› stuck follow up", on_enter="Working (1s - esc to interrupt)")
    _wire(monkeypatch, tmux)

    result = CliRunner().invoke(cli, ["nudge", job.id, "--json"])

    assert result.exit_code == 0
    assert tmux.enters == 1
    assert json.loads(result.output)["delivery"] == "confirmed"
    assert load_events(job.id, "job.nudge")[-1]["result"] == "confirmed"


def test_nudge_unconfirmed_exits_three(env, monkeypatch):
    job = _running_job()
    tmux = FakeTmux(pane="› still stuck")
    _wire(monkeypatch, tmux)

    result = CliRunner().invoke(cli, ["nudge", job.id])

    assert result.exit_code == 3
    assert tmux.enters == 1
    assert load_events(job.id, "job.nudge")[-1]["result"] == "unconfirmed"


@pytest.mark.parametrize(
    ("final_event", "expected"),
    [
        (None, "pending"),
        ("job.message_confirmed", "confirmed"),
        ("job.message_unconfirmed", "unconfirmed"),
    ],
)
def test_status_derives_delivery_and_last_event(env, monkeypatch, final_event, expected):
    job = _running_job()
    job.last_agent_message_path = "/tmp/full-message.md"
    JobManager().save_job(job)
    tmux = FakeTmux()
    _wire(monkeypatch, tmux)
    emit(job.id, "job.message_sent", req_id="r1")
    if final_event:
        emit(job.id, final_event, req_id="r1")

    result = CliRunner().invoke(cli, ["status", job.id, "--json"])

    payload = json.loads(result.output)
    assert payload["delivery"] == expected
    assert payload["last_event"]["type"] == (final_event or "job.message_sent")
    assert payload["last_event"]["time"]
    assert payload["last_agent_message_path"] == "/tmp/full-message.md"


def test_status_old_job_delivery_is_unknown(env, monkeypatch):
    job = _running_job()
    tmux = FakeTmux()
    _wire(monkeypatch, tmux)

    result = CliRunner().invoke(cli, ["status", job.id, "--json"])

    assert json.loads(result.output)["delivery"] == "unknown"
