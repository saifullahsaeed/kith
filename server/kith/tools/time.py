"""His sense of when: reminders and standing schedules."""

from __future__ import annotations

from pathlib import Path

from kith.domain import clock
from kith.infra.db import repositories as repo
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import INT, STR
from kith.tools.registry import tool


def _set_reminder(path: Path, a: dict) -> dict:
    note = (a.get("note") or "").strip()
    if not note:
        raise ValueError("note is required")
    fire_at = clock.resolve_fire_at(a.get("in_minutes"), a.get("at"))
    reminder = repo.reminders.add_reminder(path, fire_at, note)
    return {**reminder, "fires": clock.humanize_until(fire_at)}


def _list_reminders(path: Path) -> list[dict]:
    return [
        {**r, "fires": clock.humanize_until(r["fire_at"])}
        for r in repo.reminders.list_reminders(path, status="pending")
    ]


def _schedule(path: Path, a: dict) -> dict:
    note = (a.get("note") or "").strip()
    if not note:
        raise ValueError("note is required")
    every, daily = a.get("every_minutes"), a.get("daily_at")
    next_fire = clock.next_fire_after(every, daily)
    sched = repo.schedules.add_schedule(path, note, next_fire, every, daily)
    return {**sched, "fires": clock.humanize_until(next_fire)}


@tool(
    "set_reminder",
    "Leave a reminder for your future self, tied to a moment. Give either "
    "'in_minutes' (from now) or 'at' (a time). When it comes due, it surfaces "
    "to you on your own so you can act on it. Use it to pace yourself, follow "
    "up on something later, or not lose a thread.",
    {
        "note": {**STR, "description": "What to remind yourself of."},
        "in_minutes": {"type": "number", "description": "Fire this many minutes from now."},
        "at": {**STR, "description": "Or an absolute time, ISO 8601 (your local zone if no offset)."},
    },
    required=("note",),
)
def set_reminder(path: Path, args: dict):
    return _set_reminder(path, args)


@tool(
    "list_reminders",
    "See the reminders you've set that haven't fired yet.",
    {**PAGE_PARAMS},
    required=(),
)
def list_reminders(path: Path, args: dict):
    return paging.page(_list_reminders(path), args)


@tool(
    "cancel_reminder",
    "Cancel a reminder you no longer need, by its id.",
    {"id": INT},
    required=("id",),
)
def cancel_reminder(path: Path, args: dict):
    return {"cancelled": repo.reminders.delete_reminder(path, args["id"])}


@tool(
    "schedule",
    "Set a STANDING job that repeats on a cadence (unlike a one-off reminder) — "
    "e.g. a daily briefing, or a recurring check. Give either 'every_minutes' "
    "(repeat that often) or 'daily_at' (a local 'HH:MM', once a day). When it "
    "comes due it surfaces to you to carry out, then rolls to next time.",
    {
        "note": {**STR, "description": "What to do each time it fires."},
        "every_minutes": {**INT, "description": "Repeat every this many minutes."},
        "daily_at": {**STR, "description": "Or once a day at this local time, 'HH:MM'."},
    },
    required=("note",),
)
def schedule(path: Path, args: dict):
    return _schedule(path, args)


@tool(
    "list_schedules",
    "See your standing jobs and when each fires next.",
    {**PAGE_PARAMS},
    required=(),
)
def list_schedules(path: Path, args: dict):
    rows = [{**s, "fires": clock.humanize_until(s["next_fire"])} for s in repo.schedules.list_schedules(path)]
    return paging.page(rows, args)


@tool(
    "cancel_schedule",
    "Remove a standing job by its id.",
    {"id": INT},
    required=("id",),
)
def cancel_schedule(path: Path, args: dict):
    return {"cancelled": repo.schedules.delete_schedule(path, args["id"])}
