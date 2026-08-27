"""Links and documents his person fed him, chunked and embedded."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from sqlalchemy import delete, func, select

from kith.infra.db.engine import as_dict, changed, session
from kith.infra.db.models import Source, SourceChunk
from kith.infra.db.support import utc_now_iso
from kith.infra.db.vectors import cosine, pack_vector, unpack_vector

# How much of a match to show. A snippet is there to let him decide whether to open
# the full source, not to substitute for reading it.
_SOURCE_SNIPPET = 500
_CHUNK_SNIPPET = 600


def add_source(
    path: Path, title: str, origin: str, content: str, embedding: list[float] | None = None
) -> dict:
    with session(path) as db:
        row = Source(
            title=title,
            origin=origin,
            content=content,
            created_at=utc_now_iso(),
            embedding=pack_vector(embedding),
        )
        db.add(row)
        db.flush()
        return as_dict(row, drop=("embedding",))


def list_sources(path: Path, limit: int = 200) -> list[dict]:
    """Light list for the panel — metadata only, no full content or vector.

    Selects `length(content)` instead of the content itself on purpose: an ingested
    document can be hundreds of KB, and the panel only needs to show how big it is.
    """
    query = (
        select(
            Source.id,
            Source.title,
            Source.origin,
            Source.created_at,
            func.length(Source.content).label("chars"),
        )
        .order_by(Source.id.desc())
        .limit(limit)
    )
    with session(path) as db:
        return [dict(row._mapping) for row in db.execute(query).all()]


def get_source(path: Path, source_id: int) -> dict | None:
    with session(path) as db:
        row = db.get(Source, source_id)
        return as_dict(row, drop=("embedding",)) if row else None


def search_sources(path: Path, query_vec: list[float], limit: int = 5) -> list[dict]:
    """Rank ingested sources by cosine similarity; returns snippets + scores."""
    with session(path) as db:
        rows = db.scalars(select(Source).where(Source.embedding.is_not(None))).all()
        scored = _rank(rows, query_vec, lambda row: row.embedding, limit)
        return [
            {
                "id": row.id,
                "title": row.title,
                "origin": row.origin,
                "snippet": row.content[:_SOURCE_SNIPPET],
                "score": score,
            }
            for score, row in scored
        ]


def add_source_chunk(path: Path, source_id: int, ord: int, text: str, embedding: list[float] | None) -> None:
    with session(path) as db:
        db.add(SourceChunk(source_id=source_id, ord=ord, text=text, embedding=pack_vector(embedding)))


def search_source_chunks(path: Path, query_vec: list[float], limit: int = 5) -> list[dict]:
    """Rank the most relevant passages across all sources (real RAG), each tagged
    with its source's title/origin."""
    with session(path) as db:
        chunks = db.scalars(select(SourceChunk).where(SourceChunk.embedding.is_not(None))).all()
        # One lookup table rather than a join: the ranking happens in Python anyway,
        # and this keeps it to two queries regardless of how many chunks match.
        labels = {
            row_id: (title, origin)
            for row_id, title, origin in db.execute(select(Source.id, Source.title, Source.origin)).all()
        }
        scored = _rank(chunks, query_vec, lambda row: row.embedding, limit)
        out = []
        for score, row in scored:
            title, origin = labels.get(row.source_id, ("", ""))
            out.append(
                {
                    "source_id": row.source_id,
                    "title": title,
                    "origin": origin,
                    "snippet": row.text[:_CHUNK_SNIPPET],
                    "score": score,
                }
            )
        return out


def delete_source(path: Path, source_id: int) -> bool:
    with session(path) as db:
        # Chunks first: they reference the source, and leaving them behind would
        # keep the text searchable after the source was supposedly deleted.
        db.execute(delete(SourceChunk).where(SourceChunk.source_id == source_id))
        return changed(db.execute(delete(Source).where(Source.id == source_id))) > 0


def _rank[Row](
    rows: Iterable[Row],
    query_vec: list[float],
    vector_of: Callable[[Row], bytes | None],
    limit: int,
) -> list[tuple[float, Row]]:
    """Cosine-rank rows by their stored vector, best first, dropping non-matches.

    Generic in the row type so a caller gets back what it put in. Returning `object` meant every
    `row.title` / `row.source_id` that unpacks these results was unprovable.
    """
    scored: list[tuple[float, Row]] = []
    for row in rows:
        similarity = cosine(query_vec, unpack_vector(vector_of(row)))
        if similarity > 0:
            scored.append((round(similarity, 3), row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:limit]
