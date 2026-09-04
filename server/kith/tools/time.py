"""His sense of when: things set to come back to him, once or on a cadence.

There were six tools here and there are three, because a one-off and a repeating job were
never two capabilities — they are one capability with two ways of saying when. `set_reminder`
and `schedule` took the same `note` and differed only in whether the time argument named an
instant or an interval; `list_reminders` and `list_schedules` were the same query twice; and
`cancel_reminder` and `cancel_schedule` were the same delete. Three schemas of the six existed
to make the model classify its own intent before it could act on it, which is a choice it can
get wrong and a round it can lose.

The two tables stay separate — a fired reminder is finished and a fired schedule rolls forward,
and that is a real difference in what they *are*. It is just not a difference the caller has to
resolve before it can speak.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.kernel import clock
from kith.services import reminders as reminder_service
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import INT, STR
from kith.tools.registry import tool

#: What `kind` says, and what each one means when it fires. Named rather than inferred from
#: which table a row came out of, because the row is what the model sees and "once" is the
#: thing it needs to know before deciding whether cancelling matters.
ONCE = "once"
REPEATING = "repeating"


def _pending(path: Path) -> list[dict]:
    return [
        {**r, "kind": ONCE, "fires": clock.humanize_until(r["fire_at"]), "at": r["fire_at"]}
        for r in repo.reminders.list_reminders(path, status="pending")
    ]


def _standing(path: Path) -> list[dict]:
    return [
        {**s, "kind": REPEATING, "fires": clock.humanize_until(s["next_fire"]), "at": s["next_fire"]}
        for s in repo.schedules.list_schedules(path)
    ]


def _soonest(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: str(row.get("at") or ""))


@tool(
    "schedule",
    "Set something to come back to you at a time. `in_minutes` or `at` for a one-off; "
    "`every_minutes` or `daily_at` ('HH:MM' local) for a standing job that repeats. When it "
    "comes due it surfaces to you so you can act on it, and a standing one then rolls to next "
    "time. Use it to pace yourself, follow something up later, or not lose a thread. "
    "**Not for waiting on a background task.** A test run, a build, a CI check come back to "
    "you by themselves when they end, so a reminder to go and look is a round spent on an "
    "answer that was already coming — this is for things nothing else will tell you about.",
    {
        "note": {**STR, "description": "What to remind yourself of, or to do each time."},
        "in_minutes": {"type": "number", "description": "Once, this many minutes from now."},
        "at": {**STR, "description": "Once, at an absolute ISO 8601 time (local zone if no offset)."},
        "every_minutes": {**INT, "description": "Repeating, every this many minutes."},
        "daily_at": {**STR, "description": "Repeating, once a day at this local 'HH:MM'."},
    },
    required=("note",),
)
def schedule(path: Path, args: dict):
    """The cadence arguments decide which it is, so the caller never has to name the kind.

    Reading the arguments rather than a `kind` flag is what makes the merge free: there is no
    third state to get wrong, and a call that gives both a cadence and an instant is answered
    as a cadence rather than refused — `every_minutes` is the more specific request, and
    refusing would cost a round to learn something we can simply decide.
    """
    if args.get("every_minutes") or args.get("daily_at"):
        return {**reminder_service.schedule(path, args), "kind": REPEATING}
    return {**reminder_service.set_reminder(path, args), "kind": ONCE}


@tool(
    "list_schedules",
    "Everything you have set to come back to you — one-off reminders that haven't fired and "
    "standing jobs, with when each one is next. Each row says its `kind`: 'once' or "
    "'repeating'.",
    {**PAGE_PARAMS},
    required=(),
)
def list_schedules(path: Path, args: dict):
    """One list, because "what have I got waiting" is one question.

    Two lists meant it was asked twice or, more often, asked once and answered half — a turn
    that checked `list_reminders`, found nothing, and concluded nothing was pending while a
    daily job sat in the other table.

    **The standing jobs are never paged away, and that is what makes one list honest.** Merging
    them and paging the result reproduced the very failure the merge was for: twenty pending
    reminders fill a page of ten and every standing job is off the end, so "what have I got
    waiting" is answered without a word about the thing that fires every day. Sorting by time
    does not fix it either — a reminder an hour from now sorts ahead of tomorrow's briefing.
    The two are different in *kind*: standing jobs are a handful of long-lived commitments,
    one-off reminders are the many. So the handful is always shown and the many are paged.
    """
    standing = _soonest(_standing(path))
    paged = paging.page(_soonest(_pending(path)), args)
    items = standing + list(paged.get("items", []))
    return {**paged, "items": items, "standing": len(standing)}


@tool(
    "cancel_schedule",
    "Cancel something you set, by its id — a one-off reminder or a standing job. Pass `kind` "
    "('once' or 'repeating', as `list_schedules` reports it) if you have it.",
    {
        "id": INT,
        "kind": {**STR, "enum": [ONCE, REPEATING], "description": "Which one, if you know."},
    },
    required=("id",),
)
def cancel_schedule(path: Path, args: dict):
    """Ids are per-table, so the same number can name two different things.

    Which is why this looks before it deletes rather than trying both: reminder 3 and schedule
    3 can both exist, and a cancel that guessed would sometimes cancel the daily briefing
    because the model meant a reminder it set an hour ago. When `kind` is given it is obeyed;
    when it is not and the id is unambiguous the answer is obvious and taken; and when it is
    ambiguous the only honest reply is to say so and ask for the kind, which costs a round
    exactly in the case where a wrong guess would cost something that cannot be got back.
    """
    wanted = int(args["id"])
    kind = str(args.get("kind") or "").strip().lower()

    if kind == ONCE:
        return {"cancelled": repo.reminders.delete_reminder(path, wanted), "kind": ONCE}
    if kind == REPEATING:
        return {"cancelled": repo.schedules.delete_schedule(path, wanted), "kind": REPEATING}

    here = {row["id"] for row in _pending(path)}
    there = {row["id"] for row in _standing(path)}
    if wanted in here and wanted in there:
        return {
            "cancelled": False,
            "error": (
                f"id {wanted} is both a one-off reminder and a standing job. Pass `kind` — "
                "'once' or 'repeating' — to say which."
            ),
        }
    if wanted in there:
        return {"cancelled": repo.schedules.delete_schedule(path, wanted), "kind": REPEATING}
    return {"cancelled": repo.reminders.delete_reminder(path, wanted), "kind": ONCE}
