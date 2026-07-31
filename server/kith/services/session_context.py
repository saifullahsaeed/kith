"""Which conversation the work happening right now belongs to.

A tool handler is called as ``run(path, args)`` and has always been told nothing about
who asked. That was fine while everything was global — one board, one roam switch, one
project — and stopped being fine the moment a session became the unit of work. When he
starts a project mid-conversation, *that* conversation is the one now working on it, and
there is no way to know that from ``(path, args)``.

Threading a ``conversation_id`` parameter through fifty-nine handlers to be read by two of
them would be the wrong trade. A context variable is: it is set once at the edge — the chat
route, or the tick advancing a session — read by whoever cares, and empty everywhere else,
which is the honest answer for a tool called from a test or a script.

Per-thread by construction, so two chats streaming at once cannot see each other's session.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_current: ContextVar[str] = ContextVar("kith_conversation", default="")


def current() -> str:
    """The conversation this work belongs to, or "" when nothing claims it."""
    return _current.get()


@contextmanager
def working_in(conversation_id: str) -> Iterator[None]:
    """Run a block as work belonging to one conversation."""
    token = _current.set(str(conversation_id or ""))
    try:
        yield
    finally:
        _current.reset(token)


def adopt(path: Path, project_id: int | None) -> None:
    """This session is working on that project now.

    Derived from what he does rather than declared, deliberately. The alternative was a
    `work_on(project)` tool, and a tool he has to remember to call is a tool that will be
    missed — which is the failure mode written up half a dozen times in this codebase. So
    every write against a project binds the session that made it: create one, link a folder,
    add a milestone, move a task. The session is working on the last project it touched,
    which is both true and impossible to forget to say.

    Silent when nothing is bound: a tool called from a test, a script, or a tick with no
    session has no conversation to adopt anything, and that is not an error.
    """
    conversation_id = current()
    if not conversation_id or not project_id:
        return
    from kith.infra.db import repositories as repo

    try:
        repo.conversations.set_project(path, conversation_id, int(project_id))
    except Exception:
        # Bookkeeping. A session that fails to record what it is working on must not take
        # down the work itself.
        pass
