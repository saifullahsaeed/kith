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

import os
import threading
from pathlib import Path

from kith.config import ollama_host
from kith.infra.db import repositories as repo
from kith.infra.db.vectors import cosine
from kith.llm import ollama


def embed(text: str) -> list[float] | None:
    from kith.services import tuning

    return ollama.embed(ollama_host(), text, str(tuning.value("embed_model")))


def embed_for_injection(text: str, timeout: float = 1.0) -> list[float] | None:
    """Embed ``text`` for the prompt path, or None if it does not answer within ``timeout``.

    Separate from :func:`embed` for one reason: this runs while the turn's prompt is being
    assembled, which is the one place in this codebase that must never wait on the network for
    long. :func:`embed` inherits Ollama's own timeouts — a 5-second connect and a 60-second read,
    right for a deliberate ``recall`` and catastrophic before every turn — so here the call is
    given a hard wall-clock cap and, if it has not answered by then (Ollama cold, swapping, gone),
    yields None and the caller falls back to lexical overlap. A healthy local nomic embed returns
    in tens to a few hundred milliseconds (measured ~50-300ms on this machine), so a one-second
    cap never fires in the good case and bounds the sick case at one second instead of sixty.

    Implemented with a joined daemon thread rather than a shorter request timeout because the
    timeout lives in ``llm.ollama.embed``, which this layer does not own; the thread bounds the
    wall clock without reaching into it, and a straggling request dies with the process.
    """
    text = (text or "").strip()
    if not text:
        return None
    result: list[list[float] | None] = [None]

    def _run() -> None:
        result[0] = embed(text)

    worker = threading.Thread(target=_run, name="kith-embed-injection", daemon=True)
    worker.start()
    worker.join(timeout)
    return result[0] if not worker.is_alive() else None


def _project_profile(project: dict) -> str:
    """The text a project is embedded as, for deciding what a memory is about: its name, its
    description, and its folder's basename. The basename is in because it is often the only
    token a memory and its project share — a note that says ``~/Kith/missile-sim`` names no
    project but points straight at one."""
    directory = str(project.get("directory") or "")
    folder = os.path.basename(directory.rstrip("/")) if directory else ""
    return f"{project.get('name') or ''}. {project.get('description') or ''} {folder}".strip()


def closest_project(
    content: str,
    content_vector: list[float] | None,
    projects: list[dict],
    bar: float,
    margin: float,
) -> int | None:
    """The project a memory's content is closest to by meaning, if one is clearly clearest.

    Returns that project's id, or None when no project clears ``bar`` cosine *and* leads the
    runner-up by ``margin``. Both requirements matter, and the margin is why: measured on this
    machine (nomic-embed-text), unrelated text pairs still score 0.3-0.5, so an absolute bar
    alone is nearly meaningless and the Sadeef One design-token memory embeds *closer* to the
    "Sadeef AI" project (0.629) than to "Sadeef One" (0.590). This signal is therefore the last
    resort behind an exact name or folder match, and it only speaks when the winner is
    unmistakable — which is the shape that misfiles nothing rather than the shape that files
    the most.

    Best effort: content or a profile that will not embed is skipped, and if nothing embeds the
    answer is None and the caller keeps the chat's binding.
    """
    vector = content_vector or embed(content)
    if not vector or not projects:
        return None
    scored: list[tuple[float, int]] = []
    for project in projects:
        profile_vec = embed(_project_profile(project))
        if profile_vec:
            scored.append((cosine(vector, profile_vec), int(project["id"])))
    if not scored:
        return None
    scored.sort(reverse=True)
    best_score, best_id = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= bar and (best_score - runner_up) >= margin:
        return best_id
    return None


def remember(
    path: Path,
    content: str,
    tags: list[str] | None = None,
    importance: int = 0,
    level: str = "recall",
    project_id: int | None = None,
) -> dict:
    """Store a memory together with its embedding (best effort)."""
    return repo.memories.add_memory(
        path, content, tags, importance, level, embedding=embed(content), project_id=project_id
    )


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
