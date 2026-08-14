"""Plumbing shared by every repository: row mapping, keyword search, timestamps, notices."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from kith.infra.db.session import transaction
from kith.kernel import changes

# Common words dropped from search queries so recall matches on meaningful terms.
STOPWORDS = {
    "the",
    "and",
    "for",
    "are",
    "was",
    "were",
    "that",
    "this",
    "with",
    "have",
    "has",
    "how",
    "who",
    "why",
    "when",
    "where",
    "from",
    "about",
    "into",
    "them",
    "their",
    "you",
    "your",
    "user",
    "what",
    "they",
    "its",
    "not",
    "but",
    "can",
    "will",
    "would",
    "should",
    "did",
    "does",
    "any",
    "all",
}


def tokenize(query: str) -> list[str]:
    """Meaningful words from a query (lowercased, ≥3 chars, minus stopwords)."""
    words = re.findall(r"[a-z0-9]+", (query or "").lower())
    return [word for word in words if len(word) >= 3 and word not in STOPWORDS]


def keyword_search(path: Path, table: str, columns: tuple[str, ...], query: str, limit: int):
    """Match rows containing any query token, ranked by how many tokens hit."""
    tokens = tokenize(query)
    with transaction(path) as conn:
        if not tokens:
            return conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        conditions, params = [], []
        for token in tokens:
            like = f"%{token}%"
            for column in columns:
                conditions.append(f"{column} LIKE ?")
                params.append(like)
        rows = conn.execute(f"SELECT * FROM {table} WHERE {' OR '.join(conditions)}", params).fetchall()

    def score(row) -> int:
        haystack = " ".join(str(row[column]) for column in columns).lower()
        return sum(1 for token in tokens if token in haystack)

    return sorted(rows, key=score, reverse=True)[:limit]


# `fetch_row(conn, table, row_id)` used to be here and had no callers. It also interpolated
# the table name straight into the SQL, which is the shape of an injection even when every
# caller today passes a literal — and a dead helper is exactly the one someone reaches for
# later without reading it. Everything goes through the ORM now.


def row_to_dict(row) -> dict[str, Any]:
    return dict(row)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def notifies(kind: str) -> Callable:
    """Publish a `kind` change after a write returns.

    A decorator rather than a call at the end of ten functions, because the eleventh is the one
    somebody forgets — and a board that updates for nine kinds of change and not the tenth is
    worse than one that never updates, since you stop trusting it. The projects repository
    learned that the other way round: the panel subscribed to `project` before anything
    published it, so creating a project still needed a reload.

    Three repositories had this, written out three times and differing only in the string.
    `messages` even called its copy `_notifies_message` because the name was taken in a module
    it could not share with.

    **Swallowed, and that is the load-bearing part.** This is a note *about* a save. It must
    never be the reason one fails, which is why the `try` wraps the publish and nothing else.
    """

    def decorate(write):
        @wraps(write)
        def inner(*args, **kwargs):
            out = write(*args, **kwargs)
            try:
                changes.publish(kind)
            except Exception:
                pass
            return out

        return inner

    return decorate
