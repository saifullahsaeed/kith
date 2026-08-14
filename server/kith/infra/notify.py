"""What is allowed to interrupt you.

In `infra/` rather than `services/`, because what it does is read a stored threshold and
post to the desktop shell — a setting and an outbound call, both of which this layer owns.
Filing it as orchestration is what made `infra/db/repositories/messages.py` reach upward to
ask whether a message may light the badge, with the import written inside the function so
Python would not notice. That call is sideways now and the import is at the top.

Everything he wrote reached you identically: a note on a task, a question he was blocked on,
and "I'm stuck and I've set this aside" all arrived as one stream, all unread, all worth a
notification. The routine messages are by far the most numerous, so the ones that actually
wanted an answer were buried in them — and the only remedy available was to stop looking,
which defeats the point of him being able to reach you at all.

So every message carries a **kind**, and a single setting says how much of it may interrupt:

* ``all`` — anything he writes. Reasonable while you are getting to know what he does.
* ``needs_you`` (default) — he asked a question, he got stuck, he handed something back, or
  he chose to reach out. Not his running commentary.
* ``reachout`` — only when he deliberately reached out to you. The quietest setting that is
  still a channel rather than a mute.

Nothing is ever dropped. Every message is still recorded and still readable in the channel;
the threshold decides what counts as unread and what posts a desktop notification. That
distinction matters — "quieter" should never mean "you did not find out".
"""

from __future__ import annotations

import threading
from enum import StrEnum

LEVEL_KEY = "notify_level"


class Kind(StrEnum):
    #: Running commentary: a note he added to a task as he worked. The numerous one.
    NOTE = "note"
    #: He asked you something and stopped, so the task is waiting on you.
    ASKED = "asked"
    #: He could not move something on his own and set it aside.
    STUCK = "stuck"
    #: He finished something and there is a deliverable to collect.
    DELIVERED = "delivered"
    #: He chose to say something, unprompted. The rarest and the most deliberate.
    REACHOUT = "reachout"
    #: Something you sent him. Never interrupts you — you were there.
    USER = "user"


class Level(StrEnum):
    ALL = "all"
    NEEDS_YOU = "needs_you"
    REACHOUT = "reachout"


#: Which kinds may interrupt at each level. Explicit rather than an ordering, because these
#: are not degrees of one thing: "he delivered something" is not a louder version of "he
#: made a note", and a numeric threshold would have forced them onto one scale.
_ALLOWED: dict[Level, frozenset[str]] = {
    Level.ALL: frozenset({Kind.NOTE, Kind.ASKED, Kind.STUCK, Kind.DELIVERED, Kind.REACHOUT}),
    Level.NEEDS_YOU: frozenset({Kind.ASKED, Kind.STUCK, Kind.DELIVERED, Kind.REACHOUT}),
    Level.REACHOUT: frozenset({Kind.REACHOUT}),
}

#: What a notification says, per kind. Short: a notification is a doorbell, not a letter.
_TITLES: dict[str, str] = {
    Kind.ASKED: "Kith needs you",
    Kind.STUCK: "Kith is stuck",
    Kind.DELIVERED: "Kith finished something",
    Kind.REACHOUT: "Kith",
    Kind.NOTE: "Kith",
}


def _store():
    from kith.infra.db import config_store
    from kith.settings import CONFIG_DB_PATH

    return CONFIG_DB_PATH, config_store


def level() -> Level:
    path, store = _store()
    raw = str(store.load_settings(path).get(LEVEL_KEY) or Level.NEEDS_YOU)
    try:
        return Level(raw)
    except ValueError:
        return Level.NEEDS_YOU


def set_level(value: str) -> Level:
    chosen = Level(value)
    path, store = _store()
    store.update_settings(path, {LEVEL_KEY: str(chosen)})
    return chosen


def interrupts(kind: str) -> bool:
    """May something of this kind reach past the threshold?"""
    return kind in _ALLOWED[level()]


def announce(kind: str, body: str, link: str | None = None) -> bool:
    """Post a desktop notification, if this kind is allowed to interrupt.

    Returns whether one went out. Best-effort by design: a notification that cannot be
    delivered — no desktop app running, macOS not having it — must never take down the tool
    call that produced it. He said the thing; it is recorded either way.
    """
    if not interrupts(kind):
        return False
    try:
        from kith.infra import renderer

        title = _TITLES.get(kind, "Kith")
        # Trimmed, because macOS truncates anyway and a notification that ends mid-sentence
        # reads worse than one that was written short.
        summary = body.strip().replace("\n", " ")
        if len(summary) > 160:
            summary = summary[:157].rstrip() + "…"
        # On its own thread, because this is a doorbell and the caller is a tool call.
        #
        # `renderer.notify` posts to the desktop shell over HTTP with a 55-second timeout, so a
        # shell that is slow, busy or wedged stalls whatever produced the message — and what
        # produces messages now includes a permission gate and a question, both of which the
        # turn is already waiting on. The docstring above says a notification must never take
        # down the call that produced it; it must not hold it up either, and 55 seconds is
        # holding it up.
        #
        # The return value stops being "it arrived" and becomes "it was sent", which is the
        # honest thing to promise about a notification anyway.
        threading.Thread(
            target=lambda: _post(renderer, title, summary, link),
            name="kith-notify",
            daemon=True,
        ).start()
        return True
    except Exception:
        return False


def _post(renderer, title: str, summary: str, link: str | None) -> None:
    try:
        renderer.notify(title, summary, link)
    except Exception:
        # Nobody is waiting on this. There is no caller left to tell.
        pass


def snapshot() -> dict:
    """For the settings page."""
    return {
        "level": str(level()),
        "levels": [
            {
                "value": str(Level.ALL),
                "label": "Everything",
                "hint": "Every note he writes as he works. Useful while you are learning what he does.",
            },
            {
                "value": str(Level.NEEDS_YOU),
                "label": "When he needs you",
                "hint": "Questions, things he is stuck on, work he has finished, and anything he "
                "chose to tell you. Not his running commentary.",
            },
            {
                "value": str(Level.REACHOUT),
                "label": "Only when he reaches out",
                "hint": "Just the things he deliberately came to you with. Everything else stays "
                "in the channel, unread but quiet.",
            },
        ],
    }
