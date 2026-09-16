"""A report that is eighty per cent right should cost a round to fix, not a whole second search.

An errand used to be strictly one-shot. Its private message list was built, used and dropped
inside one call, so "your patch misses the test file" meant sending a fresh worker at the same
ground — ten more rounds and a second model's worth of tokens to add one file to a diff.

So a worker is stored. The thing to understand about that is what it *is*: there is no thread
kept alive and nothing to revive. A worker is a row holding a message list, plus the function
that runs more rounds on it — which is why resuming works across a turn, across a restart, and
through exactly the same code path as sending one in the first place.

What is deliberately **not** stored is the middle: everything the worker read on the way. That
is the expensive thing, and keeping it out of anybody's context is the entire reason delegation
exists — so a resumed worker knows its brief and its own conclusion, and not how it got there.
Weak for a scout, and strong for a builder, whose real state was never in its transcript: it is
the edits sitting in its copy of the repository, which are still there.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.services import agent_loop
from kith.tools import delegation


def _rounds(*script):
    calls: list[list[dict]] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        calls.append(list(convo))
        text, tool_calls = script[min(len(calls) - 1, len(script) - 1)]
        if text:
            yield {"type": "delta", "role": "text", "text": text}
        yield {"type": "turn", "content": text, "tool_calls": list(tool_calls), "stats": {}}

    return fake, calls


class TestAWorkerSurvivesItsTurn:
    def test_a_report_carries_the_id_needed_to_come_back_to_it(self, db: Path, monkeypatch):
        """Without this the feature is unusable: there is nothing to name."""
        fake, _ = _rounds(("The ledger lives in core/ledger.py.", []))
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = delegation.delegate_subtask(db, {"objective": "where does the ledger live"})
        assert answer["worker_id"]

    def test_what_it_said_is_stored(self, db: Path, monkeypatch):
        fake, _ = _rounds(("The ledger lives in core/ledger.py.", []))
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = delegation.delegate_subtask(db, {"objective": "where does the ledger live"})
        stored = repo.workers.get(db, answer["worker_id"])

        assert stored is not None
        assert stored["state"] == "reported"
        assert stored["role"] == "scout"
        assert "core/ledger.py" in stored["report"]

    def test_the_brief_and_the_objective_are_kept(self, db: Path, monkeypatch):
        """A worker that had forgotten what it was sent to do would be worse than a fresh one."""
        fake, _ = _rounds(("Found it.", []))
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = delegation.delegate_subtask(db, {"objective": "where does the ledger live"})
        kept = repo.workers.scratchpad(db, answer["worker_id"])

        assert kept[0]["role"] == "system"
        assert delegation.SCOUT_BRIEF in kept[0]["content"]
        assert kept[1] == {"role": "user", "content": "where does the ledger live"}
        assert kept[-1]["role"] == "assistant"

    def test_the_searching_is_not_kept(self, db: Path, monkeypatch):
        """The whole economics, asserted. A stored transcript of forty file reads would put the
        expensive thing back — somewhere nobody is looking at it."""
        fake, _ = _rounds(
            ("Reading the ledger now.", [{"function": {"name": "list_projects", "arguments": "{}"}}]),
            ("It is in core/ledger.py.", []),
        )
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = delegation.delegate_subtask(db, {"objective": "where does the ledger live"})
        kept = repo.workers.scratchpad(db, answer["worker_id"])

        assert len(kept) == 3
        assert not any("Reading the ledger now" in str(message.get("content")) for message in kept)


class TestComingBackToOne:
    def test_it_starts_from_what_it_already_said(self, db: Path, monkeypatch):
        """The point of the feature: the follow-up is answered in context, not re-derived."""
        fake, seen = _rounds(("It is in core/ledger.py.", []), ("Instantiated in app/boot.py:41.", []))
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        first = delegation.delegate_subtask(db, {"objective": "where does the ledger live"})
        second = delegation.follow_up(
            db, {"worker_id": first["worker_id"], "ask": "where is it instantiated"}
        )

        assert second["findings"] == "Instantiated in app/boot.py:41."
        resumed = seen[-1]
        assert any("core/ledger.py" in str(message.get("content")) for message in resumed)
        assert resumed[-1] == {"role": "user", "content": "where is it instantiated"}

    def test_it_keeps_its_id_rather_than_becoming_a_new_worker(self, db: Path, monkeypatch):
        """Otherwise every follow-up would fork a worker, and the third would have to guess
        which of two ids held the context it wanted."""
        fake, _ = _rounds(("First.", []), ("Second.", []))
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        first = delegation.delegate_subtask(db, {"objective": "find it"})
        second = delegation.follow_up(db, {"worker_id": first["worker_id"], "ask": "and the other thing"})

        assert second["worker_id"] == first["worker_id"]

    def test_the_new_answer_replaces_the_old_one(self, db: Path, monkeypatch):
        fake, _ = _rounds(("First.", []), ("Second.", []))
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        first = delegation.delegate_subtask(db, {"objective": "find it"})
        delegation.follow_up(db, {"worker_id": first["worker_id"], "ask": "again"})

        stored = repo.workers.get(db, first["worker_id"])
        assert stored is not None
        assert stored["report"] == "Second."
        assert stored["rounds"] == 2


class TestWhatCannotBeFollowedUp:
    def test_an_unknown_id_says_so_rather_than_starting_a_worker(self, db: Path):
        answer = delegation.follow_up(db, {"worker_id": "nope", "ask": "anything"})
        assert "No worker nope" in answer["error"]

    def test_a_worker_that_was_cleared_away_says_so(self, db: Path, monkeypatch):
        """A spent builder's copy is gone, so following it up would be asking about a tree
        that no longer exists. Its report is still worth having; a follow-up is not."""
        fake, _ = _rounds(("Done.", []))
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        first = delegation.delegate_subtask(db, {"objective": "find it"})
        repo.workers.spend(db, first["worker_id"])

        answer = delegation.follow_up(db, {"worker_id": first["worker_id"], "ask": "more"})
        assert "cleared away" in answer["error"]

    def test_both_arguments_are_required(self, db: Path):
        assert "error" in delegation.follow_up(db, {"worker_id": "", "ask": "x"})
        assert "error" in delegation.follow_up(db, {"worker_id": "w1", "ask": "  "})

    def test_a_worker_cannot_follow_anything_up_itself(self):
        """One worker must not become a tree of them, in any of the three spellings."""
        for role in delegation.ROLES:
            given = delegation.ROLES[role]["given"]()
            assert "follow_up" not in given
            assert "send_builder" not in given
            assert "delegate_subtask" not in given


class TestTheScratchpadDoesNotGrowForever:
    def test_a_long_transcript_is_capped_but_keeps_its_brief(self):
        """Trimmed from the front, except the first message. A worker that has forgotten what
        it was sent to do is worse than one that has forgotten how it got here."""
        long = [{"role": "system", "content": "THE BRIEF"}]
        long += [{"role": "user", "content": f"turn {n}"} for n in range(400)]

        trimmed = repo.workers._trim(long)

        assert len(trimmed) == repo.workers._SCRATCHPAD_LIMIT
        assert trimmed[0]["content"] == "THE BRIEF"
        assert trimmed[-1]["content"] == "turn 399"
