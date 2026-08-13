"""Which conversation the work happening right now belongs to.

A tool handler is called as ``run(path, args)`` and has always been told nothing about
who asked. That was fine while everything was global — one board, one switch, one
project — and stopped being fine the moment a session became the unit of work. When he
starts a project mid-conversation, *that* conversation is the one now working on it, and
there is no way to know that from ``(path, args)``.

Threading a ``conversation_id`` parameter through fifty-nine handlers to be read by two of
them would be the wrong trade. A context variable is: it is set once at the edge — the chat
route, or the scheduler continuing one — read by whoever cares, and empty everywhere else,
which is the honest answer for a tool called from a test or a script.

Per-thread by construction, so two chats streaming at once cannot see each other's session.

**In the kernel because there is nothing behind it.** Every layer asks who is working —
`infra/workspace/paths.py` decides where a relative path lands by it, `checkpoints` gates real
git commits on it — and no layer can be handed the answer, because the code that reads it is
fifty frames below the code that set it. That is what makes it ambient rather than a parameter.

The three functions that resolved a project through the repositories are **not** here; they
need storage, so they live in `services/project_binding.py`. Splitting them out is what let the
rest of this module stop being importable-only-from-inside-a-function.

**A `ContextVar` is identified by object, not by name.** Two built from the string
`"kith_conversation"` are different variables, and reading the wrong one returns the default —
which here is `""`, this module's documented legitimate answer for "nothing claims this work".
A half-finished move of this file is therefore silent rather than loud, which is why it moved
in one commit with no re-export left behind in `services/`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[str] = ContextVar("kith_conversation", default="")
_project: ContextVar[int | None] = ContextVar("kith_project", default=None)


def current() -> str:
    """The conversation this work belongs to, or "" when nothing claims it."""
    return _current.get()


def current_project() -> int | None:
    """The project this work is on, when it is narrower than the conversation's.

    He picks up a task, and that task belongs to a project — which is not necessarily the
    project its *session* is bound to, and for an unbound session is not any project at all.
    Everything downstream that asks "where am I working" used to resolve it through the
    conversation, so working a task in a folder-linked project got the folder of
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


def in_turn() -> bool:
    """Is this call happening inside a real turn — distinct from `turn_notes()`, which
    deliberately collapses "no turn at all" and "a turn that has recorded nothing yet" to
    the same empty dict, because its one caller does not care which.

    Checkpointing does care: a test or script driving a write/edit/shell function directly,
    with no `a_turn()` around it, is not Kith working, and must not silently start creating
    real git commits on every such call with no memoization to make it cheap.
    """
    return _scratch.get() is not None


#: Whether this work is happening with nobody watching. False in chat, in a test, in a script —
#: the honest default, since the only thing that can truthfully claim otherwise is the scheduler.
_unattended: ContextVar[bool] = ContextVar("kith_unattended", default=False)


def unattended() -> bool:
    """Is this running with nobody present — a reminder firing — rather than someone talking?

    Exists for one decision: who may declare a task finished. Verifying its own work and
    then closing the task is marking its own homework — it wrote the brief, it wrote the evidence,
    and it graded itself. In a chat turn there is a person on the other side and a full context to
    judge from, so `done` there means what it says.

    Read from a context variable rather than threaded through, for the same reason
    :func:`current` is: a tool handler is called as ``run(path, args)`` and fifty-nine of them do
    not care.
    """
    return _unattended.get()


@contextmanager
def nobody_watching() -> Iterator[None]:
    """Run a block as work nobody is watching. Opened by the scheduler, and nothing else."""
    token = _unattended.set(True)
    try:
        yield
    finally:
        _unattended.reset(token)


@contextmanager
def working_on(project_id: int | None) -> Iterator[None]:
    """Run a block as work on one project, whatever the conversation says.

    Nests inside ``working_in``: the conversation is decided when the work starts, the
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
