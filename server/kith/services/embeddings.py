"""Semantic memory — recall by meaning, not by shared words.

The pure data layer (``infra.db.repositories``) knows how to store a vector and rank by
cosine similarity, but not how to *make* a vector. This module bridges that gap:
it owns the embedding model and the Ollama host, turns text into vectors, and
routes memory writes/reads through them.

Everything degrades gracefully — if the embedding model is missing or Ollama is
unreachable, ``embed`` returns None and callers fall back to keyword search, so
recall keeps working (just less cleverly).
"""

from __future__ import annotations

import threading
from pathlib import Path

from kith import settings
from kith.config import OLLAMA_HOST
from kith.infra.db import repositories as repo
from kith.llm import ollama

EMBED_MODEL = settings.EMBED_MODEL


def embed(text: str) -> list[float] | None:
    return ollama.embed(OLLAMA_HOST, text, EMBED_MODEL)


def remember(
    path: Path,
    content: str,
    tags: list[str] | None = None,
    importance: int = 0,
    level: str = "recall",
) -> dict:
    """Store a memory together with its embedding (best effort)."""
    return repo.memories.add_memory(path, content, tags, importance, level, embedding=embed(content))


def recall(path: Path, query: str, limit: int = 20) -> list[dict]:
    """Find memories by meaning, falling back to keyword search when needed."""
    vector = embed(query)
    if vector:
        results = repo.memories.semantic_search(path, vector, limit)
        if results:
            return results
    return repo.memories.search_memories(path, query, limit)


def reembed(path: Path, memory_id: int, content: str) -> None:
    """Refresh a memory's vector after its content changed (best effort)."""
    vector = embed(content)
    if vector:
        repo.memories.set_memory_embedding(path, memory_id, vector)


def backfill(path: Path) -> int:
    """Embed every memory that doesn't have a vector yet. Returns how many."""
    done = 0
    for memory_id, content in repo.memories.memories_missing_embedding(path):
        vector = embed(content)
        if vector:
            repo.memories.set_memory_embedding(path, memory_id, vector)
            done += 1
    return done


def backfill_async(path: Path) -> None:
    """Backfill in the background so startup never blocks on Ollama."""

    def _run() -> None:
        try:
            count = backfill(path)
            if count:
                print(f"[kith] embeddings: backfilled {count} memory vector(s)")
        except Exception as exc:
            print(f"[kith] embeddings: backfill skipped ({exc})")

    threading.Thread(target=_run, name="kith-embed-backfill", daemon=True).start()
