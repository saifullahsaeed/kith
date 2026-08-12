"""Tasks, and working one in depth: thread, checklist, deliverables."""

from __future__ import annotations

from pathlib import Path

from kith.domain import stall
from kith.domain.enums import TASK_ACTIVE, TASK_PRIORITIES, TASK_SETTLED, TASK_STATUSES
from kith.infra.db import repositories as repo
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import INT, STR
from kith.tools.registry import tool


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
    from kith.services import session_context

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
    from kith.services import session_context

    reason = session_context.foreign_project(path, project_id)
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
    from kith.services import session_context

    session_context.adopt(path, (out or {}).get("project_id"))
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


_VERIFY_MIN_BRIEF = 80

#: Shortest description that counts as a real "definition of done" on a task that belongs to a
#: project or milestone. Trivial standalone errands need none, the same way _verify_done only
#: gates a task that carries a written brief.
_MIN_DONE_CHARS = 24


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
        from kith.services import project_files

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


@tool(
    "add_task",
    "Record a task to pursue — a real unit of work, and an OUTCOME, not an activity. A task is "
    "the thing produced (\"the seed script runs and loads fixtures\"), with a 'description' saying "
    "how you'll KNOW it's done — ideally something runnable (a command that exits 0, a test that "
    'passes, a file that exists). "Verify/inspect/consolidate X" is a done-condition, not a task '
    "of its own. A task under a project or milestone MUST carry such a description. 'priority' is "
    "high for what matters most. Every task starts in 'planning' and nothing is pickable until "
    "your person has approved a plan for it — draft one with the planning-a-task skill, with the "
    "whole checklist written, then hand it over. There are no due dates: a date you set yourself "
    "is not a deadline anyone agreed to.",
    {
        "goal": {**STR, "description": "Short title — the outcome, not a verb like 'verify X'."},
        "description": {
            **STR,
            "description": "How you'll know it's done — required for a task under a project or "
            "milestone; a runnable check beats prose.",
        },
        "priority": {**STR, "enum": list(TASK_PRIORITIES), "description": "low | normal | high."},
        "status": {**STR, "enum": list(TASK_STATUSES), "description": "Defaults to 'planning'."},
        "project_id": {**INT, "description": "Optional: the project this task belongs to."},
        "milestone_id": {
            **INT,
            "description": "Optional: the milestone this task delivers (it inherits that milestone's project).",
        },
    },
    required=("goal",),
)
def add_task(path: Path, args: dict):
    from kith.services import session_context, tuning

    goal = (args.get("goal") or "").strip()
    description = (args.get("description") or "").strip()
    project_id = args.get("project_id")
    milestone_id = args.get("milestone_id")
    # A milestone carries its project, so resolve before checking scope — otherwise filing into
    # another project by naming only its milestone walks straight past the guard.
    if milestone_id and not project_id:
        milestone = repo.projects.get_milestone(path, int(milestone_id))
        if milestone:
            project_id = milestone.get("project_id")
    foreign = _out_of_scope(path, project_id)
    if foreign is not None:
        return foreign
    scoped = bool(project_id or milestone_id)

    # A task that belongs to real work needs a checkable finish line. Without one, "Verify the
    # Prisma foundation" can never be objectively done, so it grinds forever. Trivial standalone
    # errands are left alone — the same proportionality _verify_done uses on the closing side.
    if scoped and len(description) < _MIN_DONE_CHARS:
        return {
            "ok": False,
            "error": (
                "This task belongs to real work but has no checkable finish line. Add a "
                "'description' saying how you'll KNOW it's done — ideally something runnable (a "
                "command that exits 0, a test that passes, a file that exists), not a bare verb "
                "like 'verify' or 'inspect'. Then file it again."
            ),
        }

    # Don't file a second copy of work already open. The 106≈110 / 107≈111 duplicates were the
    # loop re-decomposing the same stuck milestone; merge into the existing task rather than grow
    # the pile. Scoped to the same project so unrelated look-alikes aren't collapsed.
    new_sig = stall.signature(f"{goal} {description}")
    for existing in repo.tasks.list_tasks(path):
        if existing.get("status") in TASK_SETTLED or existing.get("project_id") != project_id:
            continue
        prior = stall.signature(f"{existing['goal']} {existing.get('description') or ''}")
        if stall.similar(new_sig, prior):
            return {
                "ok": True,
                "id": existing["id"],
                "duplicate": True,
                "note": f"Merged into existing open task #{existing['id']} — near-identical goal.",
            }

    # One milestone, a handful of concrete tasks — not the whole roadmap at once. The breakdown
    # prompt says "one sitting each", but a prompt limit is advisory, so enforce it here: nine
    # overlapping tasks under one milestone is exactly what this stops.
    if milestone_id:
        cap = int(tuning.value("milestone_task_cap"))
        open_here = sum(
            1
            for t in repo.tasks.list_tasks(path)
            if t.get("milestone_id") == milestone_id and t.get("status") not in TASK_SETTLED
        )
        if open_here >= cap:
            return {
                "ok": False,
                "error": (
                    f"This milestone already has {open_here} open task(s) — finish or drop some "
                    "before adding more. Plan one milestone shallowly, not all of it at once."
                ),
            }

    # Everything starts in `planning`, milestone or not. The old split — a standalone errand
    # landing straight in a ready-to-pick column — has nowhere to go, because nothing is pickable
    # until a person has approved a plan for it. The problem the separate backlog originally
    # solved (a roadmap being laid out is not started halfway through writing it) is now true of
    # every task by construction rather than a special case for the ones under a milestone.
    status = args.get("status") or "planning"
    # A task cannot be born approved, and the reason is stronger than "the gate would refuse it":
    # the plan lives at `.kith/work/task-<id>.md`, and the id does not exist until this row does.
    # There is no order of operations in which a brand-new task already has an approved plan, so
    # `approved` here is always a mistake — and left unhandled it was a way straight past
    # `_verify_approvable`, which only guards `update_task`.
    #
    # Filed rather than refused: the work is still wanted, it just starts where everything starts.
    born_approved = status == "approved"
    if born_approved:
        status = "planning"
    made = repo.tasks.add_task(
        path,
        goal,
        args.get("priority") or "normal",
        description,
        status,
        "kith",
        project_id,
        milestone_id,
    )
    # Filing work under a project is working on that project. Read back off the row rather than
    # off the arguments, because a task given only a milestone still lands in a project — the
    # repository resolves it — and a session that laid out a roadmap this way would otherwise be
    # bound to nothing.
    session_context.adopt(path, made.get("project_id"))
    _mirror_brief(path, made.get("id"))
    # And something actionable is a reason to get on with it. The loop only wakes for a session
    # that is working, so before this a task filed in a conversation sat there until someone
    # found the button — you asked for a thing, he wrote it down, and you both waited.
    if status in TASK_ACTIVE:
        reopened = _reopen_if_finished(path, made.get("project_id"))
        if reopened:
            return {**made, "note": reopened}
    if born_approved:
        return {
            **made,
            "note": (
                "Filed in 'planning', not 'approved' — a task cannot start approved, because its "
                "plan lives at .kith/work/task-"
                f"{made.get('id')}.md and there was no id to write it against until now. Write the "
                "plan and the whole checklist, then set 'planning' for them to look at."
            ),
        }
    return made


