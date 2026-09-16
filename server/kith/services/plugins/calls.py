"""Asking a live surface something, and waiting for it to answer.

This is the one genuinely new runtime primitive in the plugin design, and it is built last on
purpose — everything that did not need it works without it. `services/questions.py` is the shape
it copies, because it is the same wait with a frame where the person was: park the turn, publish
that something is pending, and let a reply or a deadline wake it.

**Three clocks, and they cover three different failures.** Getting this wrong is how a turn hangs
on a window that closed.

=====================  ==================  ===========================================
clock                  value               the failure it covers
=====================  ==================  ===========================================
renderer deadline      the command's own   the frame is slow or hung; the renderer
                       ``timeout_ms``      answers on its behalf and drops a late reply
server deadline        ``DEADLINE``        the *renderer* is gone — window closed,
                                           laptop asleep — so nobody is running the
                                           renderer's clock at all
unattended             checked *before*    a scheduled turn with no person and no
                       parking             window; refuses at once rather than parking
                                           a background thread on nothing
=====================  ==================  ===========================================

The ten-second ceiling on a command's own timeout is because **the model is parked inside
`run_tool` while this waits.** `ask` waits fifteen minutes because a person is answering; a frame
is not a person, and a surface that needs ten seconds to answer a question is a surface with a
bug rather than one being thoughtful.

**The frame is never killed on a timeout.** That would cost the person their scroll position and
anything half-typed, to fix a problem the host already fixed by refusing the late reply.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field

from kith.kernel import changes, live_turns, session_context

#: How long the *server* waits, whatever the command asked for.
#:
#: Strictly longer than any renderer clock, because it is covering a different failure: the
#: renderer's own deadline answers for a slow frame, and this one answers when there is no
#: renderer left to run that deadline. Twenty seconds is long enough that it never fires while
#: a renderer is alive and short enough that a closed window costs a turn one pause.
DEADLINE = 20.0

#: Consecutive timeouts before a mount is treated as unresponsive. After this the pane offers a
#: reload rather than every call waiting out its full deadline against a frame that is not
#: answering.
UNRESPONSIVE_AFTER = 3

#: How many calls may be open against one frame. Four is more than any real surface needs, and a
#: bound means a model in a loop cannot fill the table.
MAX_INFLIGHT = 4


@dataclass
class Call:
    """One question put to a surface, and somewhere to put the answer."""

    id: str
    conversation_id: str
    plugin: str
    command: str
    view: str
    instance: str
    args: dict
    #: Who answers this. `surface` is a live frame; `host` is the app itself.
    #:
    #: **Both consumers poll the same route, and `pending` claims what it hands out** — so
    #: without this a mounted frame would claim a host effect it has no way to perform, and the
    #: call would sit until its deadline with the app never seeing it. The kind is what keeps
    #: the two collectors out of each other's queue.
    kind: str = "surface"
    #: Whether delivering this twice is safe. A reload that cannot prove a call ran reports
    #: uncertainty rather than repeating a side effect.
    repeatable: bool = False
    timeout_ms: int = 3_000
    answered: threading.Event = field(default_factory=threading.Event)
    reply: dict | None = None
    #: Set when something answered *for* the frame because the frame would not.
    #:
    #: **`reply is None` cannot carry this and that was a real bug.** A stopped turn and a
    #: renderer reporting a hung frame both left `reply` unset, so the commonest surface failure
    #: there is reached the model as "The turn was stopped before that was answered" — a sentence
    #: about something the person did, for something the person had not done. A third state costs
    #: one field and makes both sentences true.
    failed: bool = False
    #: Set once a renderer has taken it, so a second window does not answer the same call.
    claimed_by: str = ""

    def public(self) -> dict:
        """What a renderer is told. The arguments are already coerced against the declaration."""
        return {
            "id": self.id,
            "conversation": self.conversation_id,
            "plugin": self.plugin,
            "command": self.command,
            "view": self.view,
            "instance": self.instance,
            "kind": self.kind,
            "args": self.args,
            "timeoutMs": self.timeout_ms,
            "repeatable": self.repeatable,
        }


_OPEN: dict[str, Call] = {}
_LOCK = threading.Lock()
#: mount key -> consecutive timeouts. Reset by any answer.
_MISSES: dict[str, int] = {}


def ask(call: Call) -> dict:
    """Put a call to a surface and wait. Always returns a dict, never raises.

    Returns the `{ok, result}` envelope the tool loop speaks, so a refusal reads as a tool
    result he can act on rather than an exception the stream has to survive.
    """
    # Before parking anything. A scheduled turn at four in the morning has no window, so a wait
    # here would hold a background thread on a question drawn on nobody's screen — the failure
    # `permissions._wait_for` and `questions.ask` both carry a comment about.
    if session_context.unattended():
        return _refused(
            "no_renderer",
            "Nobody is here and no window is open, so nothing could do that. Carry on and say "
            "what you skipped.",
        )
    if not live_turns.current(call.conversation_id):
        return _refused("no_renderer", "There is no live turn to hold open for that.")

    key = _mount_key(call)
    with _LOCK:
        if _MISSES.get(key, 0) >= UNRESPONSIVE_AFTER:
            # Fail fast rather than making every subsequent call wait out its whole deadline
            # against a frame that has stopped answering.
            return _refused(
                "unresponsive",
                f"The {call.view} surface has stopped answering. Reload it from its tab, or "
                f"carry on without it.",
            )
        mine = [one for one in _OPEN.values() if _mount_key(one) == key]
        if len(mine) >= MAX_INFLIGHT:
            return _refused("busy", f"The {call.view} surface already has {len(mine)} questions open.")
        _OPEN[call.id] = call

    # The renderer learns there is something to deliver. Published outside the lock and before
    # the wait, so the thing that answers is not queued behind the thing waiting.
    changes.publish("plugin_call", conversation=call.conversation_id)
    try:
        # `DEADLINE`, not `min(DEADLINE, the command's own)`. The two clocks cover different
        # failures — see the table at the top — and the renderer runs the inner one. Taking the
        # smaller of the two made the outer clock fire first or level with the inner one in every
        # case the ceiling allows (10s against 20s), so the *server* answered for a slow frame
        # while the renderer's own answer was still in flight, and the deadline that exists for a
        # window that has gone away never once ran to its length.
        if not call.answered.wait(timeout=DEADLINE):
            with _LOCK:
                _MISSES[key] = _MISSES.get(key, 0) + 1
            return _refused(
                "slow",
                f"The {call.view} surface did not answer within {call.timeout_ms}ms. Carry on "
                f"and say what you skipped.",
            )
        if call.failed:
            # The renderer answering for a frame that did not. Counted the same way a server-side
            # timeout is, because it is the same fact arriving by a faster route — and without
            # this `UNRESPONSIVE_AFTER` could never trip while a window was open, which is every
            # case it was written for.
            with _LOCK:
                _MISSES[key] = _MISSES.get(key, 0) + 1
            return _refused(
                "slow",
                f"The {call.view} surface did not answer within {call.timeout_ms}ms. Carry on "
                f"and say what you skipped.",
            )
        if call.reply is None:
            return _refused("stopped", "The turn was stopped before that was answered.")
        with _LOCK:
            _MISSES.pop(key, None)
        return {"ok": True, "result": {"from": call.plugin, "value": call.reply}}
    finally:
        with _LOCK:
            _OPEN.pop(call.id, None)
        # And it is no longer pending. A widget told when a call opens and not when it closes
        # shows a question that has been answered until something else happens to move.
        changes.publish("plugin_call", conversation=call.conversation_id)


def pending(conversation_id: str = "", client: str = "", kind: str = "") -> list[dict]:
    """Calls waiting for a surface to answer.

    Claimed as they are handed out, so two windows on one backend do not both deliver the same
    call and race to answer it — the person would click in one and watch it act in the other.
    A claim is not a lock: if that renderer never answers, the deadline still fires.
    """
    with _LOCK:
        out = []
        for call in _OPEN.values():
            if kind and call.kind != kind:
                continue
            if conversation_id and call.conversation_id != conversation_id:
                continue
            if call.claimed_by and call.claimed_by != client:
                continue
            if client:
                call.claimed_by = client
            out.append(call.public())
        return out


def held(call_id: str) -> Call | None:
    """The open call, or None. For a caller that has to know *which* command is being answered
    before it can shape the answer against that command's declaration."""
    with _LOCK:
        return _OPEN.get(call_id)


