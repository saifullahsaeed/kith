"""Once a conversation's replayed prose grows large, old turns fold into a running brief.

History is replayed in full every turn (only the prose, but all of it), so a long-lived
conversation's prompt grows without bound. Folding the old turns into a ~1k-token brief and
keeping only the most recent verbatim bounds it — the field's compaction pattern. The brief
is rolling: it is only re-generated when enough new turns have accumulated, never every turn,
because a model call per turn to summarise a growing history would cost more than it saves.

The fold must never break a turn: if the summariser fails, the full history is replayed as
before.
"""

from __future__ import annotations

from kith.config import Config
from kith.services import conversations, history
from kith.services.history import compact


def _cfg() -> Config:
    return Config(model="m", num_ctx=0, num_predict=0, system="", think=True)


def _turns(n: int, size: int = 1000) -> list[dict]:
    # Alternating user/assistant, each `size` chars, so total prose is predictable.
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": chr(65 + i % 26) * size} for i in range(n)
    ]


class TestLeavingShortHistoryAlone:
    def test_under_the_budget_it_is_returned_untouched(self):
        history = _turns(4, 500)  # 2,000 chars
        called = []

        messages, summary = compact(
            history, lambda t: called.append(t) or "brief", max_chars=5000, keep_recent=4
        )

        assert messages == history
        assert summary is None
        assert called == []  # no model call for a short conversation


class TestFoldingWhenItGrows:
    def test_old_turns_become_a_brief_and_recent_ones_survive(self):
        history = _turns(12, 1000)  # 12,000 chars, over budget

        messages, summary = compact(history, lambda t: "THE BRIEF", max_chars=5000, keep_recent=4)

        # A leading summary system message, then exactly the last 4 turns, verbatim and in order.
        assert messages[0]["role"] == "system"
        assert "THE BRIEF" in messages[0]["content"]
        assert messages[1:] == history[-4:]
        assert summary == {"through": 8, "text": "THE BRIEF"}

    def test_the_brief_summarises_the_old_turns_not_the_recent_ones(self):
        history = _turns(12, 1000)
        seen = {}

        compact(history, lambda t: seen.setdefault("text", t) or "b", max_chars=5000, keep_recent=4)

        # The 5th turn's content (index 4, letter 'E') is old; the 9th (index 8) is recent.
        assert "E" * 1000 in seen["text"]
        assert "I" * 1000 not in seen["text"]  # index 8 is kept verbatim, not summarised


class TestReusingTheBriefWithoutAnotherCall:
    def test_a_recent_enough_brief_is_reused(self):
        history = _turns(10, 1000)
        prior = {"through": 8, "text": "OLD BRIEF"}
        called = []

        messages, summary = compact(
            history, lambda t: called.append(t) or "new", prior=prior, max_chars=5000, keep_recent=4
        )

        assert called == []  # only 2 new turns since the brief — no re-summary
        assert "OLD BRIEF" in messages[0]["content"]
        assert messages[1:] == history[8:]
        assert summary is None  # nothing new to persist

    def test_the_prior_brief_is_folded_in_when_it_regenerates(self):
        history = _turns(16, 1000)  # 8 turns past a through=8 brief, well over keep_recent
        prior = {"through": 8, "text": "OLD BRIEF"}
        seen = {}

        compact(
            history, lambda t: seen.setdefault("t", t) or "rolled", prior=prior, max_chars=5000, keep_recent=4
        )

        assert "OLD BRIEF" in seen["t"]  # the rolling summary carries the prior brief forward


class TestNeverBreakingATurn:
    def test_a_failed_summary_replays_the_full_history(self):
        history = _turns(12, 1000)

        messages, summary = compact(history, lambda t: "", max_chars=5000, keep_recent=4)

        assert messages == history  # unchanged — better a big prompt than a broken turn
        assert summary is None


class TestTheBriefSurvivesBetweenTurns:
    def test_a_summary_round_trips_through_the_transcript(self, db):
        conv = conversations.start(db, "hi")["id"]

        conversations.record_summary(conv, 8, "the brief")

        assert conversations.latest_summary(conv) == {"through": 8, "text": "the brief"}

    def test_the_most_recent_summary_wins(self, db):
        conv = conversations.start(db, "hi")["id"]
        conversations.record_summary(conv, 8, "old")
        conversations.record_summary(conv, 16, "new")

        assert conversations.latest_summary(conv) == {"through": 16, "text": "new"}

    def test_no_summary_yet_reads_as_empty(self, db):
        conv = conversations.start(db, "hi")["id"]

        assert conversations.latest_summary(conv) == {}


class TestFoldWiresSettingsPersistenceAndTheModel:
    def test_it_folds_using_the_configured_thresholds(self, db, monkeypatch):
        from kith.services import tuning

        tuning.apply({"history_max_chars": 5000, "history_keep_recent": 4})
        conv = conversations.start(db, "hi")["id"]
        monkeypatch.setattr(history, "_summarize", lambda text, config, host: "STUB BRIEF")

        messages, fresh = history.fold(_turns(12, 1000), _cfg(), conv)

        assert "STUB BRIEF" in messages[0]["content"]
        assert fresh == {"through": 8, "text": "STUB BRIEF"}

    def test_it_reuses_a_stored_brief_without_calling_the_model(self, db, monkeypatch):
        from kith.services import tuning

        tuning.apply({"history_max_chars": 5000, "history_keep_recent": 4})
        conv = conversations.start(db, "hi")["id"]
        conversations.record_summary(conv, 8, "STORED")
        calls: list = []
        monkeypatch.setattr(history, "_summarize", lambda text, config, host: calls.append(1) or "new")

        messages, fresh = history.fold(_turns(10, 1000), _cfg(), conv)

        assert calls == []
        assert "STORED" in messages[0]["content"]
        assert fresh is None

    def test_a_summariser_that_fails_leaves_the_turn_with_its_full_history(self, db, monkeypatch):
        from kith.services import tuning

        tuning.apply({"history_max_chars": 5000, "history_keep_recent": 4})
        conv = conversations.start(db, "hi")["id"]
        monkeypatch.setattr(history, "_summarize", lambda text, config, host: "")
        hist = _turns(12, 1000)

        messages, fresh = history.fold(hist, _cfg(), conv)

        assert messages == hist
        assert fresh is None
