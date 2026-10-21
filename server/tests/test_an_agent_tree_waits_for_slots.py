"""An agent tree runs a few turns at a time, and a handler waiting on its agents does not block them.

Through the real turn machinery — `begin_turn`, `start_from_mail`, the report on the way up — with
only the model call replaced. What the unit tests in `test_turns_wait_their_turn.py` say about the
queue, these say about a tree:

* three agents started at once with one slot run one after another, and all three report;
* a handler parked in `wait_for_agents` gives its slot to the agent it is waiting for. With one
  slot and no parking this deadlocks until the wait times out, which is what the test would show.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kith.api.routes import chat as route
from kith.infra.workspace import paths
from kith.kernel import live_turns, session_context
from kith.kernel import turns as supervisor
from kith.services import agents, conversations


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def db(never_the_real_database) -> Path:
    return never_the_real_database


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    here = tmp_path / "project"
    here.mkdir()
    _git(here, "init", "-q", "-b", "main")
    (here / "app.py").write_text("print('hi')\n")
    (here / ".gitignore").write_text(".kith/\nnode_modules/\n.env\n")
    _git(here, "add", "-A")
    _git(here, "commit", "-q", "-m", "start")
    return here


@pytest.fixture
def machinery(monkeypatch, repo_dir):
    """The real turn path with the model call taken out. Returns the fake turn's hook."""
    monkeypatch.setattr(route, "_build_messages", lambda history, *a, **k: list(history))
    monkeypatch.setattr(route, "_tool_block_chars", lambda: 0)
    monkeypatch.setattr(route.tools, "host", lambda *a, **k: SimpleNamespace(schemas=lambda *a, **k: []))
    monkeypatch.setattr(agents, "base_dir", lambda: repo_dir)

    def install(body, limit: int):
        def fake_turn(
            recorder, messages, config, conversation_id, opening="", *, stop_switch=None, tool_host=None
        ):
            reply = body(conversation_id, opening) or "Done."
            conversations.record(route.AGENT_DB_PATH, conversation_id, "assistant", reply)
            yield '{"type": "delta", "role": "text", "text": "ok"}\n'

        monkeypatch.setattr(route, "_turn", fake_turn)
        supervisor.install(route.run_queued, limit=lambda: limit)

    return install


def _settle(until, seconds: float = 15.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if until() and not live_turns.live() and not supervisor.queued():
            return
        time.sleep(0.02)
    raise AssertionError("the tree never settled")


def _chat(db: Path) -> str:
    return conversations.start(db, "build the thing")["id"]


def test_one_slot_runs_three_agents_one_at_a_time(db, machinery):
    lock = threading.Lock()
    now = {"running": 0, "most": 0}
    ran: list[str] = []

    def body(conversation_id, opening):
        if not agents.is_agent(conversation_id):
            return "Noted."
        with lock:
            now["running"] += 1
            now["most"] = max(now["most"], now["running"])
        time.sleep(0.05)
        with lock:
            now["running"] -= 1
            ran.append(conversation_id)
        return "Done: my part."

    machinery(body, limit=1)
    chat = _chat(db)
    with session_context.working_in(chat):
        started = agents.start(chat, [{"name": name, "task": f"do {name}"} for name in ("a", "b", "c")])

    ids = [one["id"] for one in started["agents"]]
    _settle(lambda: all(agents.row(one)["agent_state"] == agents.DONE for one in ids))

    assert sorted(ran) == sorted(ids)
    assert now["most"] == 1, "more than one agent turn ran at once with one slot"


def test_a_handler_waiting_on_its_agent_gives_it_the_slot(db, machinery):
    waited: dict = {}

    def body(conversation_id, opening):
        found = agents.row(conversation_id) or {}
        if not found.get("parent_id"):
            return "Noted."
        if found.get("agent_name") == "lead" and "Your task" in opening:
            agents.start(
                conversation_id, [{"name": "worker", "task": "write the file", "workspace": "shared"}]
            )
            started = time.monotonic()
            waited["result"] = agents.wait_for(conversation_id, minutes=0.5)
            waited["seconds"] = time.monotonic() - started
            return "Waiting for worker."
        if found.get("agent_name") == "worker":
            (Path(paths.base_dir()) / "done.txt").write_text("done\n")
            return "Done: wrote done.txt."
        return "Merged worker's work."

    machinery(body, limit=1)
    chat = _chat(db)
    with session_context.working_in(chat):
        agents.start(chat, [{"name": "lead", "task": "get it written"}])

    _settle(lambda: "result" in waited and agents.find_child(chat, "lead")["agent_state"] == agents.DONE)

    assert "arrived" in waited["result"], f"the wait timed out instead of hearing back: {waited['result']}"
    assert waited["seconds"] < 10
