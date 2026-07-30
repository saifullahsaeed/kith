"""What he is wondering about."""

from __future__ import annotations

from pathlib import Path

from kith.domain.enums import CURIOSITY_STATUSES
from kith.infra.db import repositories as repo
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "wonder",
    "Note something you're curious about — anything you'd like to understand, "
    "not just what's in front of you. These are yours; they're the seeds of "
    "your own interests. Follow them in your own time.",
    {
        "topic": {**STR, "description": "What you're curious about."},
        "note": {**STR, "description": "Optional: why, or what you already wonder."},
    },
    required=("topic",),
)
def wonder(path: Path, args: dict):
    return repo.curiosities.add_curiosity(path, args["topic"], args.get("note") or "")


@tool(
    "list_curiosities",
    "See the things you're curious about and where each stands.",
    {"status": {**STR, "enum": list(CURIOSITY_STATUSES)}, **PAGE_PARAMS},
    required=(),
)
def list_curiosities(path: Path, args: dict):
    return paging.page(repo.curiosities.list_curiosities(path, args.get("status")), args)


@tool(
    "update_curiosity",
    "Update a curiosity — mark it 'exploring'/'explored'/'dropped', or record what you've learned about it.",
    {
        "id": INT,
        "status": {**STR, "enum": list(CURIOSITY_STATUSES)},
        "note": {**STR, "description": "What you found, or where your thinking is."},
    },
    required=("id",),
)
def update_curiosity(path: Path, args: dict):
    return repo.curiosities.update_curiosity(path, args["id"], args.get("status"), args.get("note"))
