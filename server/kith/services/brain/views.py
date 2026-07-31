"""Read-only views of his whole mind: the panel snapshot and the lifetime timeline."""

from __future__ import annotations

from pathlib import Path

from kith.domain import clock
from kith.infra.db import repositories as repo

_LIMIT = 2000


def snapshot(path: Path) -> dict:
    memories = repo.memories.list_memories(path, _LIMIT)
    notes = repo.notes.list_notes(path, _LIMIT)
    journal = repo.journal.list_journal(path, _LIMIT)
    tasks = repo.tasks.list_tasks(path)
    tools = repo.custom_tools.list_custom_tools(path)
    reminders = [
        {**r, "fires": clock.humanize_until(r["fire_at"])} for r in repo.reminders.list_reminders(path)
    ]
    messages = repo.messages.list_messages(path)
    people = repo.people.list_people(path)
    source_list = repo.sources.list_sources(path)
    schedules = [
        {**s, "fires": clock.humanize_until(s["next_fire"])} for s in repo.schedules.list_schedules(path)
    ]
    projects = repo.projects.project_overview(path)
    return {
        "counts": {
            "memories": len(memories),
            "notes": len(notes),
            "journal": len(journal),
            "tasks": len(tasks),
            "tools": len(tools),
            "reminders": len(reminders),
            "messages": len(messages),
            "people": len(people),
            "sources": len(source_list),
            "schedules": len(schedules),
            "projects": len(projects),
        },
        "projects": projects,
        "memories": memories,
        "notes": notes,
        "journal": journal,
        "tasks": tasks,
        "tools": tools,
        "reminders": reminders,
        "messages": messages,
        "people": people,
        "sources": source_list,
        "schedules": schedules,
        "mood": repo.self_model.get_mood(path),
        "self": repo.self_model.get_self(path),
    }


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
    for n in repo.notes.list_notes(path, _LIMIT):
        events.append({"kind": "note", "at": n["created_at"], "text": n["title"], "meta": {"id": n["id"]}})
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
    for c in repo.custom_tools.list_custom_tools(path):
        events.append(
            {"kind": "tool", "at": c["created_at"], "text": c["name"], "meta": {"language": c["language"]}}
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
