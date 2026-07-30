"""Pages, so a list tool can't spend a turn's context on itself.

Every list tool returned everything it had. Measured on one real database:
``read_journal`` came back with 31,890 characters — close to 8,000 tokens — for fifty
entries, and ``list_tasks`` with 15,222 for twenty-four tasks. Seven of the ten took no
limit at all, so there was no way to ask for less.

That cost is not paid once. A tool result stays in the conversation for the rest of the
turn and is re-sent on every subsequent round, so one ``read_journal`` early in a
sixteen-round tick is eight thousand tokens carried sixteen times. It also crowds out the
thing it was fetched for: the round after a big dump has less room to think in.

Three guards, in the order they bite:

1. **A default limit** on every list, low enough to be a glance rather than an archive.
2. **A character budget**, because item count is the wrong unit — twenty short reminders
   and twenty long journal entries are not the same payload, and it is the payload that
   costs. Whichever limit is reached first wins.
3. **Honesty about what was left out.** A truncated list that looks complete is worse
   than a long one: he will conclude there are four tasks and act on that. So a page
   always says how many there are in total and how to get the next one.

The shape is the same for every tool that uses this, so learning it once is enough:

    {"items": [...], "total": 41, "showing": "1-20 of 41", "more": 21, "next_offset": 20}
"""

from __future__ import annotations

import json
from typing import Any

from kith.tools.params import INT, STR

#: Enough to see what is going on, few enough to read. Anything that needs the whole set
#: is a question for a filter, not for a bigger page.
DEFAULT_LIMIT = 20

#: A page may never exceed this many items however large a limit is asked for.
MAX_LIMIT = 100

#: The payload ceiling, in characters — roughly 2,000 tokens. Reached before the item
#: limit whenever rows are long, which is exactly when it matters.
CHAR_BUDGET = 8_000

#: Never return zero rows because the first one is enormous: one oversized row, clearly
#: marked, beats an empty page that reads as "there is nothing here".
MIN_ITEMS = 1


#: Drop these into a tool's parameter schema. Described, because the model has to
#: understand it is being handed a page in order to ask for the next one — but tersely:
#: these three ride on ten tools, and the schemas are re-sent on every single round, so a
#: sentence of explanation here costs more over a tick than the paging saves. Measured at
#: 601 tokens per round when they were written out in full.
LIMIT: dict = {**INT, "description": f"How many (default {DEFAULT_LIMIT}, max {MAX_LIMIT})."}
OFFSET: dict = {**INT, "description": "Skip this many; see next_offset."}
CONTAINS: dict = {**STR, "description": "Only rows containing this text."}

#: Ready-made, since almost every list tool wants exactly these three.
PAGE_PARAMS: dict = {"limit": LIMIT, "offset": OFFSET, "contains": CONTAINS}


def page(rows: list[dict], args: dict, *, default: int = DEFAULT_LIMIT) -> dict:
    """Cut a list down to one honest page.

    Filtering happens before paging, so ``total`` is the number of rows that matched
    rather than the number in the table — otherwise "3 of 41" would be a lie about what
    paging further would find.
    """
    matched = _matching(rows, args.get("contains"))
    offset = max(0, _int(args.get("offset"), 0))
    limit = min(MAX_LIMIT, max(1, _int(args.get("limit"), default)))

    window = matched[offset : offset + limit]
    items = _within_budget(window)
    total = len(matched)
    shown = len(items)
    first = offset + 1 if shown else offset

    result: dict[str, Any] = {
        "items": items,
        "total": total,
        "showing": f"{first}-{offset + shown} of {total}" if shown else f"none of {total}",
    }
    remaining = total - (offset + shown)
    if remaining > 0:
        result["more"] = remaining
        result["next_offset"] = offset + shown
        # Spelled out because the count alone gets skimmed past, and acting on a page as
        # though it were the whole set is the failure this exists to prevent.
        result["note"] = (
            f"{remaining} more not shown. Call again with offset={offset + shown}, "
            "or narrow it with the filters, before concluding anything about the rest."
        )
    if shown < len(window):
        result["note"] = (
            f"Cut to {shown} to stay within a sensible size — the rows are long. "
            f"Call again with offset={offset + shown} for the next one."
        )
    return result


def _matching(rows: list[dict], contains: object) -> list[dict]:
    needle = str(contains or "").strip().lower()
    if not needle:
        return rows
    return [row for row in rows if needle in json.dumps(row, default=str).lower()]


def _within_budget(window: list[dict]) -> list[dict]:
    """Take rows until the character budget is spent."""
    kept: list[dict] = []
    spent = 0
    for row in window:
        size = len(json.dumps(row, default=str))
        if kept and spent + size > CHAR_BUDGET and len(kept) >= MIN_ITEMS:
            break
        kept.append(row)
        spent += size
    return kept


def _int(raw: object, fallback: int) -> int:
    """Models send numbers as strings often enough that this has to be tolerant."""
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
