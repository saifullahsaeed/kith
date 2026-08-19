"""The rules about a task, separately from the tool that exposes them.

`tools/tasks.py` is an adapter — it parses arguments, delegates, and serialises — and it had
grown seven functions that decide things: whether a task may be called done, whether a plan may
be approved, what a status change cascades to, whether this conversation may touch that
project. Those are the rules, and they were in the adapter because the adapter was the only
caller until the control panel became a second one.

Two of them were already being copied rather than shared. `_out_of_scope` existed verbatim in
`tools/projects.py` as well, which is what a rule with no home looks like just before it
drifts.

What is still in the repository below this is storage. What is in the tool above it is
translation. This is the part in between, and the reason `services/` had no task module until
now was not that the rules did not exist — only that nobody had put them anywhere.
"""

from __future__ import annotations

from pathlib import Path

from kith.domain.enums import TASK_ACTIVE
from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.services import project_binding

#: Where a turn records the tasks it filed, so `_verify_done` can tell "created and closed in
#: one breath" from a task that was actually worked. Read through `session_context.turn_notes`,
#: which is empty outside a turn — the honest answer for a tool called from a script.
FILED_THIS_TURN = "tasks_filed_this_turn"

#: Tasks whose *plan* was written in this turn — today, the tasks a checklist item was added to.
#: Separate from `FILED_THIS_TURN` because they are different doors into the same room and only
#: one of them was locked. See `_verify_approvable`.
PLANNED_THIS_TURN = "tasks_planned_this_turn"

#: How much of a brief has to exist before "done" is believable.
_VERIFY_MIN_BRIEF = 80


def _verify_done(path: Path, a: dict) -> dict | None:
    """Make 'done' mean done. Returns a refusal to hand back, or None to let it through.

    He reports completion he hasn't earned — closing a task with three of five
    requested fields missing, then describing it as complete. The failure isn't
    dishonesty, it's that nothing in the loop ever compared his output back to the
    request. So this does: to close a task that has a written brief he must enumerate
    what was asked and answer per item, with evidence. Anything he marks unmet sends
    the task to his person instead of quietly closing it.

    Only the tool path is gated — his person can still move a task however they like
    from the UI.
    """
    detail = repo.tasks.task_detail(path, a["id"])
    if not detail:
        return None
    brief = (detail.get("description") or "").strip()
    if len(brief) < _VERIFY_MIN_BRIEF:
        return None  # nothing written down to check against

    checks = [c for c in (a.get("verification") or []) if isinstance(c, dict) and c.get("requirement")]
    if not checks:
        unchecked = [i.get("text") for i in (detail.get("checklist") or []) if not i.get("done")]
        return {
            "blocked": "This task has a brief, so it can't be closed without checking your work against it.",
            "what_was_asked": brief,
            "unchecked_checklist_items": unchecked,
            "deliverables_attached": len(detail.get("deliverables") or []),
            "next": (
                "Read what_was_asked again and split it into the separate things it asked for. "
                "Call update_task again with verification=[{requirement, met, evidence}] — one entry "
                "each. Use met=false for anything you didn't deliver; that hands it back rather than "
                "closing it. Evidence is a file, a row count, a run you did — not a restatement."
            ),
        }

    unmet = [c for c in checks if not c.get("met")]
    lines = [
        f"{'✅' if c.get('met') else '❌'} {c.get('requirement')} — {c.get('evidence') or '(no evidence given)'}"
        for c in checks
    ]
    body = "**Checked against the brief**\n" + "\n".join(lines)

    if unmet:
        # He said it himself: something's missing. A refusal, and it has to *be* one.
        #
        # This used to set `a["status"] = "waiting"` and return None, so the reclassification was
        # the refusal: the update went through, the caller was told it had succeeded, and the task
        # landed in a column meaning "your turn". That column is gone, and not for brevity — four
        # tasks once sat in it that nobody knew were waiting, because a tray waits to be noticed.
        # Taking a turn is `ask`, which holds the turn until it is answered.
        return {
            "blocked": (
                f"{len(unmet)} of {len(checks)} requirements not met by your own account, so this "
                "is not done."
            ),
            "not_met": [str(c.get("requirement")) for c in unmet],
            "checked": body,
            "next": (
                "The task stays where it is. Either finish what is missing, or raise it with your "
                "person in chat with `ask` — say what is blocking you and what you want to do. Do "
                "not call this again with the same verification."
            ),
        }

    # Every requirement met — by his own account, with nobody having checked it. That is not
    # a close, it is a submission. `_verify_done` is genuinely good at catching work he knows is
    # incomplete, and structurally incapable of catching work he believes is complete and is not:
    # he wrote the brief, he chose the requirements, he supplied the evidence, and then he graded
    # it. On a real project that produced a finished, confident analysis document asserting the
    # system used SQLite when it had moved to Postgres — every box ticked.
    #
    # So a pass with nobody watching is refused rather than filed. It used to move to `review`,
    # a column he could not pick up again — which worked, and cost a whole status to say
    # "somebody look at this". There is a person in every chat turn now, so the answer is to be
    # asked there instead of queued here.

    if session_context.unattended():
        return {
            "blocked": (
                "Every requirement is met by your own account — and you wrote the brief, chose the "
                "requirements and supplied the evidence, so nobody has actually checked this."
            ),
            "checked": body,
            "next": (
                "Not closable with nobody present. Leave it as it is and raise it in chat when "
                "there is somebody to look: say what you finished and what you want confirmed."
            ),
        }

    return None


