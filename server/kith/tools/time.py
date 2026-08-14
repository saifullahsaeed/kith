"""His sense of when: reminders and standing schedules."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.kernel import clock
from kith.services import reminders as reminder_service
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import INT, STR
from kith.tools.registry import tool


def _list_reminders(path: Path) -> list[dict]:
    return [
        {**r, "fires": clock.humanize_until(r["fire_at"])}
        for r in repo.reminders.list_reminders(path, status="pending")
    ]


@tool(
    "set_reminder",
    "Leave a reminder for your future self, tied to a moment. Give either "
    "'in_minutes' (from now) or 'at' (a time). When it comes due, it surfaces "
    "to you on your own so you can act on it. Use it to pace yourself, follow "
    "up on something later, or not lose a thread. "
    "**Not for waiting on a background task.** A test run, a build, a CI check — those come back "
    "to you by themselves when they end, so a reminder to go and look is a round spent on an "
    "answer that was already coming. Reminders are for things nothing else will tell you about.",
    {
        "note": {**STR, "description": "What to remind yourself of."},
        "in_minutes": {"type": "number", "description": "Fire this many minutes from now."},
        "at": {**STR, "description": "Or an absolute time, ISO 8601 (your local zone if no offset)."},
    },
    required=("note",),
)
def set_reminder(path: Path, args: dict):
    return reminder_service.set_reminder(path, args)


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
    return reminder_service.schedule(path, args)


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