@tool(
    "list_tasks",
    "List your tasks (highest priority first), optionally filtered by column. "
    "Returns one page — check 'more' before assuming you've seen them all.",
    {"status": {**STR, "enum": list(TASK_STATUSES)}, **PAGE_PARAMS},
    required=(),
)
def list_tasks(path: Path, args: dict):
    return paging.page(repo.tasks.list_tasks(path, args.get("status")), args)


@tool(
    "update_task",
    "Update a task — move it between planning/approved/working/done/dropped, or change its goal, "
    "description, priority, or which project/milestone it belongs to. Every task starts in "
    "'planning' while you draft its plan. 'approved' is NOT yours to set: your person approves a "
    "plan in chat, and this refuses it without a plan file and a checklist. Move it to 'working' "
    "when you start, 'done' when it is genuinely finished, 'dropped' if you are giving up. "
    "There is no column meaning 'your turn' — if you are blocked or want something confirmed, use "
    "`ask` in chat, which waits for the answer. Marking the last task of a milestone 'done' "
    "auto-completes that milestone, and completing all of a project's milestones auto-completes "
    "the project — so just keep tasks honest. Once a task is 'done' or 'dropped', leave it alone. "
    "Moving a task with a written brief to 'done' REQUIRES `verification` — see below.",
    {
        "id": INT,
        "status": {**STR, "enum": list(TASK_STATUSES)},
        "goal": STR,
        "description": STR,
        "priority": {**STR, "enum": list(TASK_PRIORITIES)},
        "project_id": {**INT, "description": "Move it under this project (or omit)."},
        "milestone_id": {**INT, "description": "Link it to this milestone (or omit)."},
        "verification": {
            "type": "array",
            "description": (
                "Required to set status='done' on a task that has a written brief. Re-read that "
                "brief and add ONE entry per separate thing it asked for — not a summary of what "
                "you did. Mark met=false for anything you didn't deliver; that hands the task "
                "back to your person instead of closing it, which is the honest outcome."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {**STR, "description": "One thing the brief asked for, in its words."},
                    "met": {"type": "boolean", "description": "Did you actually deliver this? Be honest."},
                    "evidence": {
                        **STR,
                        "description": (
                            "How you know — the file, the row count, the run you did. If not met, why not. "
                            "Restating the requirement is not evidence."
                        ),
                    },
                },
                "required": ["requirement", "met", "evidence"],
            },
        },
    },
    required=("id",),
)
def update_task(path: Path, args: dict):
    return _update_task(path, args)


