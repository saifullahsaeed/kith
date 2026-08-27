"""Changing what a memory says has to change what finds it.

Two halves of one operation, and only the first was wired up. `update_memory` rewrites
`content` and leaves `embedding` alone. `embeddings.reembed` exists precisely to close
that — its docstring reads "refresh a memory's vector after its content changed" — and an
audit for functions nothing calls found it had never been called from anywhere.

Neither half looks broken on its own, which is why it survived: the edit saves, the search
returns rows. It is the pair that is wrong, and silently. Recall keeps matching on the words
you deleted and then hands back the words you wrote, so the answer on screen looks right
while being found for the wrong reason.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo
from kith.services import brain


@pytest.fixture
def fake_embedder(monkeypatch):
    """A stand-in embedder: the vector is just the text, so a stale one is obvious.

    Real embeddings would make the assertion "the vector changed" true for the wrong
    reasons — float noise moves. This makes the question exact.
    """
    from kith.services import embeddings

    calls: list[str] = []

    def embed(text: str):
        calls.append(text)
        return [float(len(text)), float(sum(map(ord, text[:8])))]

    monkeypatch.setattr(embeddings, "embed", embed)
    return calls


def vector_of(db, memory_id: int):
    from sqlalchemy import select

    from kith.infra.db.engine import session
    from kith.infra.db.models import Memory

    with session(db) as s:
        return s.scalar(select(Memory.embedding).where(Memory.id == memory_id))


class TestEditingAMemory:
    def test_the_vector_follows_the_text(self, db, fake_embedder):
        made = brain.create(db, "memory", {"content": "he takes his coffee black"})
        before = vector_of(db, made["id"])

        brain.update(db, "memory", made["id"], {"content": "he drinks only tea, never coffee"})

        assert vector_of(db, made["id"]) != before, "the vector still points at the old words"

    def test_the_text_is_actually_saved(self, db, fake_embedder):
        made = brain.create(db, "memory", {"content": "first"})
        brain.update(db, "memory", made["id"], {"content": "second"})
        rows = repo.memories.list_memories(db)
        assert [m["content"] for m in rows if m["id"] == made["id"]] == ["second"]

    def test_it_re_embeds_the_new_text_and_not_the_old(self, db, fake_embedder):
        made = brain.create(db, "memory", {"content": "old words"})
        fake_embedder.clear()
        brain.update(db, "memory", made["id"], {"content": "new words"})
        assert fake_embedder == ["new words"]

    def test_changing_only_the_importance_does_not_re_embed(self, db, fake_embedder):
        """The text is what the vector is of. Re-embedding on every edit would spend a model
        call to arrive at the identical vector."""
        made = brain.create(db, "memory", {"content": "unchanged"})
        fake_embedder.clear()
        brain.update(db, "memory", made["id"], {"importance": 3})
        assert fake_embedder == []

    def test_an_embedder_that_is_down_does_not_lose_the_edit(self, db, monkeypatch):
        """A memory that saves but does not re-embed is stale in search. One that refuses to
        save because the embedder is unreachable is gone. The first is recoverable."""
        from kith.services import embeddings

        made = brain.create(db, "memory", {"content": "before"})
        monkeypatch.setattr(embeddings, "embed", lambda _t: None)

        brain.update(db, "memory", made["id"], {"content": "after"})

        rows = repo.memories.list_memories(db)
        assert [m["content"] for m in rows if m["id"] == made["id"]] == ["after"]

    def test_editing_a_memory_that_is_gone_says_so_rather_than_embedding_nothing(self, db, fake_embedder):
        assert brain.update(db, "memory", "9999", {"content": "ghost"}) is None
        assert fake_embedder == []
