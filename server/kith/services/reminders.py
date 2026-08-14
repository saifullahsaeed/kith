"""Making a reminder or a schedule, from wherever the request came.

There are two ways one gets created — his own `set_reminder` tool, and the control panel —
and they must agree, because a reminder made from the panel that forgot to record its
conversation fires into nowhere.

They already agreed. The logic lived in `tools/time.py` as `_set_reminder` and `_schedule`,
and `services/brain/kinds.py` reached into that tool module to call them, under a comment
saying exactly what was wrong with it:

    Imported here rather than at module scope because kith.tools imports this package: a
    top-level import would close the loop and neither module would load.

That is the loop, named. A tool module is an adapter — it parses arguments, delegates, and
serialises — so a service reaching into one for the delegate is the arrow pointing the wrong
way. The shared half lives here now; both callers are adapters over it, and neither imports
the other.

The conversation is **captured rather than asked for**. The model has no reason to think
about which conversation it is in, and a required argument it has to remember is one it will
eventually forget. This is what lets a reminder firing report back to the chat it was set in,
instead of whichever conversation happens to be open when it comes due.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.kernel import clock, session_context
from kith.services import local_time


def set_reminder(path: Path, args: dict) -> dict:
    """One reminder, at a moment. Raises ValueError on a missing note or an unreadable time."""
    note = (args.get("note") or "").strip()
    if not note:
        raise ValueError("note is required")
    fire_at = local_time.resolve_fire_at(args.get("in_minutes"), args.get("at"))
    reminder = repo.reminders.add_reminder(path, fire_at, note, conversation_id=session_context.current())
    return {**reminder, "fires": clock.humanize_until(fire_at)}


def schedule(path: Path, args: dict) -> dict:
    """One recurring schedule. Raises ValueError on a missing note or no cadence."""
    note = (args.get("note") or "").strip()
    if not note:
        raise ValueError("note is required")
    every, daily = args.get("every_minutes"), args.get("daily_at")
    next_fire = local_time.next_fire_after(every, daily)
    sched = repo.schedules.add_schedule(
        path, note, next_fire, every, daily, conversation_id=session_context.current()
    )
    return {**sched, "fires": clock.humanize_until(next_fire)}