@tool(
    "view_task",
    "Open a task in full — its description, checklist, comment thread, and deliverables.",
    {"id": INT},
    required=("id",),
)
def view_task(path: Path, args: dict):
    return repo.tasks.task_detail(path, args["id"]) or {"note": "No such task."}


@tool(
    "add_checklist_item",
    "Add a sub-step to a task's checklist — break the work into concrete steps.",
    {"id": {**INT, "description": "The task id."}, "text": STR},
    required=("id", "text"),
)
def add_checklist_item(path: Path, args: dict):
    made = repo.tasks.add_checklist_item(path, args["id"], args["text"])
    _mirror_brief(path, args["id"])
    return made


@tool(
    "check_item",
    "Check a checklist item off (or back on) as you complete steps.",
    {"item_id": INT, "done": {"type": "boolean", "description": "Defaults to true."}},
    required=("item_id",),
)
def check_item(path: Path, args: dict):
    done = repo.tasks.set_checklist_item(path, args["item_id"], args.get("done", True))
    _mirror_brief(path, (done or {}).get("task_id"))
    return done


@tool(
    "add_deliverable",
    "Attach a deliverable to a task — the actual output it produced, so your person "
    "can collect it. Use kind 'file' with a path in your sandbox for a document you "
    "wrote, 'link' for a URL, or 'text' for a written result. Do this when you finish "
    "real work.",
    {
        "id": {**INT, "description": "The task id."},
        "kind": {**STR, "enum": ["text", "file", "link"]},
        "title": STR,
        "content": {**STR, "description": "The text, a sandbox file path, or a URL."},
    },
    required=("id", "title", "content"),
)
def add_deliverable(path: Path, args: dict):
    saved = repo.tasks.add_deliverable(
        path, args["id"], args.get("kind") or "text", args["title"], args["content"]
    )
    _mirror_brief(path, args["id"])
    # Something finished and collectable is worth knowing about — it is the whole point of
    # having handed him the work. Notified separately from the task's own comments, which is
    # why this is here rather than left to whatever note he happens to write alongside it.
    detail = repo.tasks.task_detail(path, args["id"])
    goal = (detail or {}).get("goal") or f"task #{args['id']}"
    repo.messages.add_message(
        path,
        f"Finished something on “{goal}”: {args['title']}",
        link=f"/tasks/{args['id']}",
        kind="delivered",
    )
    return saved
