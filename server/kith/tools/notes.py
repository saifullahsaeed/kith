"""His notebook."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "take_note",
    "Write a note in your notebook.",
    {"title": STR, "body": STR},
    required=("title",),
)
def take_note(path: Path, args: dict):
    return repo.notes.add_note(path, args["title"], args.get("body") or "")


@tool(
    "read_notes",
    "Read your notes. With a query, search; otherwise list the most recent.",
    {"query": STR, "limit": INT},
    required=(),
)
def read_notes(path: Path, args: dict):
    return (
        repo.notes.search_notes(path, args["query"], args.get("limit") or 20)
        if args.get("query")
        else repo.notes.list_notes(path, args.get("limit") or 50)
    )


@tool(
    "update_note",
    "Edit an existing note by its id.",
    {"id": INT, "title": STR, "body": STR},
    required=("id",),
)
def update_note(path: Path, args: dict):
    return repo.notes.update_note(path, args["id"], args.get("title"), args.get("body"))
