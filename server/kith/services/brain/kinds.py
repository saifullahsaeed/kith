"""What each editable kind of thing is, in one table.

The UI edits Kith's mind generically: ``DELETE /api/brain/task/12``,
``POST /api/brain/note``. That keeps the frontend small, but it needs the server to
turn a *string* into the right repository call — and that used to be three functions
of ``if kind == "…"``, 38 branches in total, each one repeating the same shape.

The cost of that shape was not length. It was that adding a kind meant remembering
three separate places, nothing checked they agreed, and a kind that supported
deleting but not creating looked identical to one that had simply been forgotten.

So each kind is declared once, with only the operations it genuinely supports.
Anything absent is *unsupported by declaration* rather than by omission, and the
API can say so precisely.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kith.infra.db import repositories as repo
from kith.services import embeddings

# A key arrives from a URL, so it is always a string. Most tables use integer ids;
# custom tools are keyed by name. This is the only difference between them, and it
# is worth naming rather than sprinkling int() calls through a switch.
Coerce = Callable[[str], Any]


@dataclass(frozen=True)
class Kind:
    """One editable kind of thing, and what may be done to it."""

    name: str
    remove: Callable[[Path, Any], bool] | None = None
    add: Callable[[Path, dict], dict] | None = None
    edit: Callable[[Path, Any, dict], dict | None] | None = None
    key: Coerce = int

    def supports(self, action: str) -> bool:
        return getattr(self, action, None) is not None


def _task_edit(path, task_id, data: dict):
    """Update a task, including where it sits on the roadmap.

    Milestone is a separate call rather than another column on update_task because moving a
    task between milestones also has to keep its project straight — a task cannot belong to a
    milestone of a different project. Handled here so the interface can offer it as one field
    like any other.
    """
    if "milestone_id" in data:
        # A blank selection arrives as None, "", "none" or 0 depending on who is calling.
        # All of them mean "no milestone"; the first version of this let 0 through as an id.
        raw = data.get("milestone_id")
        chosen = None if raw in (None, "", "none", 0, "0") else int(raw)
        repo.tasks.set_task_milestone(path, task_id, chosen)
    return repo.tasks.update_task(
        path,
        task_id,
        data.get("status"),
        data.get("goal"),
        data.get("priority"),
        data.get("description"),
    )


def _make_reminder(path, data: dict) -> dict:
    """A reminder from the panel, through the same code his own tool uses.

    Imported here rather than at module scope because kith.tools imports this package: a
    top-level import would close the loop and neither module would load.
    """
    from kith.tools.time import _set_reminder

    return _set_reminder(path, data)


def _make_schedule(path, data: dict) -> dict:
    """A schedule from the panel, through the same code his own tool uses."""
    from kith.tools.time import _schedule

    return _schedule(path, data)


def _milestone_add(path: Path, data: dict) -> dict:
    return repo.projects.add_milestone(
        path, int(data["project_id"]), data.get("title", ""), data.get("target_at")
    )


def _memory_add(path: Path, data: dict) -> dict:
    # Through the service, not the repository: a new memory needs an embedding, and
    # the repository deliberately knows nothing about how vectors are produced.
    return embeddings.remember(
        path,
        data.get("content", ""),
        data.get("tags"),
        int(data.get("importance") or 0),
        data.get("level") or "recall",
    )


def _memory_edit(path: Path, memory_id: int, data: dict) -> dict | None:
    """Change a memory, and change the vector that finds it.

    These were two halves of one operation and only the first was wired up. `update_memory`
    rewrites `content` and leaves `embedding` alone; `embeddings.reembed` exists precisely to
    close that, with the docstring "refresh a memory's vector after its content changed" —
    and nothing called it. Both facts were invisible because each half works.

    The result was silent and permanent: edit a memory and recall keeps matching it on the
    words you deleted, then hands back the words you wrote. Worse than a stale answer,
    because the text on screen looks right.

    Best effort, deliberately. A memory that saves but does not re-embed is stale in search;
    a memory that refuses to save because the embedder is down is lost.
    """
    from kith.services import embeddings

    content = data.get("content")
    updated = repo.memories.update_memory(path, memory_id, content, data.get("importance"))
    if updated is not None and content:
        embeddings.reembed(path, memory_id, content)
    return updated


KINDS: dict[str, Kind] = {
    kind.name: kind
    for kind in (
        Kind(
            "memory",
            remove=repo.memories.delete_memory,
            add=_memory_add,
            edit=_memory_edit,
        ),
        Kind(
            "note",
            remove=repo.notes.delete_note,
            add=lambda p, d: repo.notes.add_note(p, d.get("title", ""), d.get("body") or ""),
            edit=lambda p, k, d: repo.notes.update_note(p, k, d.get("title"), d.get("body")),
        ),
        Kind("journal", remove=repo.journal.delete_journal),
        Kind(
            "task",
            remove=repo.tasks.delete_task,
            add=lambda p, d: repo.tasks.add_task(
                p,
                d.get("goal", ""),
                d.get("priority") or "normal",
                d.get("description") or "",
                d.get("status") or "planning",
                "user",
                d.get("project_id"),
            ),
            edit=_task_edit,
        ),
        Kind(
            "project",
            remove=repo.projects.delete_project,
            add=lambda p, d: repo.projects.add_project(p, d.get("name", ""), d.get("description") or ""),
            edit=lambda p, k, d: repo.projects.update_project(
                p, k, d.get("status"), d.get("name"), d.get("description")
            ),
        ),
        Kind(
            "milestone",
            remove=repo.projects.delete_milestone,
            add=_milestone_add,
            edit=lambda p, k, d: repo.projects.update_milestone(
                p, k, d.get("status"), d.get("title"), d.get("target_at")
            ),
        ),
        Kind(
            "checklist_item",
            remove=repo.tasks.delete_checklist_item,
            add=lambda p, d: repo.tasks.add_checklist_item(p, int(d["task_id"]), d.get("text", "")),
            edit=lambda p, k, d: repo.tasks.set_checklist_item(p, k, d.get("done"), d.get("text")),
        ),
        Kind(
            "deliverable",
            remove=repo.tasks.delete_deliverable,
            add=lambda p, d: repo.tasks.add_deliverable(
                p, int(d["task_id"]), d.get("kind") or "text", d.get("title", ""), d.get("content", "")
            ),
        ),
        Kind(
            "reminder",
            remove=repo.reminders.delete_reminder,
            # The panel has had an Add form for this the whole time and it answered 400
            # "cannot add a reminder" — a button that looks like a feature and is a dead end.
            # The tool that he uses already resolves the same payload the form sends
            # ({note, in_minutes} or {note, at}), so this reuses it rather than growing a
            # second interpretation of "in 20 minutes" that could disagree with his.
            add=lambda p, d: _make_reminder(p, d),
            edit=lambda p, k, d: repo.reminders.set_reminder_status(p, k, d.get("status", "done")),
        ),
        Kind(
            "message",
            remove=repo.messages.delete_message,
            add=lambda p, d: repo.messages.add_message(p, d.get("body", ""), "user"),
        ),
        Kind(
            "person",
            remove=repo.people.delete_person,
            add=lambda p, d: repo.people.upsert_person(
                p, d.get("name", ""), d.get("relationship"), d.get("profile")
            ),
            edit=lambda p, k, d: repo.people.update_person(p, k, d.get("relationship"), d.get("profile")),
        ),
        Kind("source", remove=repo.sources.delete_source),
        Kind(
            "schedule",
            remove=repo.schedules.delete_schedule,
            # Same as reminders: the form existed, the endpoint refused.
            add=lambda p, d: _make_schedule(p, d),
            edit=lambda p, k, d: repo.schedules.set_schedule_status(p, k, d.get("status", "active")),
        ),
        # Keyed by name: he calls his self-made tools by name, so that is their identity.
        Kind("tool", remove=repo.custom_tools.delete_custom_tool, key=str),
    )
}


def require(kind: str, action: str) -> Kind:
    """The Kind, or a ValueError naming what is actually possible.

    Distinguishes "no such kind" from "that kind cannot be created", which the old
    switch could not: both fell through to the same generic error.
    """
    entry = KINDS.get(kind)
    if entry is None:
        raise ValueError(f"unknown kind: {kind}. Known: {', '.join(sorted(KINDS))}")
    if not entry.supports(action):
        raise ValueError(f"cannot {action} a {kind}")
    return entry
