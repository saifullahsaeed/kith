"""What is due, and the chat it belongs to.

This ran inside the loop that used to run unasked, which is the only reason it looked like
part of it. It never chose work and never read a task: it asked whether a reminder or a schedule had come
due and continued the conversation that thing was set in. That question survives the loop
that used to ask it.

One timer thread, and it does exactly one thing. If you find yourself adding "and while we're
awake, also…" here, that is the loop growing back.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from kith.infra.db import repositories as repo
from kith.kernel import clock, session_context
from kith.services import local_time
from kith.services.activity import feed
from kith.settings import AGENT_DB_PATH

#: How to wake a conversation: given its id and an opening line, run one turn in it.
#:
#: A parameter rather than an import. Continuing a conversation is the chat route's job and
#: this is a service; reaching up for it is the arrow this exists to stop. `kith/app.py`, which
#: is allowed to know about both, supplies the real one at startup.
Resume = Callable[[str, str], None]

#: How often to ask. Reminders are minute-grained at best, so this is comfortably finer than
#: anything anyone can set, and cheap: two indexed reads against SQLite.
_EVERY_SECONDS = 30.0

_thread: threading.Thread | None = None
_stop = threading.Event()


def fire_due(now_iso: str, resume: Resume) -> list[str]:
    """Wake every conversation with something due. Returns the ids woken, in order.

    Grouped, so two reminders due for the same chat at once become one continuation with both
    notes rather than two replies talking past each other.
    """
    reminders = [r for r in repo.reminders.due_reminders(AGENT_DB_PATH, now_iso) if r.get("conversation_id")]
    schedules = [s for s in repo.schedules.due_schedules(AGENT_DB_PATH, now_iso) if s.get("conversation_id")]
    if not reminders and not schedules:
        return []

    by_conversation: dict[str, list[str]] = {}
    for r in reminders:
        by_conversation.setdefault(r["conversation_id"], []).append(r["note"])
    for s in schedules:
        by_conversation.setdefault(s["conversation_id"], []).append(f"(standing) {s['note']}")

    woken: list[str] = []
    for conversation_id, notes in by_conversation.items():
        woken.append(conversation_id)
        with session_context.working_in(conversation_id), session_context.nobody_watching():
            try:
                _continue(conversation_id, notes, resume)
            except Exception:
                # A reminder that fails to report back must not take the rest down — every
                # other conversation waiting on one still gets its turn.
                traceback.print_exc()

    # Retired regardless of whether the continuation above succeeded. One that keeps failing
    # to report back is a bug to find in the traceback just printed, not a reason to retry
    # forever.
    for r in reminders:
        repo.reminders.set_reminder_status(AGENT_DB_PATH, r["id"], "done")
        feed.publish("reminder", r["note"], conversation=r["conversation_id"])
    for s in schedules:
        nxt = local_time.next_fire_after(s.get("every_minutes"), s.get("daily_at"))
        repo.schedules.reschedule(AGENT_DB_PATH, s["id"], nxt)
        feed.publish("reminder", f"(standing) {s['note']}", conversation=s["conversation_id"])
    return woken


def _continue(conversation_id: str, notes: list[str], resume: Resume) -> None:
    """Wake a conversation because one of its reminders came due.

    The prose is this module's — it is what a reminder firing should sound like. Running the
    turn is not, and `resume` is handed in for that: this used to import `_build_messages`,
    `_Recorder` and `_turn` out of `api/routes/chat.py`, a service reaching up into an
    adapter for three private functions, and the last upward import in the tree.
    """
    trigger = (
        "One of your reminders just fired. Check on it and tell them what changed — "
        "briefly, the way you would mid-conversation, not a report — or that nothing "
        "has, if that's the honest answer.\n\n" + "\n".join(f"- {note}" for note in notes)
    )
    resume(conversation_id, trigger)


def start(resume: Resume) -> None:
    """Begin asking. Called when the app boots, never at import.

    Import-time threads are how a test suite ends up with a loop pointed at the real
    database — see `test_the_suite_does_not_start_loops`, which guards exactly this and
    outlived the loop that first earned it.
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()

    def run() -> None:
        while not _stop.wait(_EVERY_SECONDS):
            try:
                fire_due(clock.now_iso(), resume)
            except Exception:
                traceback.print_exc()
            # A background task that has finished is the same question this thread already asks —
            # something completed, and the conversation it belongs to should hear about it — so it
            # is asked here rather than on a second timer. The docstring above warns against "and
            # while we're awake, also…", and this is not that: it is due-work, in a different coat.
            try:
                from kith.services.code import processes

                processes.finished_since_last_look()
            except Exception:
                traceback.print_exc()

    _thread = threading.Thread(target=run, name="kith-scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
