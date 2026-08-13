"""A question he asks mid-turn, and the turn waiting for the answer.

This is now the only way he asks for something. It used to be one of three: `ask_on_task` filed a
comment and moved the task to "Waiting on you", and the permission gate refused the call and told
him to ask in his own words. All of it was shaped by one constraint, written down in
`permissions.py`: holding a tool call open on a background thread means waiting for a click that may
never come, on a turn you may have walked away from.

That constraint expired. A turn already runs on its own thread and survives you closing the
window, and since `live_turns` it can be watched again from wherever you come back to. So a
question can now hold the turn where it is, which is the only way to ask something whose answer
changes what happens next. "Which of these three directions?" filed as a task and answered
tomorrow is not the same question.

The waiting is bounded three ways, because a thread parked forever is still a leak:

* answered — the normal path,
* the turn stopped — `release` is called from the stop switch, so Stop stops a turn that is
  waiting exactly as it stops one that is working,
* or the deadline, after which he is told nobody answered and carries on with that as the
  fact. Long enough to make a cup of tea, short enough that a forgotten window does not hold a
  thread until the process dies.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

#: How long a question waits before giving up on being answered.
_DEADLINE_SECONDS = 15 * 60


@dataclass
class Question:
    id: str
    conversation_id: str
    #: `[{"question": str, "options": [{"label": str, "description": str}], "multiple": bool}]`
    asked: list[dict]
    answered: threading.Event = field(default_factory=threading.Event)
    #: One entry per question once answered: `{"chosen": [str], "text": str, "skipped": bool}`.
    replies: list[dict] | None = None
    #: True for a question recovered from a transcript after a restart, where the turn that
    #: asked it is gone. Nothing is waiting on `answered`, so an answer has nowhere to be
    #: returned to — the client sends it as an ordinary message instead, and the next turn picks
    #: it up with the whole conversation in front of it. See `recover_interrupted`.
    interrupted: bool = False


_OPEN: dict[str, Question] = {}
_LOCK = threading.Lock()


def _normalise(raw: list) -> list[dict]:
    """Whatever he passed, in the one shape the interface renders.

    Options may be plain strings or `{label, description}` — a model asked for a list of
    choices writes both, and rejecting the simpler one would mean a tool that fails on the
    obvious call.
    """
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        options = []
        for option in item.get("options") or []:
            if isinstance(option, str):
                options.append({"label": option, "description": ""})
            elif isinstance(option, dict) and option.get("label"):
                options.append(
                    {"label": str(option["label"]), "description": str(option.get("description") or "")}
                )
        question = str(item.get("question") or "").strip()
        if question and options:
            out.append({"question": question, "options": options, "multiple": _wants_several(item)})
    return out


#: Every spelling of "more than one may be picked" a model reaches for. The schema says
#: `multiple`; `multiSelect` is what the convention is elsewhere and what he writes about half
#: the time, and a flag read under only one of its names is a flag that is silently always
#: false — which shows up not as an error but as a question you cannot answer properly.
_SEVERAL = ("multiple", "multiSelect", "multi_select", "multiselect", "allowMultiple", "many")


def _wants_several(item: dict) -> bool:
    for name in _SEVERAL:
        value = item.get(name)
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1"}
        if value is not None:
            return bool(value)
    return False


def ask(conversation_id: str, raw: list, deadline: float = _DEADLINE_SECONDS) -> dict:
    """Put the questions to the person and wait. Returns what they said.

    Blocking on purpose — see the module docstring. The caller is a tool, so the turn is
    already parked inside `_run` waiting for it, and the interface has had the `tool_call`
    event describing these questions since before this ran.
    """
    asked = _normalise(raw)
    if not asked:
        return {"ok": False, "error": "ask needs at least one question with at least one option"}

    question = Question(id=uuid.uuid4().hex[:12], conversation_id=conversation_id, asked=asked)
    _tell_them(conversation_id, asked)
    with _LOCK:
        # One open question per conversation. A second would put two cards on screen with no
        # way to tell which turn is waiting on which.
        previous = _OPEN.get(conversation_id)
        _OPEN[conversation_id] = question
    if previous is not None:
        previous.replies = None
        previous.answered.set()

    try:
        if not question.answered.wait(timeout=deadline):
            return {
                "ok": True,
                "answered": False,
                "note": (
                    "Nobody answered within the time allowed. Carry on with what you have, and "
                    "say which way you went and why."
                ),
            }
        if question.replies is None:
            return {"ok": True, "answered": False, "note": "The turn was stopped before this was answered."}
        return {"ok": True, "answered": True, "answers": question.replies}
    finally:
        with _LOCK:
            if _OPEN.get(conversation_id) is question:
                del _OPEN[conversation_id]


def _tell_them(conversation_id: str, asked: list[dict]) -> None:
    """Raise the badge and the desktop notification, pointing at the chat that is waiting.

    Without this the question is only visible in the conversation it was asked in, so walking
    away — or simply being in another chat when he asks — means a turn parked for fifteen
    minutes on a card nobody knew existed. The link is the conversation and not the inbox,
    because the answer can only be given in one place.

    Best-effort, and deliberately so: a notification that cannot be delivered must never take
    down the tool call that produced it. He still asked; the card is still there.
    """
    try:
        from kith.infra.db import repositories as repo
        from kith.settings import AGENT_DB_PATH

        first = asked[0]["question"]
        more = f" (+{len(asked) - 1} more)" if len(asked) > 1 else ""
        repo.messages.add_message(
            AGENT_DB_PATH,
            f"I need an answer before I can carry on: {first}{more}",
            link=f"/chat/{conversation_id}",
            kind="asked",
        )
    except Exception:
        pass


def open_question(conversation_id: str) -> dict | None:
    """What this conversation is waiting to be asked, if anything.

    Read when a client attaches to a turn already in flight: the `tool_call` that raised the
    card may have gone past before it was watching, and a question nobody can see is a turn
    that looks hung.
    """
    with _LOCK:
        question = _OPEN.get(conversation_id)
    if question is None:
        return None
    return {
        "id": question.id,
        "conversationId": conversation_id,
        "questions": question.asked,
        # Nothing is waiting on this one, so answering it cannot hand anything back — the client
        # sends the answer as an ordinary message instead. See `recover_interrupted`.
        "interrupted": question.interrupted,
    }


def answer(question_id: str, replies: list) -> bool:
    """Hand back what they chose. False if that question is no longer open."""
    with _LOCK:
        question = next((q for q in _OPEN.values() if q.id == question_id), None)
    if question is None:
        return False
    question.replies = [
        {
            "chosen": [str(c) for c in (reply.get("chosen") or [])],
            "text": str(reply.get("text") or ""),
            "skipped": bool(reply.get("skipped")),
        }
        for reply in replies or []
        if isinstance(reply, dict)
    ]
    question.answered.set()
    # A live question is cleared by `ask`'s own `finally` as the tool returns. A recovered one
    # has no `ask` running — that turn died with the last process — so nothing would ever take
    # it out of `_OPEN`, and the endpoint would go on offering a card that has been answered
    # until some later turn happened to ask something else and overwrite it.
    if question.interrupted:
        with _LOCK:
            if _OPEN.get(question.conversation_id) is question:
                del _OPEN[question.conversation_id]
    return True


def release(conversation_id: str) -> None:
    """Stop waiting, without an answer. Called when the turn is stopped."""
    with _LOCK:
        question = _OPEN.get(conversation_id)
    if question is not None:
        question.replies = None
        question.answered.set()


#: What the interrupted turn's missing tool result says, written on the next start.
_INTERRUPTED = (
    "The server restarted while this question was waiting, so it was never answered and the "
    "turn that asked it ended there. Their answer, if they give one, arrives as an ordinary "
    "message — read it as the answer to this."
)


def recover_interrupted() -> int:
    """Close the books on questions the process died holding, and put the cards back.

    A parked question lived only in `_OPEN` and the turn waiting on it was a daemon thread, so
    a restart took both with nothing written down: the transcript stopped mid-tool-call, there
    was no result, no error, and no turn-log row. Measured on 2026-08-13 — a question asked at
    11:33:15 with a fifteen-minute deadline, a server that came up at 11:41:56, and a
    conversation whose file simply ends. Five of them across the transcripts, two of which were
    `remember this`, asked again by hand minutes later because nothing said what had happened.

    The transcript is the persistence, which is why there is no table here. The `tool_call` is
    written before the tool runs and carries the whole question, so a call with no result *is*
    the record of an interrupted ask — no second copy to keep in step, and no migration.

    Two things happen for each one:

    * the missing `tool_result` is written, so the turn's books are closed and the history is
      well-formed. Until it is, `conversations.full_messages` drops the orphaned call entirely
      (it has to — a provider refuses a `tool_calls` message it cannot pair), so the next turn
      cannot see that it ever asked. That is the whole of why "remember this" had to be typed
      twice: he had no record of asking what to remember.
    * the question goes back in `_OPEN`, marked `interrupted`, so the card returns rather than
      the person being left to guess what he wanted.

    Returns how many were recovered, for the line the server prints at startup.
    """
    from kith.services import conversations

    found = 0
    for conversation_id in conversations.interrupted_asks():
        # Checked before anything is written, not after. A conversation already holding a live
        # question has a turn parked on it right now, and writing a "the server restarted"
        # result into its transcript would be a lie about a turn that is still running — and
        # would pair off the call it is about to answer for itself.
        with _LOCK:
            if conversation_id in _OPEN:
                continue
        asked = _asked_in(conversation_id)
        if asked is None:
            continue
        call_id, questions_asked = asked
        conversations.record_event(
            conversation_id,
            "tool_result",
            {"id": call_id, "name": "ask", "result": {"ok": True, "answered": False, "note": _INTERRUPTED}},
        )
        with _LOCK:
            _OPEN[conversation_id] = Question(
                id=uuid.uuid4().hex[:12],
                conversation_id=conversation_id,
                asked=questions_asked,
                interrupted=True,
            )
        found += 1
    return found


def _asked_in(conversation_id: str) -> tuple[str, list[dict]] | None:
    """The unanswered `ask` at the end of this transcript: its call id and its questions."""
    from kith.services import conversations

    pending: dict[str, dict] = {}
    for entry in conversations.read(conversation_id):
        kind = entry.get("type")
        if kind == "message" and entry.get("role") == "user":
            pending = {}
        elif kind == "tool_call" and entry.get("name") == "ask":
            pending[str(entry.get("id") or "")] = entry
        elif kind == "tool_result":
            pending.pop(str(entry.get("id") or ""), None)
    if not pending:
        return None
    call_id, entry = next(reversed(pending.items()))
    asked = _normalise((entry.get("arguments") or {}).get("questions") or [])
    return (call_id, asked) if asked else None
