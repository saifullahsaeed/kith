"""Plumbing shared by every repository: row mapping, keyword search, timestamps."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kith.infra.db.session import transaction

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


def fetch_row(conn, table: str, row_id: int | None):
    if row_id is None:
        return None
    return conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()


def row_to_dict(row) -> dict[str, Any]:
    return dict(row)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
