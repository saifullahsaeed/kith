"""Read-only views of his whole mind: the panel snapshot and the lifetime timeline."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.kernel import clock

_LIMIT = 2000


def snapshot(path: Path) -> dict:
    memories = repo.memories.list_memories(path, _LIMIT)
    journal = repo.journal.list_journal(path, _LIMIT)
    tasks = snapshot_tasks(path)
    reminders = [
        {**r, "fires": clock.humanize_until(r["fire_at"])} for r in repo.reminders.list_reminders(path)
    ]
    messages = repo.messages.list_messages(path)
    source_list = repo.sources.list_sources(path)
    schedules = [
        {**s, "fires": clock.humanize_until(s["next_fire"])} for s in repo.schedules.list_schedules(path)
    ]
    projects = repo.projects.project_overview(path)
    return {
        "counts": {
            "memories": len(memories),
            "journal": len(journal),
            "tasks": len(tasks),
            "reminders": len(reminders),
            "messages": len(messages),
            "sources": len(source_list),
            "schedules": len(schedules),
            "projects": len(projects),
        },
        "projects": projects,
        "memories": memories,
        "journal": journal,
        "tasks": tasks,
        "reminders": reminders,
        "messages": messages,
        "sources": source_list,
        "schedules": schedules,
    }


def snapshot_tasks(path: Path) -> list[dict]:
    """Every task as the board draws it — see `_with_progress`."""
    return _with_progress(path, repo.tasks.list_tasks(path))


def _with_progress(path: Path, tasks: list[dict]) -> list[dict]:
    """Each task with the three numbers a board card shows: checklist done/total and outputs.

    Two grouped queries for the whole board, rather than the card fetching its task's detail — a
    board of forty cards opening forty requests to draw "3/5" is how a page gets slow.
    """
    progress = repo.tasks.checklist_progress(path, [int(t["id"]) for t in tasks])
    outputs = repo.tasks.deliverable_counts(path)
    return [
        {
            **task,
            "checklist_done": progress.get(int(task["id"]), (0, 0))[0],
            "checklist_total": progress.get(int(task["id"]), (0, 0))[1],
            "outputs": outputs.get(int(task["id"]), 0),
        }
        for task in tasks
    ]


def timeline(path: Path, limit: int = 500) -> list[dict]:
    events: list[dict] = []
    for m in repo.memories.list_memories(path, _LIMIT):
        events.append(
            {
                "kind": "memory",
                "at": m["created_at"],
                "text": m["content"],
                "meta": {"id": m["id"], "level": m["level"], "importance": m["importance"]},
            }
        )
    for j in repo.journal.list_journal(path, _LIMIT):
        events.append({"kind": "journal", "at": j["created_at"], "text": j["entry"], "meta": {"id": j["id"]}})
    for t in repo.tasks.list_tasks(path):
        events.append(
            {
                "kind": "task",
                "at": t["created_at"],
                "text": t["goal"],
                "meta": {"id": t["id"], "status": t["status"]},
            }
        )
    for r in repo.reminders.list_reminders(path):
        events.append(
            {
                "kind": "reminder",
                "at": r["created_at"],
                "text": r["note"],
                "meta": {"id": r["id"], "status": r["status"], "fireAt": r["fire_at"]},
            }
        )
    for m in repo.messages.list_messages(path):
        events.append(
            {
                "kind": "message",
                "at": m["created_at"],
                "text": m["body"],
                "meta": {"id": m["id"], "read": m["read"]},
            }
        )
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]
