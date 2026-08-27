"""Facts he chooses to keep, and semantic recall over them."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlalchemy import delete, select

from kith.domain.enums import MEMORY_LEVELS
from kith.infra.db.engine import as_dict, changed, session
from kith.infra.db.models import Memory
from kith.infra.db.support import keyword_search, utc_now_iso
from kith.infra.db.vectors import cosine, pack_vector, unpack_vector


def _scope(project_id: int | None):
    """The WHERE that keeps a project's chat to its own memories plus the global ones.

    A memory with no project is global and shows everywhere. One with a project shows only when
    the conversation is in that project. `recall` never uses this — fetching should reach any
    memory, which is the whole point of the split: the injected set is small and on-topic, and
    everything else is a search away rather than a tax on every turn.
    """
    if project_id is None:
        return Memory.project_id.is_(None)
    return (Memory.project_id.is_(None)) | (Memory.project_id == int(project_id))


def add_memory(
    path: Path,
    content: str,
    tags: list[str] | None = None,
    importance: int = 0,
    level: str = "recall",
    embedding: list[float] | None = None,
    project_id: int | None = None,
) -> dict:
    if level not in MEMORY_LEVELS:
        level = "recall"
    with session(path) as db:
        row = Memory(
            content=content,
            tags=json.dumps(tags or []),
            importance=int(importance),
            level=level,
            created_at=utc_now_iso(),
            embedding=pack_vector(embedding),
            project_id=int(project_id) if project_id else None,
        )
        db.add(row)
        db.flush()
        return _public(row)


def search_memories(path: Path, query: str, limit: int = 20) -> list[dict]:
    # Still the hand-rolled ranker: it scores by how many query tokens a row
    # matches, which no ORM query expresses more clearly.
    rows = keyword_search(path, "memories", ("content", "tags"), query, limit)
    return [_public_row(row) for row in rows]


def semantic_search(path: Path, query_vec: list[float], limit: int = 20) -> list[dict]:
    """Rank memories that have an embedding by cosine similarity to ``query_vec``.

    Brute-force over every embedded memory — fine at this scale (hundreds of
    768-dim vectors). Each result carries a ``score`` in [0, 1]. Memories without
    an embedding are invisible here; the caller falls back to keyword search.
    """
    with session(path) as db:
        rows = db.scalars(select(Memory).where(Memory.embedding.is_not(None))).all()
        scored = []
        for row in rows:
            similarity = cosine(query_vec, unpack_vector(row.embedding))
            if similarity > 0:
                scored.append((similarity, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [{**_public(row), "score": round(score, 3)} for score, row in scored[:limit]]


def set_memory_embedding(path: Path, memory_id: int, embedding: list[float]) -> None:
    with session(path) as db:
        row = db.get(Memory, memory_id)
        if row is not None:
            row.embedding = pack_vector(embedding)


def memories_missing_embedding(path: Path) -> list[tuple[int, str]]:
    """(id, content) for every memory without a vector yet — for backfilling."""
    with session(path) as db:
        return [
            (row_id, content)
            for row_id, content in db.execute(
                select(Memory.id, Memory.content).where(Memory.embedding.is_(None))
            ).all()
        ]


def list_memories(path: Path, limit: int = 50) -> list[dict]:
    with session(path) as db:
        rows = db.scalars(select(Memory).order_by(Memory.id.desc()).limit(limit)).all()
        return [_public(row) for row in rows]


def memories_by_level(path: Path, level: str, limit: int = 50, project_id: int | None = None) -> list[dict]:
    """Memories at one level, scoped to what the conversation is about.

    `project_id` is the project the conversation is in, or None for a chat that is in none.
    Global memories (``project_id IS NULL``) come back in both cases — they are the facts that
    hold whatever the work is. A project's own memories join them only when the chat is in that
    project; another project's are left for `recall`, which is unscoped, to reach. This is the
    same line the project region draws for the board: what you are on, plus what is universal,
    and nothing from the project next door.
    """
    query = select(Memory).where(Memory.level == level)
    query = query.where(_scope(project_id))
    query = query.order_by(Memory.importance.desc(), Memory.id.desc()).limit(limit)
    with session(path) as db:
        return [_public(row) for row in db.scalars(query).all()]


def recent_memories(path: Path, limit: int = 8, project_id: int | None = None) -> list[dict]:
    """The latest non-core memories — the 'back of your mind' that surfaces on its own.

    Scoped like :func:`memories_by_level`. This is where the bleed actually showed: "recent"
    was the eight most recent non-core memories on the whole machine, so whichever project was
    touched last coloured every other project's chats. Now the recent memories of *this* project
    surface, alongside any global ones, and another project's stay in `recall`.
    """
    query = (
        select(Memory)
        .where(Memory.level != "core")
        .where(_scope(project_id))
        .order_by(Memory.id.desc())
        .limit(limit)
    )
    with session(path) as db:
        return [_public(row) for row in db.scalars(query).all()]


def set_memory_level(path: Path, memory_id: int, level: str) -> dict | None:
    if level not in MEMORY_LEVELS:
        raise ValueError(f"level must be one of {MEMORY_LEVELS}")
    with session(path) as db:
        row = db.get(Memory, memory_id)
        if row is None:
            return None
        row.level = level
        db.flush()
        return _public(row)


def update_memory(
    path: Path, memory_id: int, content: str | None = None, importance: int | None = None
) -> dict | None:
    with session(path) as db:
        row = db.get(Memory, memory_id)
        if row is None:
            return None
        if content is not None:
            row.content = content
        if importance is not None:
            row.importance = int(importance)
        db.flush()
        return _public(row)


def delete_memory(path: Path, memory_id: int) -> bool:
    with session(path) as db:
        return changed(db.execute(delete(Memory).where(Memory.id == memory_id))) > 0


def _public(row: Memory) -> dict:
    """A memory as callers expect it: tags decoded, and the raw vector withheld.

    The embedding is dropped deliberately — it is a few KB of float noise that
    would otherwise land in an API response and, worse, in the model's context.
    """
    data = as_dict(row, drop=("embedding",))
    data["tags"] = _tags(data.get("tags"))
    return data


def _public_row(row: sqlite3.Row) -> dict:
    """Same shape, for a raw sqlite3.Row coming back from the keyword ranker."""
    data = {key: row[key] for key in row.keys() if key != "embedding"}  # noqa: SIM118 - sqlite3.Row has no __iter__ over keys
    data["tags"] = _tags(data.get("tags"))
    return data


def _tags(raw: object) -> list[str]:
    try:
        parsed = json.loads(raw if isinstance(raw, str | bytes) else "[]")
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []
