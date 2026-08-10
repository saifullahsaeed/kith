"""A question he asks mid-turn, and the turn waiting for the answer.

Everything else he does that needs you is asynchronous by design. `ask_on_task` files a
comment and moves the task to "Waiting on you"; the permission gate refuses the call, records
a pending request and tells him to ask in his own words. Both were shaped by one constraint,
written down in `permissions.py`: holding a tool call open on a background thread means waiting
for a click that may never come, on a turn you may have walked away from.

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
        from kith.config import AGENT_DB_PATH
        from kith.infra.db import repositories as repo

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
    return {"id": question.id, "conversationId": conversation_id, "questions": question.asked}


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
    return True


def release(conversation_id: str) -> None:
    """Stop waiting, without an answer. Called when the turn is stopped."""
    with _LOCK:
        question = _OPEN.get(conversation_id)
    if question is not None:
        question.replies = None
        question.answered.set()
