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

from kith.config import default_config
from kith.domain import clock
from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.services import conversations
from kith.services.activity import feed
from kith.settings import AGENT_DB_PATH

#: How often to ask. Reminders are minute-grained at best, so this is comfortably finer than
#: anything anyone can set, and cheap: two indexed reads against SQLite.
_EVERY_SECONDS = 30.0

_thread: threading.Thread | None = None
_stop = threading.Event()


def fire_due(now_iso: str) -> list[str]:
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
                _continue(conversation_id, notes)
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
        nxt = clock.next_fire_after(s.get("every_minutes"), s.get("daily_at"))
        repo.schedules.reschedule(AGENT_DB_PATH, s["id"], nxt)
        feed.publish("reminder", f"(standing) {s['note']}", conversation=s["conversation_id"])
    return woken


def _continue(conversation_id: str, notes: list[str]) -> None:
    """Run one turn in `conversation_id`, triggered by a reminder instead of a message typed
    in — but everything downstream of that is the same machinery a real chat turn uses. That
    is the whole point: the result becomes an actual message in the transcript, not a line in
    the live feed that is gone the moment nobody is looking at it.
    """
    from kith.api.routes.chat import _build_messages, _Recorder, _turn

    config = default_config()
    trigger = (
        "One of your reminders just fired. Check on it and tell them what changed — "
        "briefly, the way you would mid-conversation, not a report — or that nothing "
        "has, if that's the honest answer.\n\n" + "\n".join(f"- {note}" for note in notes)
    )
    history = [*conversations.full_messages(conversation_id), {"role": "user", "content": trigger}]
    messages = _build_messages(history, config, conversation_id)
    conversations.record(AGENT_DB_PATH, conversation_id, "user", trigger)

    recorder = _Recorder(conversation_id)
    for _ in _turn(recorder, messages, config, conversation_id, opening=trigger):
        pass  # driving the generator is the point — nothing is streaming this anywhere


def start() -> None:
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
                fire_due(clock.now_iso())
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
