"""The people he knows."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools.params import STR
from kith.tools.registry import tool


@tool(
    "note_about",
    "Remember something about a person in your life — chiefly the one you talk "
    "to. Builds up who they are over time (their name, what they like, your "
    "history, how they prefer you) so you're not relearning them. Creates the "
    "person if they're new.",
    {
        "name": {**STR, "description": "Who this is about."},
        "note": {**STR, "description": "What you've learned or noticed about them."},
    },
    required=("name", "note"),
)
def note_about(path: Path, args: dict):
    return repo.people.append_person_note(path, args["name"], args["note"])


@tool(
    "recall_person",
    "Recall everything you know about a person by name.",
    {"name": STR},
    required=("name",),
)
def recall_person(path: Path, args: dict):
    return repo.people.get_person(path, args["name"]) or {"note": "You don't know them yet."}
