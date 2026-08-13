"""Asking a question you have to answer before he goes on."""

from __future__ import annotations

from pathlib import Path

from kith.kernel import session_context
from kith.services import questions
from kith.tools.registry import tool

_QUESTIONS = {
    "type": "array",
    "description": (
        "One to four questions. Ask several at once when they are all about the same decision "
        "— it is one interruption instead of four."
    ),
    "items": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question, in one line."},
            "options": {
                "type": "array",
                "description": (
                    "Two to five concrete choices. Real options, not 'yes'/'no' dressed up — "
                    "each one should describe a different thing you would go and do."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "The choice, in a few words."},
                        "description": {
                            "type": "string",
                            "description": "What picking it would mean. Optional, one line.",
                        },
                    },
                    "required": ["label"],
                },
            },
            # Named `multiSelect` because that is the convention everywhere else and therefore
            # what he writes unprompted. The service reads it under six spellings anyway — a
            # flag understood under only one of its names is a flag that is silently always
            # false, which surfaces as a question the person cannot answer properly rather than
            # as an error anybody would notice.
            "multiSelect": {
                "type": "boolean",
                "description": (
                    "Set true when more than one option can be picked. Do set it when the "
                    "choices are not exclusive — 'which of these should I include' is almost "
                    "always several, and leaving it off makes the first click the answer."
                ),
            },
        },
        "required": ["question", "options"],
    },
}


@tool(
    "ask",
    "Ask your person something and WAIT for the answer before doing anything else. Two uses. "
    "One: a real fork, where two answers would send you to different work — not reassurance, "
    "and not having a decision confirmed back to you. Two: you are BLOCKED and need something "
    "only they can give — a credential, a decision, an action in a system you cannot reach. "
    "Being blocked is the case people most often get wrong: the alternative to asking is not "
    "waiting, it is ending your turn, and from their side that is indistinguishable from "
    "giving up. If you are about to write 'I need X from you' or 'I'll wait until Y', ask "
    "instead — that is what this is. First make sure you are actually blocked: a condition you "
    "could go and check yourself is not a blocker, it is your next step. "
    "They can pick an option, write something else, or skip. Skipping means they would rather "
    "you chose: say what you chose and why. This holds the turn, so one good question beats "
    "three cautious ones.",
    {"questions": _QUESTIONS},
    required=("questions",),
)
def ask(path: Path, args: dict):
    """Put the questions on screen and block until they are answered.

    The blocking is the feature. Everything else that needs the person is asynchronous — a task
    comment, a permission request — because none of it can hold a turn open. This can, now that
    a turn survives the window closing and can be watched again from wherever you come back to.

    The conversation comes from the turn's own context rather than an argument, so he cannot ask
    a question into a conversation he is not in.
    """
    conversation_id = session_context.current()
    if not conversation_id:
        # A turn with no conversation has nowhere to draw the card, so waiting would hang on
        # something nobody could ever answer.
        return {"ok": False, "error": "There is nobody to ask here — answer with what you have."}
    return questions.ask(conversation_id, args.get("questions") or [])
