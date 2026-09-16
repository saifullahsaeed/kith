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


#: The copy this context is pinned to, and the folder it is a copy *of*: ``(source, copy)``.
#: Empty strings for everything that is not a worker running in its own checkout.
#:
#: **Both halves, and the second one is not decoration.** The first version stored only the
#: copy, and a builder wrote straight into the real repository anyway — because the agent that
#: sent it put the project's absolute path in the objective (its own tool description asks for
#: "any paths you have"), the worker called `write_file` with that absolute path, and
#: `paths.resolve` honours an absolute path as given without ever consulting `base_dir`. The
#: pin was set, correct, and never consulted. Knowing what the copy is a copy *of* is what lets
#: `resolve` recognise such a path and bend it into the copy.
_isolated: ContextVar[tuple[str, str]] = ContextVar("kith_isolated_base", default=("", ""))


def isolated_base() -> str:
    """The folder this context has been pinned to, or "" when nothing has pinned it.

    A string rather than a ``Path`` because a ``ContextVar`` default should be a cheap
    immutable, and because "" is already this module's spelling for "nobody claims this" —
    see :func:`current`. The one caller that wants a path builds one.
    """
    return _isolated.get()[1]


def isolated_mirror() -> tuple[str, str]:
    """``(source, copy)`` for a pinned context, or ``("", "")``.

    Separate from :func:`isolated_base` because the two questions have different callers and
    different answers when only one folder is known. `permissions` asks "may he write here",
    which is about the copy alone; `paths.resolve` asks "is this path really about the copy",
    which cannot be answered without the original.
    """
    return _isolated.get()


#: Where the request came from, when it came from a terminal. Informational only.
_sent_from: ContextVar[str] = ContextVar("kith_sent_from", default="")


def sent_from_directory() -> str:
    """The directory the CLI was run in, or "" for a request from the window."""
    return _sent_from.get()


@contextmanager
def arriving_from(directory: str | None) -> Iterator[None]:
    """Record where a message was typed, for the turn to mention. **Grants nothing.**

    The distinction from :func:`working_from` is the whole reason this exists rather than
    reusing it, and getting it wrong would be a hole rather than a bug. `working_from` names a
    folder he may *write in* — `permissions` reads it, because a worker inside its own worktree
    has no one to ask and must not stop on a prompt. This names a folder somebody *was standing
    in*, sent by a client, and a client must never be able to widen what he is allowed to touch
    by saying where it was typed. So it is read by exactly one thing: the sentence in the
    ambient block that tells him where the message came from.

    It exists because the alternative was worse in a way that showed up the first hour the
    command line was used. The CLI resolves which conversation to continue from the working
    directory and then sent nothing about it, so he was asked a question about "your own repo"
    with no way to know which folder that was — and spent an entire turn searching the disk for
    a path the client had in a variable. The fact was known, and thrown away at the wire.

    Not prepended to the message, which was the other option and is worse for a reason that
    only shows up later: a line added to what somebody typed is indistinguishable from what
    they typed, it is recorded in the transcript as their words, and every later turn reads a
    sentence they never wrote. Ambient facts belong with the clock — rebuilt each turn, never
    stored as speech.
    """
    text = str(directory or "").strip()
    if not text:
        yield
        return
    token = _sent_from.set(text)
    try:
        yield
    finally:
        _sent_from.reset(token)


@contextmanager
def working_from(directory: str | None, mirror_of: str | None = None) -> Iterator[None]:
    """Run a block with every relative path anchored in ``directory``.

    The third of the three, and the only one that names a folder outright. ``working_in``
    says which conversation, ``working_on`` says which project, and both of those are
    *questions* that `paths.base_dir` answers by going and looking something up. This is the
    answer handed over directly, for the case where there is nothing to look up: a worker
    editing inside its own `git worktree` is not on a different project and not in a
    different conversation — it is the same work, in a copy of the same folder.

    That is what makes worker isolation one variable rather than a parameter on fifty-nine
    handlers. Every path in the application goes through `paths.resolve` → `paths.base_dir`,
    so a worker thread that runs in a ``copy_context()`` with this set reads and writes its
    copy through every existing tool, and no tool knows it moved.

    **It grants permission as well as location, and that is deliberate.** `permissions`
    reads this too (see ``_inside_linked_project``), because a folder he may write in is
    exactly what a worktree has to be: a worker has no `ask`, and nobody is watching a
    scratchpad, so a permission prompt raised in here would be asked of an empty room while
    the turn that sent it waits on a tool call that never returns. Pinning a context to a
    folder is therefore a decision only the code that *made* that folder may take — which is
    why this takes a path and not a name, and why nothing reachable by a tool calls it.

    ``mirror_of`` is the folder ``directory`` is a copy of. Optional only so that a test can
    pin a plain folder; a real worktree must always pass it, because without it an absolute
    path into the original is honoured as written and the isolation is silently not there.

    Passing ``None`` or "" is a no-op rather than an error: the caller that would need to
    branch is the one that has no worktree, and "no isolation" is what it already wants.
    """
    text = str(directory or "").strip()
    if not text:
        yield
        return
    token = _isolated.set((str(mirror_of or "").strip(), text))
    try:
        yield
    finally:
        _isolated.reset(token)
