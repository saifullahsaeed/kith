"""A conversation is bound to its project on the message that opens it, or not at all.

This is the rule `services.conversations.set_project` enforces — a session is stuck with the
project it picked, and wanting a different one is what a new conversation is for — and it is
the rule the web client broke for months. It wrote the binding over a *second* request, after
the stream had reported the id, which meant the first turn of every chat ran unbound: no
project memory, no plan, no board, and the cross-project listing in their place. Fifty-four of
sixty-six conversations on that machine never acquired a binding at all.

The CLI has exactly the same opportunity to get it wrong, so the body it sends is asserted
rather than read.
"""

from __future__ import annotations

import json

import pytest

from kith.cli import context
from kith.cli.client import Client
from kith.cli.commands import chat


class FakeClient(Client):
    """Records what was asked of it and answers from a fixture. No sockets.

    A real subclass rather than a lookalike, and the reason is the one thing a fake can get
    wrong that nothing would catch: drifting from the interface it stands in for. Inheriting
    means a method renamed on `Client` fails here at the type check rather than in six months
    when somebody runs the command. `__init__` is deliberately not called — building a real
    one reads the token file and would make this suite depend on a Kith being installed.
    """

    def __init__(self, projects=(), conversations=()):
        self._projects = list(projects)
        self._conversations = list(conversations)
        self.streamed: list[tuple[str, dict]] = []

    def get(self, path, **params):
        if path == "/projects":
            return {"projects": self._projects}
        if path == "/conversations":
            return {"conversations": self._conversations}
        raise AssertionError(f"unexpected GET {path}")

    def stream(self, path, body=None):
        self.streamed.append((path, body or {}))
        for event in (
            {"type": "conversation", "id": "20260916-000000000-newone"},
            {"type": "delta", "role": "text", "text": "ok"},
            {"type": "done"},
        ):
            yield json.dumps(event) + "\n", event


class Args:
    def __init__(self, **kwargs):
        self.message = kwargs.pop("message", ["hello"])
        self.conversation = kwargs.pop("conversation", "")
        self.new = kwargs.pop("new", False)
        self.project = kwargs.pop("project", "")
        self.json = True  # passthrough; keeps the assertions about the body, not the prose
        self.quiet = False
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(context, "STATE_PATH", tmp_path / "cli" / "sessions.json")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    return repo


def _project(identifier, name, directory):
    return {"id": identifier, "name": name, "directory": str(directory), "status": "active"}


def test_a_new_conversation_carries_its_project_in_the_first_message(isolated):
    client = FakeClient(projects=[_project(3, "Repo", isolated)])
    chat._send(client, Args())

    path, body = client.streamed[0]
    assert path == "/chat"
    assert body["projectId"] == 3, "the opening turn must be built with the project already bound"
    assert "conversationId" not in body, "the server allocates the id; inventing one guesses its format"


def test_a_resumed_conversation_never_sends_a_project_again(isolated):
    """Sending it on a resume is at best ignored and at worst misleading in a request log —
    `set_project` refuses to move a binding, so it can only ever be noise."""
    client = FakeClient(
        projects=[_project(3, "Repo", isolated)],
        conversations=[{"id": "20260916-111111111-old", "projectId": 3}],
    )
    context.remember(context.project_root(), "20260916-111111111-old", 3)

    chat._send(client, Args())
    _path, body = client.streamed[0]
    assert body["conversationId"] == "20260916-111111111-old"
    assert "projectId" not in body


def test_the_conversation_the_server_opened_is_remembered(isolated):
    client = FakeClient(projects=[_project(3, "Repo", isolated)])
    chat._send(client, Args())
    assert context.remembered(context.project_root())["conversationId"] == "20260916-000000000-newone"


def test_a_remembered_conversation_the_server_has_forgotten_is_dropped(isolated):
    """A conversation deleted in the app leaves a stale local pointer. Sending to a dead id
    would open a *new* conversation server-side under that pointer — the message would land
    somewhere real, and nothing on screen would say where."""
    client = FakeClient(projects=[_project(3, "Repo", isolated)], conversations=[])
    context.remember(context.project_root(), "20260916-999999999-gone", 3)

    chat._send(client, Args())
    _path, body = client.streamed[0]
    assert "conversationId" not in body, "a vanished conversation must not be resumed blind"
    assert body["projectId"] == 3
    assert context.remembered(context.project_root())["conversationId"] == "20260916-000000000-newone"


def test_new_forces_a_fresh_conversation_even_when_one_is_remembered(isolated):
    client = FakeClient(
        projects=[_project(3, "Repo", isolated)],
        conversations=[{"id": "20260916-111111111-old", "projectId": 3}],
    )
    context.remember(context.project_root(), "20260916-111111111-old", 3)

    chat._send(client, Args(new=True))
    _path, body = client.streamed[0]
    assert "conversationId" not in body
    assert body["projectId"] == 3


def test_an_explicit_project_overrides_the_directory(isolated):
    client = FakeClient(
        projects=[_project(3, "Repo", isolated), _project(9, "Elsewhere", isolated.parent / "other")]
    )
    chat._send(client, Args(new=True, project="Elsewhere"))
    _path, body = client.streamed[0]
    assert body["projectId"] == 9


def test_a_directory_in_no_project_still_sends(isolated):
    """Unbound is a state the server already handles. Refusing here would make the CLI unusable
    anywhere outside a registered project, which is most of the filesystem."""
    client = FakeClient(projects=[])
    chat._send(client, Args())
    _path, body = client.streamed[0]
    assert "projectId" not in body
    assert body["messages"][0]["content"] == "hello"
