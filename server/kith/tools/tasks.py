"""Tasks, and working one in depth: thread, checklist, deliverables."""

from __future__ import annotations

from pathlib import Path

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
    return repo.tasks.update_task(
        path,
        a["id"],
        a.get("status"),
        a.get("goal"),
        a.get("priority"),
        a.get("due_at"),
        a.get("description"),
    )


_VERIFY_MIN_BRIEF = 80


@tool(
    "add_task",
    "Record a task to pursue — a real unit of work. Give it a clear goal; add a "
    "'description' (what done looks like), a 'priority' (high for what matters most, "
    "so you work it first), and a 'due_at' if it's time-bound. New tasks start in "
    "'todo'; put it in 'backlog' if it's not to start yet.",
    {
        "goal": {**STR, "description": "Short title of the task."},
        "description": {**STR, "description": "Optional: detail and what 'done' means."},
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
    return repo.tasks.add_task(
        path,
        args["goal"],
        args.get("priority") or "normal",
        args.get("due_at"),
        args.get("description") or "",
        args.get("status") or "todo",
        "kith",
        args.get("project_id"),
        args.get("milestone_id"),
    )


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
    return repo.tasks.add_deliverable(
        path, args["id"], args.get("kind") or "text", args["title"], args["content"]
    )