def reply(call_id: str, value: dict | None, *, ok: bool = True) -> bool:
    """Record a surface's answer and wake whatever is waiting on it.

    The verdict is written under the lock and the event is set **outside** it. Waking a waiter
    hands it a thread that wants the lock immediately, so setting the event while holding it
    means the woken thread blocks on the waker — which is why `permissions.approve` does the
    same.
    """
    with _LOCK:
        call = _OPEN.get(call_id)
        if call is None or call.answered.is_set():
            return False
        if ok:
            call.reply = dict(value or {})
        else:
            # Not `reply = None`. That is what `release` leaves behind when a turn is stopped,
            # and one sentinel for two facts made every frame timeout report itself as the
            # person having pressed stop.
            call.failed = True
    call.answered.set()
    return True


def release(conversation_id: str) -> None:
    """Give up every call parked on this conversation.

    Beside `questions.release` and `permissions.release_waiting` on the stop path, and for the
    same reason: a turn parked on a frame is not reading the stop switch, so stopping it has to
    reach in and wake it.
    """
    with _LOCK:
        going = [call for call in _OPEN.values() if call.conversation_id == conversation_id]
        for call in going:
            call.reply = None
    for call in going:
        call.answered.set()


def forget_plugin(plugin: str) -> None:
    """Abandon every call to a plugin that is being disabled or removed."""
    with _LOCK:
        going = [call for call in _OPEN.values() if call.plugin == plugin]
        for call in going:
            call.reply = None
        for key in [one for one in _MISSES if one.startswith(f"{plugin}/")]:
            _MISSES.pop(key, None)
    for call in going:
        call.answered.set()


def forget_misses(plugin: str = "", view: str = "", instance: str = "") -> None:
    """Give a surface its second chance.

    **Without this, three timeouts made a surface permanently unusable**, and the message
    telling you to reload it was a lie. The only thing that cleared a miss was a successful
    answer — and the fast-fail above meant no answer could be attempted, so the state it was
    protecting against became the state it created. A deadlock reached by three slow replies.

    Called from `documents.mount`, so a reload clears it, which is what the error tells a person
    to do. With no arguments it clears everything, which is what a test between cases wants.
    """
    with _LOCK:
        if not plugin:
            _MISSES.clear()
            return
        _MISSES.pop(f"{plugin}/{view}/{instance}", None)


def new(conversation_id: str, plugin: str, command: str, view: str, args: dict, **rest) -> Call:
    return Call(
        id="pc" + secrets.token_urlsafe(9),
        conversation_id=conversation_id,
        plugin=plugin,
        command=command,
        view=view,
        instance=str(rest.get("instance") or ""),
        args=args,
        repeatable=bool(rest.get("repeatable", False)),
        timeout_ms=int(rest.get("timeout_ms", 3_000)),
        kind=str(rest.get("kind") or "surface"),
    )


def _mount_key(call: Call) -> str:
    return f"{call.plugin}/{call.view}/{call.instance}"


def _refused(code: str, why: str) -> dict:
    return {"ok": False, "error": why, "code": code}
