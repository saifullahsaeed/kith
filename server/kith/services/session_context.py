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
_project: ContextVar[int | None] = ContextVar("kith_project", default=None)


def current() -> str:
    """The conversation this work belongs to, or "" when nothing claims it."""
    return _current.get()


def current_project() -> int | None:
    """The project this work is on, when it is narrower than the conversation's.

    A tick picks a task, and that task belongs to a project — which is not necessarily the
    project its *session* is bound to, and for an unbound session is not any project at all.
    Everything downstream that asks "where am I working" used to resolve it through the
    conversation, so a tick working a task in a folder-linked project got the folder of
    whatever the conversation happened to be bound to, which was usually nothing. See
    :func:`working_on`.
    """
    return _project.get()


@contextmanager
def working_in(conversation_id: str) -> Iterator[None]:
    """Run a block as work belonging to one conversation."""
    token = _current.set(str(conversation_id or ""))
    try:
        yield
    finally:
        _current.reset(token)


#: Scratch space that lives exactly as long as one turn. Empty outside one, which is the
#: honest answer for a tool called from a test or a script.
_scratch: ContextVar[dict | None] = ContextVar("kith_turn_scratch", default=None)


def turn_notes() -> dict:
    """What this turn has already done, for tools that should not do it twice.

    A turn is one `stream_agent` call, and nothing before this could see that boundary: a
    handler is called as `run(path, args)` and has no idea whether it has already run. Which
    is fine for most of them — reading a file twice is cheap and sometimes right — and not
    fine for the one that pulls two thousand tokens of instructions in and leaves them there.

    Returns a throwaway dict outside a turn rather than raising, so a tool that uses this
    still works in a test with nothing set up.
    """
    notes = _scratch.get()
    return notes if notes is not None else {}


@contextmanager
def a_turn() -> Iterator[None]:
    """One turn's scratch space, cleared at the end of it."""
    token = _scratch.set({})
    try:
        yield
    finally:
        _scratch.reset(token)


@contextmanager
def working_on(project_id: int | None) -> Iterator[None]:
    """Run a block as work on one project, whatever the conversation says.

    Nests inside ``working_in``: the conversation is decided when the tick starts, the
    project only once it has looked at the board and picked something. Deliberately *not*
    written to the database — binding a session to a project is a lasting decision with
    real consequences for what it may pick up next (see ``_in_scope``), and picking up one
    task should not silently make that decision on the person's behalf.
    """
    token = _project.set(int(project_id) if project_id else None)
    try:
        yield
    finally:
        _project.reset(token)


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
