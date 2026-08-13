"""Which project a conversation is locked to, and who may write to it.

The half of session context that needs storage. `kernel/session_context.py` holds the
context variables themselves — ambient, per-thread, answerable by nobody — and these three
resolve a project through the repositories, which is a different thing living at a different
layer.

Kept together rather than folded into the repositories: `adopt` is a *policy* about when a
binding may move, not a write. Its whole substance is the refusal.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.kernel.session_context import current


def adopt(path: Path, project_id: int | None, deliberate: bool = False) -> None:
    """This session is working on that project now — unless it already has one.

    Derived from what he does rather than declared, deliberately. The alternative was a
    `work_on(project)` tool, and a tool he has to remember to call is a tool that will be
    missed — which is the failure mode written up half a dozen times in this codebase. So a
    write against a project binds the session that made it: the session is working on the
    project it touched, which is both true and impossible to forget to say.

    **A binding that already exists is not moved.** "The last project it touched" was too
    loose, and the way it failed was specific: a session finished the last task on the project
    it was hired for, went looking, updated a task on a *different* project, and was silently
    reassigned there. Nothing was refused and nothing was said, so from outside it looked like
    he had decided to change the subject — which is the one thing a session bound to a project
    is supposed to make impossible. `_in_scope` confines what a bound session may pick up, and
    that confinement is worth nothing if any tool call can move the binding.

    ``deliberate`` is the exception, for the two acts that *are* a statement about this
    session: starting a project here, and pointing one at its folder. Touching another
    project's task is bookkeeping and should leave the session where it is.

    The person's own choice in the interface does not come through here at all — it sets the
    conversation's project directly, through the same repository call this makes, which now
    refuses to move a binding either way. A conversation is stuck with the first project it
    picks, however that happened; wanting a different one is what a new conversation is for.

    Silent when nothing is bound: a tool called from a test, a script, or a turn with no
    session has no conversation to adopt anything, and that is not an error. Also silent
    when the binding is refused as already-locked — `deliberate=True` still asks, but asking
    is not the same as it being granted.
    """
    conversation_id = current()
    if not conversation_id or not project_id:
        return
    try:
        if not deliberate:
            already = repo.conversations.project_of(path, conversation_id)
            if already and int(already) != int(project_id):
                return
        repo.conversations.set_project(path, conversation_id, int(project_id))
    except Exception:
        # Bookkeeping. A session that fails to record what it is working on must not take
        # down the work itself.
        pass


def bound_project(path: Path) -> int | None:
    """The project this *conversation* is locked to, or None.

    Not :func:`current_project`, which is narrower and answers a different question: that one is the
    project of the task in hand, set by `working_on`, and is None in an ordinary chat turn. This one
    is the binding — the thing `adopt` writes and refuses to move.
    """
    conversation_id = current()
    if not conversation_id:
        return None
    try:
        bound = repo.conversations.project_of(path, conversation_id)
    except Exception:
        return None
    return int(bound) if bound else None


def foreign_project(path: Path, project_id: int | None) -> str:
    """Why this conversation may not write to that project, or "" if it may.

    The other half of :func:`adopt`, and the half that was missing. `adopt` goes to real trouble to
    keep a binding from moving — a session that wandered onto another project's task was "silently
    reassigned there" — and its docstring says the point of that is `_in_scope`, which "confines what
    a bound session may pick up". `_in_scope` was a method on the autonomy runner and went with the
    self-directed loop on 2026-08-08, so the binding became a fact nothing read. Chat was never
    confined at all, and the board is the receipt: nine projects, six on one folder, two of those
    active, one called `placeholder`.

    Three ways this is not a violation, and each matters:

    * **No conversation.** A script, a test, a reminder firing — nothing to be foreign to.
    * **No binding yet.** The first write is what binds the session, so it cannot be out of scope.
    * **No project on the thing being written.** A one-off errand belongs to nobody's project.

    Reads never consult this. Looking at another project is normal and often necessary; writing to
    one is what changes the subject.
    """
    if not project_id:
        return ""
    try:
        bound = bound_project(path)
        if not bound or int(bound) == int(project_id):
            return ""
        mine = repo.projects.get_project(path, int(bound)) or {}
        theirs = repo.projects.get_project(path, int(project_id)) or {}
    except Exception:
        # Same reasoning as `adopt`: a lookup that fails must not decide the answer. Allowing the
        # write is the safe failure — refusing on a database hiccup would block real work.
        return ""
    return (
        f"This conversation is working on {mine.get('name') or f'project #{bound}'}, and that "
        f"belongs to {theirs.get('name') or f'project #{project_id}'}. A conversation stays with "
        "the project it started on, so this needs its own conversation — say so rather than "
        "working around it."
    )