def _out_of_scope(path: Path, project_id: int | None) -> dict | None:
    """A refusal when this conversation may not write to that project, or None.

    The message is the whole point. "Not allowed" leaves a model looking for another route to the
    same place; naming both projects and saying what to do instead — start a conversation for it —
    is the difference between pushing back and being obstructive.
    """

    reason = project_binding.foreign_project(path, project_id)
    if not reason:
        return None
    return {"blocked": reason, "next": "Tell them, and work on this conversation's project instead."}


def _verify_approvable(path: Path, task_id: int) -> dict | None:
    """Make approval mean something. Returns a refusal to hand back, or None to let it through.

    Saying yes to a plan is the only decision about a task left to a person, and it is worth
    nothing if the thing being approved is incomplete. It was: asked to plan a task on the first
    real run of this vocabulary, he wrote a good 1,449-character plan, attached it, created no
    checklist items, and `update_task(status='approved')` said yes.

    Nothing was wrong with what he did. `planning-a-task` describes the plan document at length
    and never asks for a checklist — its only two mentions of one are warnings against writing it
    early. So the requirement belongs here and not in the skill: prose can be forgotten, misread,
    or skipped on a task that looks small; a refusal cannot.

    Both halves are required, and the checklist is the half worth insisting on. A plan is prose and
    can describe anything. The checklist is what makes progress legible afterwards — it is what the
    working-task card counts through — and a task approved without one shows a person nothing
    between "started" and "claims to be finished".
    """
    # The half that was missing, and the half that was the whole point.
    #
    # Everything below this checks the plan is *complete*. Nothing checked it was *theirs to
    # approve* — so the refusal text three lines down, "They approve it; you do not", was prose
    # in an error message and nothing enforced it. Measured on task #104: one turn, seven calls
    # — `add_task`, `write_file` for the plan, four `add_checklist_item`, then
    # `update_task(status='approved')`. Both conditions below were satisfied because he had just
    # written both, so it passed, and he reported "I created and approved Task #104" to a person
    # who had not seen it yet. Then he stopped anyway and waited for them, which is the worst of
    # both: their decision taken away and the wait kept.
    #
    # `add_task` already refuses a task *born* approved, and its comment worries about exactly
    # this — "left unhandled it was a way straight past `_verify_approvable`, which only guards
    # `update_task`". Filing and then approving is that same door with a different handle.
    #
    # "Has a person seen it" cannot be read from here, but "has a turn passed" can, and it is the
    # honest proxy: the plan is handed over in chat, so the earliest anyone could have answered is
    # the turn after the one that wrote it. Outside a turn — a test, a script — `turn_notes()` is
    # an empty throwaway and this cannot fire, which is right: there is nobody to have asked.

    # Filed this turn, or *planned* this turn. The second was missing and it is the door that got
    # used. Measured on tasks #110 and #111 on 2026-08-19: both rows already existed, so
    # `FILED_THIS_TURN` did not fire — and in one turn he added their checklists, wrote their
    # plans, and moved each one planning -> approved -> working in three consecutive rounds,
    # then closed them. Three tasks marked done in eight minutes with no file edited.
    #
    # The proxy was right and only half-wired. This function's own note says it: "'Has a person
    # seen it' cannot be read from here, but 'has a turn passed' can, and it is the honest
    # proxy: the plan is handed over in chat, so the earliest anyone could have answered is the
    # turn after the one that wrote it." That applies to the plan, not to the row the plan is
    # attached to. A task filed last week whose checklist was written ninety seconds ago is
    # exactly as unseen as one filed in this turn.
    notes = session_context.turn_notes()
    if int(task_id) in notes.get(PLANNED_THIS_TURN, ()):
        return {
            "blocked": (
                "You wrote this task's plan in this same turn, so nobody has had a chance to "
                "read it yet — the task being older than the plan does not make the plan seen."
            ),
            "next": (
                "Leave it in 'planning' and say the plan and the checklist back to them in chat. "
                "Approving is the one decision about a task that is theirs. If the work is small "
                "and obvious enough not to need any of this, it did not need a plan either: put "
                "it straight into 'working' and do it."
            ),
        }
    if int(task_id) in notes.get(FILED_THIS_TURN, ()):
        return {
            "blocked": "You filed this task in this same turn, so nobody has had a chance to read it yet.",
            "next": (
                "Leave it in 'planning' and say the plan and the checklist back to them in chat. "
                "Approving is the one decision about a task that is theirs — wait for them to say "
                "yes, then move it. If the work is small and obvious enough not to need any of "
                "this, it did not need a plan either: put it straight into 'working' and do it."
            ),
        }

    detail = repo.tasks.task_detail(path, int(task_id))
    if not detail:
        return None
    missing = []
    if not (detail.get("plan") or "").strip():
        missing.append("a plan")
    if not (detail.get("checklist") or []):
        missing.append("a checklist")
    if not missing:
        return None
    return {
        "blocked": f"Nothing to approve yet: this task has no {' and no '.join(missing)}.",
        "next": (
            "Write the plan to `.kith/work/task-<id>.md` and add the checklist with "
            "`add_checklist_item` — every step, before you hand it over, not as you go. Then set "
            "'planning' so they can look at both together. They approve it; you do not."
        ),
    }


