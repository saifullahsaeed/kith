"""Stopping a conversation's turn: the flag, a queued turn dropped, and every wait woken.

It lived in the chat route as `_stop`, which was fine while the person's Stop button was the only
caller. The agent tree needs it too — a handler stopping its agents — and a service may not import
a route, so it was handed over at boot through `agents.install`. It needs nothing the route has:
the switch is kernel, the waits are services and infra. So it lives here, and both callers import it.
"""

from __future__ import annotations

from kith.infra import permissions
from kith.kernel import stopping, turns
from kith.services import questions


def stop(conversation_id: str) -> bool:
    """Stop whatever a conversation is doing. False if it was doing nothing.

    Order matters here, and it used to be the other way round.

    A turn parked on a question is not reading the switch — it is inside a tool call, waiting on
    an event — so the waits have to be released or Stop does nothing for up to fifteen minutes.
    But releasing *first* hands the turn back its next round before it has been told to stop:
    `ask` returns the moment the event is set, the loop appends the result, `_turn` yields it and
    only then reads the flag. Lose that race and the flag is still unset, so the turn carries
    on — one more model call, on the full conversation, after the person pressed Stop. Which is
    what "I stopped it and it started again" is.

    So the flag goes on first and the waits are released after. The window closes rather than
    merely being small: by the time anything the release woke can reach a check, the flag it
    reads is already set.

    A turn still waiting for a slot is dropped from the queue. What it would have said stays
    where it was — a reminder in the transcript, mail in the agent's mailbox — for the next turn.

    The agent tree's own waits (`agents.release`) are the caller's to release: that module
    imports this one.
    """
    was_running = stopping.stop(conversation_id)
    dropped = turns.cancel(conversation_id)
    # Now nothing that wakes up can get past its next check, so it is safe to wake it.
    questions.release(conversation_id)
    permissions.release_waiting(conversation_id)
    # A turn parked on a plugin surface is not reading the stop switch either, so stopping it
    # has to reach in and wake it — the same reason the two lines above exist.
    _release_plugin_calls(conversation_id)
    return was_running or bool(dropped)


def _release_plugin_calls(conversation_id: str) -> None:
    """Wrapped and lazy: a plugin fault must never be what stops a stop from working."""
    try:
        from kith.services.plugins import calls

        calls.release(conversation_id)
    except Exception:
        pass
