"""His notebook."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
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
    {"query": STR, **PAGE_PARAMS},
    required=(),
)
def read_notes(path: Path, args: dict):
    # Fetch generously and page here, so 'total' counts what matched rather than what
    # a hardcoded ceiling happened to let through.
    rows = (
        repo.notes.search_notes(path, args["query"], 200)
        if args.get("query")
        else repo.notes.list_notes(path, 200)
    )
    return paging.page(rows, args)


@tool(
    "update_note",
    "Edit an existing note by its id.",
    {"id": INT, "title": STR, "body": STR},
    required=("id",),
)
def update_note(path: Path, args: dict):
    return repo.notes.update_note(path, args["id"], args.get("title"), args.get("body"))
