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
    "Add an entry to your journal — a running log of your own thoughts and actions.",
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
