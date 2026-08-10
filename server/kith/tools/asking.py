"""Asking a question you have to answer before he goes on."""

from __future__ import annotations

from pathlib import Path

from kith.services import questions, session_context
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
            "multiple": {
                "type": "boolean",
                "description": "True when more than one option can be picked at once.",
            },
        },
        "required": ["question", "options"],
    },
}


@tool(
    "ask",
    "Ask your person something and WAIT for the answer before doing anything else. Use it at a "
    "real fork — when two answers would send you to different work — and not for reassurance "
    "or to have a decision confirmed back to you. They can pick an option, write something "
    "else, or skip. Skipping means they would rather you chose: say what you chose and why. "
    "This holds the turn, so one good question beats three cautious ones.",
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
