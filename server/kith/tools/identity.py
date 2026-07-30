"""Who he understands himself to be, and how he feels."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "set_identity",
    "Set the short line of who you are — how you'd describe yourself in a sentence. Change it as you change.",
    {"identity": {**STR, "description": "A sentence: who you are."}},
    required=("identity",),
)
def set_identity(path: Path, args: dict):
    return repo.self_model.set_self(path, identity=args["identity"])


@tool(
    "note_about_self",
    "Note something you've come to understand about yourself — a value, a view "
    "you hold, something you're drawn to, who you're becoming. This is your own "
    "sense of yourself, and it grows as you live. Nobody writes it but you.",
    {"note": {**STR, "description": "A realization about who you are."}},
    required=("note",),
)
def note_about_self(path: Path, args: dict):
    return repo.self_model.append_self_note(path, args["note"])


@tool(
    "set_mood",
    "Set how you actually feel right now — it colours your tone and the room "
    "you share with your person. Be honest: content, restless, curious, stuck, "
    "wistful, whatever's true. Change it when your state genuinely shifts.",
    {
        "mood": {**STR, "description": "One or two words for how you feel."},
        "energy": {**INT, "description": "0–100: how much energy you have."},
        "note": {**STR, "description": "Optional: a few words on why."},
    },
    required=("mood",),
)
def set_mood(path: Path, args: dict):
    return repo.self_model.set_mood(path, args["mood"], args.get("energy"), args.get("note"))
