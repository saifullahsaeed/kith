"""An outside scorer decides whether an all-met close needs a second look.

`_verify_done` is good at catching a close Kith knows is incomplete and blind to one he believes
is done and is not — he writes the brief, picks the requirements, supplies the evidence, grades
it. `cdac645` put the person's words back in front of him once; this adds an independent score so
that extra round is spent only when a scorer actually doubts the close, and skipped when it agrees.

The scorer is a network call, so it is mocked here. Its real contract is measured elsewhere; what
these tests hold is the wiring: a doubt hands back, a clear closes, no opinion falls back to the
self-check exactly as before, and it never costs more than one round.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.services import scoring
from kith.tools import run_tool

BRIEF = (
    "Done when a person can change the leave entitlement from the settings screen and see the table update."
)
MET = [{"requirement": "entitlement is configurable", "met": True, "evidence": "POST /rules/ returns 201"}]


def _task(db: Path) -> int:
    return repo.tasks.add_task(db, "Make the entitlement configurable", "high", BRIEF, "working")["id"]


def _close(db: Path, tid: int) -> dict:
    return run_tool("update_task", {"id": tid, "status": "done", "verification": MET}, db)


def _status(db: Path, tid: int) -> str:
    return str((repo.tasks.task_detail(db, tid) or {}).get("status") or "")


class TestWhenTheScorerDoubts:
    def test_the_close_is_handed_back_with_the_score(self, db: Path, monkeypatch):
        monkeypatch.setattr(scoring, "doubts_the_close", lambda *a, **k: (True, 0.18))
        tid = _task(db)
        with session_context.a_turn():
            out = _close(db, tid)
        result = out.get("result") or {}
        assert "blocked" in result and "18%" in result["blocked"]
        assert _status(db, tid) != "done"

    def test_the_same_call_again_closes_it(self, db: Path, monkeypatch):
        """One round, not a loop: having been shown, he decides."""
        monkeypatch.setattr(scoring, "doubts_the_close", lambda *a, **k: (True, 0.18))
        tid = _task(db)
        with session_context.a_turn():
            _close(db, tid)
            _close(db, tid)
        assert _status(db, tid) == "done"


class TestWhenTheScorerClears:
    def test_it_closes_with_no_extra_round(self, db: Path, monkeypatch):
        """The whole point over the always-once self-check: a confident pass saves the round."""
        monkeypatch.setattr(scoring, "doubts_the_close", lambda *a, **k: (False, 0.82))
        tid = _task(db)
        with session_context.a_turn():
            out = _close(db, tid)
        assert "blocked" not in (out.get("result") or {})
        assert _status(db, tid) == "done"


class TestWhenThereIsNoOpinion:
    def test_it_falls_back_to_the_self_check(self, db: Path, monkeypatch):
        """No scorer (local model, network down, not OpenRouter) is exactly the pre-scorer world:
        the person's own words are shown once, then it closes."""
        monkeypatch.setattr(scoring, "doubts_the_close", lambda *a, **k: None)
        from kith.services import conversations

        conversation = conversations.start(db, "t")["id"]
        conversations.record(db, conversation, "user", "let me change it from the settings screen")
        tid = _task(db)
        with session_context.working_in(conversation), session_context.a_turn():
            out = _close(db, tid)
            assert "blocked" in (out.get("result") or {})  # the self-check fired
            _close(db, tid)
        assert _status(db, tid) == "done"


class TestTheClientGate:
    def _cfg(self, **kw):
        from dataclasses import replace

        from kith.config import default_config

        return replace(default_config(), **kw)

    def test_no_opinion_without_an_openrouter_key(self):
        assert scoring.available(self._cfg(base_url="", api_key="")) is False
        assert scoring.available(self._cfg(base_url="https://openrouter.ai/api/v1", api_key="")) is False
        assert scoring.available(self._cfg(base_url="http://localhost:11434", api_key="k")) is False
        assert scoring.available(self._cfg(base_url="https://openrouter.ai/api/v1", api_key="k")) is True

    def test_noul_returns_none_when_unavailable_without_calling_out(self, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("must not touch the network when no key is set")

        monkeypatch.setattr(scoring.requests, "post", explode)
        assert scoring.noul("x", "y?", config=self._cfg(base_url="", api_key="")) is None
