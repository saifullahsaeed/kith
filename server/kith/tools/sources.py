"""What his person has given him to read."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools import sources
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "search_sources",
    "Search the links and documents your person has given you to read. Use this "
    "when they ask about something they've shared, or when it might help. Returns "
    "the closest sources with snippets.",
    {"query": {**STR, "description": "What to look for."}, "limit": INT},
    required=("query",),
)
def search_sources(path: Path, args: dict):
    return sources.search(path, args["query"], args.get("limit") or 5)


@tool(
    "read_source",
    "Read the full text of one of the sources your person gave you, by its id.",
    {"id": INT},
    required=("id",),
)
def read_source(path: Path, args: dict):
    return repo.sources.get_source(path, args["id"]) or {"note": "No such source."}
