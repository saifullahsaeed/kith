"""Tasks, and working one in depth: thread, checklist, deliverables."""

from __future__ import annotations

from pathlib import Path

from kith.domain import stall
from kith.domain.enums import TASK_ACTIVE, TASK_PRIORITIES, TASK_SETTLED, TASK_STATUSES
from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.services import board_sync, project_binding
from kith.services.tasks import (
    FILED_THIS_TURN,
    _mirror_brief,
    _out_of_scope,
    _reopen_if_finished,
    _update_task,
)
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import INT, STR
from kith.tools.registry import tool

#: Key in `session_context.turn_notes()` holding the ids of tasks filed during this turn.
#:
#: A set rather than a flag because a turn can file several, and approval is asked about one at
#: a time. Lives in the turn's scratch rather than on the row: "was this filed a moment ago"
#: is a fact about *now*, and a column would still say yes tomorrow.


#: Shortest description that counts as a real "definition of done" on a task that belongs to a
#: project or milestone. Trivial standalone errands need none, the same way _verify_done only
#: gates a task that carries a written brief.
_MIN_DONE_CHARS = 24


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
    from kith.services import tuning

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
        if stall.similar(new_sig, prior, threshold=tuning.value("prose_match")):
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
    project_binding.adopt(path, made.get("project_id"))
    # Remembered so `_verify_approvable` can refuse to approve it in the same breath — see the
    # note there. Written here rather than inferred from `created_at` because the question is
    # "was this filed in *this* turn", and a timestamp comparison would need a turn-start time
    # nothing records and would answer differently on a slow turn.
    session_context.turn_notes().setdefault(FILED_THIS_TURN, set()).add(made.get("id"))
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
    out = paging.page(repo.tasks.list_tasks(path, args.get("status")), args)
    waiting = board_sync.waiting_here(path)
    if waiting:
        out["from_the_folder"] = waiting
    return out


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
