"""Which recall memories ride along a turn is decided by meaning, not shared words.

The old test was lexical overlap (`domain.present.relevance`), so "authentication" did not surface
for a chat about "login" and memories flickered with phrasing. Every memory is already embedded and
nomic-embed-text runs locally, so `context_block` now scores `about` against each candidate's stored
vector by cosine, above a calibrated bar. The lexical overlap is kept as the fallback for when the
embed times out, Ollama is gone, or a memory was never embedded — recall must never silently stop
working on a machine without embeddings.

These tests drive the vector math with fixed vectors so the threshold and the fallback are checked
without a network. The calibration of the bar itself (greetings peak at 0.501, real questions
0.55-0.86) is recorded on `memory_context.EMBED_RELEVANT`.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.services import memory_context as mc


def _recall(mid: int, content: str, vector: list[float] | None):
    return ({"id": mid, "level": "recall", "content": content}, vector or [])


class TestScoringByMeaning:
    def test_a_close_memory_rides_along_and_a_far_one_does_not(self, monkeypatch):
        monkeypatch.setattr(mc.embeddings, "embed_for_injection", lambda _t, **k: [1.0, 0.0, 0.0])
        candidates = [
            _recall(1, "about the same thing", [1.0, 0.0, 0.0]),  # cosine 1.0
            _recall(2, "about something else", [0.0, 1.0, 0.0]),  # cosine 0.0
        ]
        kept = mc._relevant_recall(candidates, about="anything, the vector is stubbed")
        assert [m["id"] for m in kept] == [1]

    def test_the_closest_comes_first(self, monkeypatch):
        monkeypatch.setattr(mc.embeddings, "embed_for_injection", lambda _t, **k: [1.0, 0.0, 0.0])
        candidates = [
            _recall(1, "half on topic", [0.5, 0.5, 0.0]),  # cosine ~0.707
            _recall(2, "dead on topic", [1.0, 0.0, 0.0]),  # cosine 1.0
        ]
        kept = mc._relevant_recall(candidates, about="stubbed")
        assert [m["id"] for m in kept] == [2, 1]

    def test_a_greeting_that_embeds_to_no_one_pulls_nothing(self, monkeypatch):
        # A greeting sits below the bar against every memory — modelled here as a vector orthogonal
        # to all of them. This is the "hey injects nothing" guarantee, in vector form.
        monkeypatch.setattr(mc.embeddings, "embed_for_injection", lambda _t, **k: [0.0, 0.0, 1.0])
        candidates = [_recall(1, "a", [1.0, 0.0, 0.0]), _recall(2, "b", [0.0, 1.0, 0.0])]
        assert mc._relevant_recall(candidates, about="hey") == []


class TestTheLexicalFallback:
    def test_when_the_embed_is_unavailable_word_overlap_decides(self, monkeypatch):
        monkeypatch.setattr(mc.embeddings, "embed_for_injection", lambda _t, **k: None)
        candidates = [
            _recall(1, "The odoo connector syncs invoices sequentially and is slow.", None),
            _recall(2, "The missile lands 38 km east of target.", None),
        ]
        kept = mc._relevant_recall(candidates, about="why is the odoo invoice sync connector slow")
        assert [m["id"] for m in kept] == [1]

    def test_a_never_embedded_memory_still_gets_a_chance(self, monkeypatch):
        # One candidate has a vector, one does not. The embed succeeds, so the vectored one is
        # scored by cosine and the bare one falls back to overlap — neither is silently invisible.
        monkeypatch.setattr(mc.embeddings, "embed_for_injection", lambda _t, **k: [1.0, 0.0, 0.0])
        candidates = [
            _recall(1, "vectored and on topic", [1.0, 0.0, 0.0]),
            _recall(2, "the odoo invoice connector sync", None),  # no vector, lexical only
        ]
        kept = mc._relevant_recall(candidates, about="odoo invoice connector sync")
        assert 1 in [m["id"] for m in kept]
        assert 2 in [m["id"] for m in kept]


class TestEmptyConversation:
    def test_nothing_to_talk_about_pulls_nothing(self, monkeypatch):
        # No network call should even be attempted for an empty subject.
        called = []
        monkeypatch.setattr(
            mc.embeddings, "embed_for_injection", lambda _t, **k: called.append(1) or [1, 0, 0]
        )
        candidates = [_recall(1, "anything", [1.0, 0.0, 0.0])]
        assert mc._relevant_recall(candidates, about="") == []
        assert called == []


class TestThroughContextBlock:
    """The whole path: fetch scoped recall with vectors, score, and assemble the block. Core is
    unconditional; recall has to earn its place."""

    def test_core_is_unconditional_and_recall_is_earned(self, db: Path, monkeypatch):
        repo.memories.add_memory(db, "A core fact, always present.", level="core", project_id=None)
        repo.memories.add_memory(
            db, "On the subject.", level="recall", embedding=[1.0, 0.0, 0.0], project_id=None
        )
        repo.memories.add_memory(
            db, "Off the subject.", level="recall", embedding=[0.0, 1.0, 0.0], project_id=None
        )
        monkeypatch.setattr(mc.embeddings, "embed_for_injection", lambda _t, **k: [1.0, 0.0, 0.0])

        block = mc.context_block(db, about="a question the vector is stubbed for", project_id=None)

        assert "A core fact, always present." in block  # core rides regardless
        assert "On the subject." in block  # earned its slot
        assert "Off the subject." not in block  # below the bar

    def test_a_greeting_carries_only_core(self, db: Path, monkeypatch):
        repo.memories.add_memory(db, "A core fact.", level="core", project_id=None)
        repo.memories.add_memory(
            db, "A recall fact.", level="recall", embedding=[1.0, 0.0, 0.0], project_id=None
        )
        # Greeting → a vector far from everything.
        monkeypatch.setattr(mc.embeddings, "embed_for_injection", lambda _t, **k: [0.0, 0.0, 1.0])

        block = mc.context_block(db, about="hey", project_id=None)

        assert "A core fact." in block
        assert "A recall fact." not in block
