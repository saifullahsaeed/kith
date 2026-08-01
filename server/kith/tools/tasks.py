"""Tasks, and working one in depth: thread, checklist, deliverables."""

from __future__ import annotations

from pathlib import Path

from kith.domain import stall
from kith.domain.enums import TASK_PRIORITIES, TASK_STATUSES
from kith.infra.db import repositories as repo
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import INT, STR
from kith.tools.registry import tool


def _ask_on_task(path: Path, a: dict) -> dict:
    task_id = a["id"]
    question = (a.get("question") or "").strip()
    if not question:
        raise ValueError("question is required")
    comment = repo.tasks.add_task_comment(path, task_id, "kith", question)
    repo.tasks.update_task(path, task_id, status="waiting")
    task = repo.tasks.task_detail(path, task_id)
    goal = task["goal"] if task else f"task #{task_id}"
    # A notification so they know to come answer on the task — clickable to it.
    repo.messages.add_message(
        path,
        f"I need your input on “{goal}” (task #{task_id}): {question}",
        link=f"/tasks/{task_id}",
        kind="asked",
    )
    return {
        "asked": True,
        "task_id": task_id,
        "comment_id": comment["id"],
        "note": "Moved to Waiting on you; they've been notified.",
    }


def _comment_on_task(path: Path, a: dict) -> dict:
    """Post a comment on a task and raise a clickable notification, so a note left
    on a task actually reaches the person (this is how he keeps me in the loop)."""
    task_id = a["id"]
    comment = (a.get("comment") or "").strip()
    if not comment:
        raise ValueError("comment is required")
    saved = repo.tasks.add_task_comment(path, task_id, "kith", comment)
    task = repo.tasks.task_detail(path, task_id)
    goal = task["goal"] if task else f"task #{task_id}"
    snippet = comment if len(comment) <= 140 else comment[:140] + "…"
    repo.messages.add_message(
        path,
        f"New note on “{goal}” (task #{task_id}): {snippet}",
        link=f"/tasks/{task_id}",
        # Running commentary. The numerous kind, and the one that was burying the rest.
        kind="note",
    )
    return {
        "commented": True,
        "task_id": task_id,
        "comment_id": saved["id"],
        "note": "Posted and they've been notified.",
    }


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
        # He said it himself: something's missing. That's a hand-back, not a close.
        body += f"\n\n**{len(unmet)} of {len(checks)} not met — leaving this with you rather than calling it done.**"
        a["status"] = "waiting"
        repo.tasks.add_task_comment(path, a["id"], "kith", body)
        repo.messages.add_message(
            path,
            f"I couldn't finish “{detail.get('goal') or ('task #' + str(a['id']))}” (task #{a['id']}): "
            + "; ".join(str(c.get("requirement")) for c in unmet),
            link=f"/tasks/{a['id']}",
            kind="stuck",
        )
        return None

    repo.tasks.add_task_comment(path, a["id"], "kith", body)
    return None


def _update_task(path: Path, a: dict) -> dict | None:
    if (a.get("status") or "") == "done":
        refusal = _verify_done(path, a)
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
        a.get("due_at"),
        a.get("description"),
    )
    # Moving a task along is working on its project, whether or not this call is the one that
    # named it. That covers the ordinary case a create-only rule would miss: picking up a
    # project someone laid out yesterday, where the first thing he touches is a task that
    # already exists.
    from kith.services import session_context

    session_context.adopt(path, (out or {}).get("project_id"))
    # Promoting something out of the backlog is the moment it becomes work, and therefore the
    # moment worth waking for. This is the other half of scaffolding into `backlog`: you lay
    # the roadmap out with nothing running, and moving the first task to `todo` is what says
    # go — rather than a button somewhere else that means the same thing.
    if (a.get("status") or "") in _ACTIONABLE:
        _get_on_with_it(f"task ready: {str((out or {}).get('goal') or '')[:40]}")
    return out


_VERIFY_MIN_BRIEF = 80

#: Shortest description that counts as a real "definition of done" on a task that belongs to a
#: project or milestone. Trivial standalone errands need none, the same way _verify_done only
#: gates a task that carries a written brief.
_MIN_DONE_CHARS = 24

#: Statuses that mean a task is off the board — it neither blocks a duplicate nor counts against
#: a milestone's task cap.
_SETTLED = ("done", "dropped")

#: Statuses the tick can actually pick up — see `repo.tasks.active_tasks`. A task arriving in
#: one of these is a reason to wake; a task arriving in `backlog` deliberately is not.
_ACTIONABLE = ("todo", "doing")


def _get_on_with_it(why: str) -> None:
    """Wake the session this is happening in, if there is one.

    Imported here rather than at module scope: `kith.autonomy.runner` imports the tool
    registry, so a top-level import would be a cycle. Silent on failure — waking the loop is
    a courtesy, and a task must still be filed on a machine where it does not work.
    """
    try:
        from kith.autonomy import runner as loop
        from kith.services import session_context

        conversation = session_context.current()
        if conversation:
            loop.nudge(conversation, why)
    except Exception:
        pass