def _update_task(path: Path, a: dict) -> dict | None:
    # There is no longer a status that means "your turn", so there is no longer a status change
    # that has to be announced. `waiting` was that status and it was announced from three
    # different places, none of which agreed; the notification that mattered — "I am stuck and
    # you are the only one who can help" — is now `ask`, which holds the turn instead of leaving
    # a card in a tray somebody has to notice. Four tasks once sat in that tray unnoticed.
    existing = repo.tasks.task_detail(path, a["id"]) or {}
    foreign = _out_of_scope(path, existing.get("project_id"))
    if foreign is not None:
        return foreign
    # And you cannot move a task *into* another project either.
    if a.get("project_id"):
        foreign = _out_of_scope(path, a.get("project_id"))
        if foreign is not None:
            return foreign
    requested = (a.get("status") or "").strip()
    if requested == "done":
        refusal = _verify_done(path, a)
        if refusal is not None:
            return refusal
    if requested == "approved":
        refusal = _verify_approvable(path, a["id"])
        if refusal is not None:
            return refusal
    # Milestone linkage also sets the project, so apply it before a bare project set.
    if "milestone_id" in a:
        repo.tasks.set_task_milestone(path, a["id"], a.get("milestone_id"))
    elif "project_id" in a:
        repo.tasks.set_task_project(path, a["id"], a.get("project_id"))
    out = repo.tasks.update_task(
        path,
        a["id"],
        a.get("status"),
        a.get("goal"),
        a.get("priority"),
        a.get("description"),
    )
    # Moving a task along is working on its project, whether or not this call is the one that
    # named it. That covers the ordinary case a create-only rule would miss: picking up a
    # project someone laid out yesterday, where the first thing he touches is a task that
    # already exists.

    project_binding.adopt(path, (out or {}).get("project_id"))
    _mirror_brief(path, (out or {}).get("id"))
    # Approving a plan is the moment work becomes work. This is the other half of everything
    # landing in `planning`: you lay the roadmap out with nothing running, and saying yes to a
    # plan is what says go — rather than a button somewhere else that means the same thing.
    if (a.get("status") or "") in TASK_ACTIVE:
        reopened = _reopen_if_finished(path, (out or {}).get("project_id"))
        if reopened and out:
            return {**out, "note": reopened}
    # Handing a plan over for approval is worth showing in full, not just the status change —
    # otherwise the person reviewing it sees "→ planning" and has to go read the file
    # themselves to find out what they are actually being asked to approve.
    if requested == "planning" and out:
        plan = _plan_doc(path, out.get("id"))
        if plan:
            return {**out, "plan": plan}
    return out


