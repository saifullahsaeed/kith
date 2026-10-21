"""A message sent into a conversation that already has a turn running goes into that turn.

On 2026-10-21 the window's stream dropped when Chromium restarted its network service, so the chat
looked idle and "wtf" was posted as a new message. `begin_turn` replaced the running turn's record
and stop switch: the first turn carried on unseen and unstoppable, due to wake beside the second.
The server no longer takes a client's word that nothing is running.
"""

from __future__ import annotations

import json

import pytest

from kith.api.routes import chat as route
from kith.kernel import events, live_turns
from kith.services import steering


@pytest.fixture(autouse=True)
def clean():
    live_turns._LIVE.clear()
    steering.forget_everything()
    yield
    live_turns._LIVE.clear()
    steering.forget_everything()
    events.log._subscribers.clear()


@pytest.fixture
def client():
    from kith import settings
    from kith.api import auth
    from kith.app import create_app

    made = create_app()
    made.config["TESTING"] = True
    given = made.test_client()
    given.environ_base[f"HTTP_{auth.HEADER.upper().replace('-', '_')}"] = auth.token(settings.DATA_DIR)
    return given


def _send(client, conversation_id: str, text: str):
    return client.post(
        "/api/chat",
        json={"conversationId": conversation_id, "messages": [{"role": "user", "content": text}]},
        buffered=False,
    )


def test_it_is_steered_into_the_running_turn_and_no_second_turn_starts(client, monkeypatch):
    begun: list = []
    monkeypatch.setattr(route, "begin_turn", lambda *a, **k: begun.append(a))
    running = live_turns.begin("c-busy")
    live_turns.publish(running, json.dumps({"type": "delta", "role": "text", "text": "working on it"}) + "\n")

    answer = _send(client, "c-busy", "wtf")
    lines = []
    for chunk in answer.response:
        lines.extend(one for one in chunk.decode().splitlines() if one.strip())
        if len(lines) >= 2:
            break
    answer.close()

    assert begun == [], "a second turn was started on top of the running one"
    assert steering.pending("c-busy") == ["wtf"]
    assert answer.headers["X-Kith-Turn"] == running.id
    assert json.loads(lines[0]) == {"type": "conversation", "id": "c-busy"}
    assert json.loads(lines[1])["text"] == "working on it", "the reply is the running turn, from its start"
    live_turns.finish(running)


def test_with_nothing_running_a_turn_starts_as_before(client, monkeypatch):
    begun: list = []

    def begin(*args, **kwargs):
        begun.append(args)
        live = live_turns.begin(args[0])
        live_turns.finish(live)
        return live

    monkeypatch.setattr(route, "begin_turn", begin)
    _send(client, "c-idle", "hello").close()

    assert len(begun) == 1
    assert steering.pending("c-idle") == []
