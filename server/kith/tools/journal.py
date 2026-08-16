"""His running log of what he did."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import STR
from kith.tools.registry import tool


@tool(
    "journal",
    "Add an entry to your journal — a running log of your own thoughts and actions. "
    "This is the one for narration: what you did, what you were thinking, how it went. "
    "It is a record in time order, and you read it back to remember a stretch of work. "
    "For a FACT that should still be true in a month — how they like something, what a "
    "system is for, a decision and its reason — use `remember` instead: that is retrieved "
    "by relevance and surfaces on its own, and a fact buried in a day's narration will not.",
    {"entry": STR},
    required=("entry",),
)
def journal(path: Path, args: dict):
    return repo.journal.add_journal(path, args["entry"])


@tool(
    "read_journal",
    "Read your most recent journal entries.",
    {**PAGE_PARAMS},
    required=(),
)
def read_journal(path: Path, args: dict):
    # The worst offender before paging: fifty entries measured 31,890 characters, near
    # 8,000 tokens, re-sent on every later round of the turn.
    return paging.page(repo.journal.list_journal(path, 200), args, default=10)