def _reopen_if_finished(path: Path, project_id: int | None) -> str:
    """Filing work into a finished project means it is not finished. Say so, and reopen it.

    Whoever closed it — a person, always, now — nothing then stopped an actionable task being
    added underneath afterwards, where it was **invisible**: `active_tasks` excludes everything
    under a done or paused project, so the board showed two `todo` tasks and every reportted
    "caught up — resting".

    Three symptoms, one cause, and none of them pointed here. The work never started. The
    project picker showed a bare `1` instead of a name, because the interface lists only
    active projects and had nothing to match the id against. And the loop woke on the new
    task, found nothing it was allowed to touch, and went back to sleep — over and over.

    Reopening rather than refusing: the task is the evidence. Someone deciding there is more
    to do is a fact about the project, not a mistake to correct.
    """
    if not project_id:
        return ""
    try:
        row = repo.projects.get_project(path, int(project_id))
        if not row or row.get("status") not in ("done", "paused"):
            return ""
        was = row["status"]
        repo.projects.update_project(path, int(project_id), status="active")
        return f"{row.get('name') or 'That project'} was {was}; there is work in it again, so it is active."
    except Exception:
        return ""


def _mirror_brief(path: Path, task_id: int | None) -> None:
    """Write this task's readable brief into its project's `.kith/tasks/`.

    So the work survives being handed over: someone clones the repository and the briefs are
    there, rather than living only in a SQLite file on whichever machine happened to be
    driving. A record, not a second board — status and ordering stay in the database, which
    is the one writer.

    Silent on every failure. The board is the thing that matters; this is the copy, and a
    copy that cannot be written must not fail the change that prompted it.
    """
    if not task_id:
        return
    try:
        from kith.infra import project_files

        detail = repo.tasks.task_detail(path, int(task_id))
        if not detail:
            return
        project_id = detail.get("project_id")
        if not project_id:
            return  # a one-off errand belongs to nobody's repository
        project = repo.projects.get_project(path, int(project_id))
        directory = str((project or {}).get("directory") or "").strip()
        if directory and Path(directory).is_dir():
            project_files.write_brief(directory, detail)
    except Exception:
        # The task is saved; this is the readable copy of it in the project folder. A brief
        # that could not be written is worth less than a task that could not be created.
        pass


def _plan_doc(path: Path, task_id: int | None) -> str:
    """The plan the planning-a-task skill wrote, if there is one at the convention it names.

    Best effort and silent, the same way `_mirror_brief` is: the skill's `.kith/work/task-<id>.md`
    is prose guidance to the model, not an enforced path, so a plan filed anywhere else just does
    not attach. The status change still goes through either way; this only decides whether the
    approval carries the doc with it.

    Delegates to `task_detail` rather than resolving the path a second time. It had its own copy
    of that logic and the copies had already drifted — this one required a `project_id` and so
    could never find the plan for a standalone task, which every task now is at the point the
    gate applies. That parameter is gone with the duplication that needed it.
    """
    if not task_id:
        return ""
    try:
        detail = repo.tasks.task_detail(path, int(task_id))
        return str((detail or {}).get("plan") or "")
    except Exception:
        return ""
