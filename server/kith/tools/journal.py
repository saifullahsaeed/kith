"""His running log of what he did."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "journal",
    "Add an entry to your journal — a running log of your own thoughts and actions.",
    {"entry": STR},
    required=("entry",),
)
def journal(path: Path, args: dict):
    return repo.journal.add_journal(path, args["entry"])


@tool(
    "read_journal",
    "Read your most recent journal entries.",
    {"limit": INT},
    required=(),
)
def read_journal(path: Path, args: dict):
    return repo.journal.list_journal(path, args.get("limit") or 50)