@tool(
    "add_task",
    "Record a task to pursue — a real unit of work, and an OUTCOME, not an activity. A task is "
    "the thing produced (\"the seed script runs and loads fixtures\"), with a 'description' saying "
    "how you'll KNOW it's done — ideally something runnable (a command that exits 0, a test that "
    'passes, a file that exists). "Verify/inspect/consolidate X" is a done-condition, not a task '
    "of its own. A task under a project or milestone MUST carry such a description. 'priority' is "
    "high for what matters most; 'due_at' if it's time-bound. New tasks start in 'todo'; use "
    "'backlog' if it's not to start yet.",
    {
        "goal": {**STR, "description": "Short title — the outcome, not a verb like 'verify X'."},
        "description": {
            **STR,
            "description": "How you'll know it's done — required for a task under a project or "
            "milestone; a runnable check beats prose.",
        },
        "priority": {**STR, "enum": list(TASK_PRIORITIES), "description": "low | normal | high."},
        "due_at": {**STR, "description": "Optional due time, ISO 8601 (your local zone)."},
        "status": {**STR, "enum": list(TASK_STATUSES), "description": "Defaults to 'todo'."},
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
        if existing.get("status") in _SETTLED or existing.get("project_id") != project_id:
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
            if t.get("milestone_id") == milestone_id and t.get("status") not in _SETTLED
        )
        if open_here >= cap:
            return {
                "ok": False,
                "error": (
                    f"This milestone already has {open_here} open task(s) — finish or drop some "
                    "before adding more. Plan one milestone shallowly, not all of it at once."
                ),
            }

    # A task attached to a milestone is a roadmap being laid out, and a roadmap is not started
    # halfway through writing it. Defaulting those to `backlog` is what stops him picking up
    # task one while the third is still being typed — which is what happened, and what made
    # scaffolding a project feel like a race. A standalone errand still lands in `todo`,
    # because filing one of those *is* asking for it to happen.
    status = args.get("status") or ("backlog" if milestone_id else "todo")
    made = repo.tasks.add_task(
        path,
        goal,
        args.get("priority") or "normal",
        args.get("due_at"),
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
    # And something actionable is a reason to get on with it. The loop only wakes for a session
    # that is working, so before this a task filed in a conversation sat there until someone
    # found the button — you asked for a thing, he wrote it down, and you both waited.
    if status in _ACTIONABLE:
        _get_on_with_it(f"new task: {goal[:40]}")
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
    "Update a task — move it between columns (backlog/todo/doing/waiting/done/dropped), "
    "or change its goal, description, priority, due date, or which project/milestone it belongs to. "
    "Move it to 'doing' when you start, 'done' when finished, 'dropped' if you're giving up. "
    "Marking the last task of a milestone 'done' auto-completes that milestone, and completing "
    "all of a project's milestones auto-completes the project — so just keep tasks honest. "
    "Once a task is 'done' or 'dropped', leave it alone — don't re-work it. "
    "Moving a task with a written brief to 'done' REQUIRES `verification` — see below.",
    {
        "id": INT,
        "status": {**STR, "enum": list(TASK_STATUSES)},
        "goal": STR,
        "description": STR,
        "priority": {**STR, "enum": list(TASK_PRIORITIES)},
        "due_at": STR,
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
    "comment_on_task",
    "Add a comment to a task's thread — an update for your person about progress "
    "on that specific task. This notifies them with a link straight to the task, "
    "so it's how you keep them in the loop on a task without interrupting them.",
    {"id": INT, "comment": STR},
    required=("id", "comment"),
)
def comment_on_task(path: Path, args: dict):
    return _comment_on_task(path, args)


@tool(
    "ask_on_task",
    "Ask your person a question you need answered to move a task forward. This "
    "posts the question on the task, moves it to 'waiting', and notifies them. "
    "Don't work it further until they reply on the task.",
    {"id": INT, "question": STR},
    required=("id", "question"),
)
def ask_on_task(path: Path, args: dict):
    return _ask_on_task(path, args)


@tool(
    "add_checklist_item",
    "Add a sub-step to a task's checklist — break the work into concrete steps.",
    {"id": {**INT, "description": "The task id."}, "text": STR},
    required=("id", "text"),
)
def add_checklist_item(path: Path, args: dict):
    return repo.tasks.add_checklist_item(path, args["id"], args["text"])


@tool(
    "check_item",
    "Tick a checklist item done (or undone) as you complete steps.",
    {"item_id": INT, "done": {"type": "boolean", "description": "Defaults to true."}},
    required=("item_id",),
)
def check_item(path: Path, args: dict):
    return repo.tasks.set_checklist_item(path, args["item_id"], args.get("done", True))


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
