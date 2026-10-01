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


#: A lifetime page when nobody says how big, and the most anybody can ask for at once.
PAGE = 500
MAX_PAGE = 2000


def timeline(path: Path, limit: int = PAGE) -> list[dict]:
    """The newest `limit` events — the bare array `GET /api/brain/timeline` has always answered."""
    return timeline_page(path, limit=limit)["events"]


def timeline_page(path: Path, *, before: str | None = None, limit: int = PAGE) -> dict:
    """One page of his lifetime, newest first: every event strictly older than `before`.

    Five tables merged by time, and the merge is a page wide rather than history wide. Each
    table hands over its own newest `limit + 1` rows older than `before` — its own ORDER BY and
    LIMIT — so the merged page is right without anything having read the rest. This used to
    load every table whole (a thousand-odd rows here, growing every hour) to keep 500, and it
    could not see past them at all: the Lifetime screen ended on whatever day the 500th fell.

    ``has_more`` is whether anything older exists. ``totals`` and ``message_kinds`` are counted
    over all of history, not the page, so the screen's chips say how many there are rather
    than how many it happens to have loaded. ``oldest`` is when the first thing happened, so a
    date picker knows how far back it can go before the pages reaching it have arrived.

    A page never ends partway through events that share a timestamp: the next page asks for
    strictly older than the last one shown, so a tie cut in half would lose its second half.
    The tied run is held back for the next page — unless it is the whole page, where holding
    it back would page forever (and the run's remainder is skipped: `limit` events written in
    one microsecond is not something this database does).
    """
    limit = max(1, min(int(limit), MAX_PAGE))
    take = limit + 1
    events = [
        *(_memory(m) for m in repo.memories.memories_before(path, before, take)),
        *(_journal(j) for j in repo.journal.journal_before(path, before, take)),
        *(_task(t) for t in repo.tasks.tasks_before(path, before, take)),
        *(_reminder(r) for r in repo.reminders.reminders_before(path, before, take)),
        *(_message(m) for m in repo.messages.messages_before(path, before, take)),
    ]
    events.sort(key=lambda e: e["at"], reverse=True)
    has_more = len(events) > limit
    page = events[:limit]
    if has_more and page[-1]["at"] == events[limit]["at"]:
        cut = page[-1]["at"]
        kept = [e for e in page if e["at"] != cut]
        page = kept or page
    spans = {
        "memory": repo.memories.memories_span(path),
        "journal": repo.journal.journal_span(path),
        "task": repo.tasks.tasks_span(path),
        "reminder": repo.reminders.reminders_span(path),
    }
    message_kinds, first_message = repo.messages.messages_span(path)
    firsts = [first for _, first in spans.values() if first] + ([first_message] if first_message else [])
    return {
        "events": page,
        "has_more": has_more,
        "totals": {
            **{kind: count for kind, (count, _) in spans.items()},
            "message": sum(message_kinds.values()),
        },
        "message_kinds": message_kinds,
        "oldest": min(firsts) if firsts else None,
    }


def _memory(m: dict) -> dict:
    return {
        "kind": "memory",
        "at": m["created_at"],
        "text": m["content"],
        "meta": {"id": m["id"], "level": m["level"], "importance": m["importance"]},
    }


def _journal(j: dict) -> dict:
    return {"kind": "journal", "at": j["created_at"], "text": j["entry"], "meta": {"id": j["id"]}}


def _task(t: dict) -> dict:
    return {
        "kind": "task",
        "at": t["created_at"],
        "text": t["goal"],
        "meta": {"id": t["id"], "status": t["status"]},
    }


def _reminder(r: dict) -> dict:
    return {
        "kind": "reminder",
        "at": r["created_at"],
        "text": r["note"],
        "meta": {"id": r["id"], "status": r["status"], "fireAt": r["fire_at"]},
    }


def _message(m: dict) -> dict:
    """A message, with what the screen needs to say what it was.

    `kind`, `link` and `projectId` ride along because the screen used to look them up in the
    snapshot, and the snapshot holds the newest hundred messages — fine while the timeline
    stopped at 500 events, wrong for a page from August, whose questions and stops would have
    lost their kind and dropped off the screen.
    """
    return {
        "kind": "message",
        "at": m["created_at"],
        "text": m["body"],
        "meta": {
            "id": m["id"],
            "read": m["read"],
            "kind": m.get("kind") or "note",
            "link": m.get("link"),
            "projectId": m.get("project_id"),
        },
    }
