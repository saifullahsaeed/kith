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

from dataclasses import replace

import pytest

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

        messages, summary = compact(history, lambda t: "THE BRIEF", max_chars=5000, keep_recent=2)

        # A leading summary system message, then exactly the last 4 turns, verbatim and in order.
        assert messages[0]["role"] == "system"
        assert "THE BRIEF" in messages[0]["content"]
        assert messages[1:] == history[-4:]
        assert summary == {"through": 8, "text": "THE BRIEF"}

    def test_the_brief_summarises_the_old_turns_not_the_recent_ones(self):
        history = _turns(12, 1000)
        seen = {}

        compact(history, lambda t: seen.setdefault("text", t) or "b", max_chars=5000, keep_recent=2)

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
        history = _turns(24, 1000)  # 16 turns past a through=8 brief, well past the roll window
        prior = {"through": 8, "text": "OLD BRIEF"}
        seen = {}

        compact(
            history, lambda t: seen.setdefault("t", t) or "rolled", prior=prior, max_chars=5000, keep_recent=4
        )

        assert "OLD BRIEF" in seen["t"]  # the rolling summary carries the prior brief forward

    def test_a_fold_does_not_immediately_retrigger_when_the_new_tail_still_fits(self):
        """The bug this whole class guards against: once a brief exists, a real 2-day
        conversation kept refolding every ~9 messages forever, no matter how large the real
        budget actually was — because reuse was judged on a message count, not on whether the
        new tail since the last fold had actually grown past the budget. A turn that fits
        comfortably inside `max_chars` must be able to add several more turns before another
        model call is worth paying for."""
        history = _turns(12, 1000)  # 12,000 chars
        calls = []

        _, fresh = compact(history, lambda t: calls.append(t) or "BRIEF-1", max_chars=5000, keep_recent=4)
        assert fresh is not None  # the first fold, as expected: 12,000 > 5,000

        history = history + _turns(2, 1000)  # one more small turn: 2,000 more chars

        # The real budget can (and normally does) come out the same on the next call — it is
        # recomputed from the model's window, not from this history — but a bigger one here
        # makes the point cleanly: the tail since the cut (10 items, 10,000 chars) fits inside
        # it, so it must reuse regardless of how many turns that took.
        _, fresh2 = compact(
            history, lambda t: calls.append(t) or "BRIEF-2", prior=fresh, max_chars=20_000, keep_recent=4
        )

        assert fresh2 is None, f"refolded again while the new tail still fit the budget: {fresh2}"
        assert len(calls) == 1

    def test_a_fold_does_retrigger_once_the_new_tail_outgrows_the_budget(self):
        """The other half of the same fix: reuse is not unconditional — once real content
        piles up past the budget, it still has to refold. Otherwise this would just be the old
        bug's mirror image: never folding again regardless of size."""
        history = _turns(12, 1000)
        calls = []

        _, fresh = compact(history, lambda t: calls.append(t) or "BRIEF-1", max_chars=5000, keep_recent=4)

        history = history + _turns(10, 1000)  # 10,000 more chars since the cut — past the budget
        _, fresh2 = compact(
            history, lambda t: calls.append(t) or "BRIEF-2", prior=fresh, max_chars=5000, keep_recent=4
        )

        assert fresh2 is not None
        assert len(calls) == 2


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

        tuning.apply({"history_max_chars": 5000, "history_keep_recent": 2})
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


class TestTheBudgetScalesToTheRealWindow:
    """`history_max_chars` is a fixed guess. A model's real context window is not — a 1M-token
    cloud model and a 40K local one need very different budgets, and no single number is right
    for both. Once the window is known, the budget is a share of *that*, the same way
    `agent_loop`'s in-turn fold already works, instead of the flat setting.
    """

    def _cfg(self, window: int) -> Config:
        return Config(model="m", num_ctx=0, num_predict=0, system="", think=True, context_window=window)

    def test_an_unknown_window_falls_back_to_the_flat_setting(self, db):
        from kith.services import tuning

        tuning.apply({"history_max_chars": 9000})

        assert history._budget_chars(self._cfg(0), None) == 9000

    def test_a_known_window_ignores_the_flat_setting(self, db):
        from kith.services import tuning

        tuning.apply({"history_max_chars": 5_000_000})  # would swamp a small window if it won

        # ~1,000 tokens * 3.7 chars/token * 80% share.
        assert history._budget_chars(self._cfg(1_000), None) == pytest.approx(2_960, abs=1)

    def test_a_bigger_window_buys_a_bigger_budget(self, db):
        small = history._budget_chars(self._cfg(1_000), None)
        big = history._budget_chars(self._cfg(1_000_000), None)

        assert big > small * 100

    def test_the_persona_is_netted_out_of_the_budget(self, db):
        bare = self._cfg(10_000)
        with_persona = replace(bare, system="x" * 2_000)

        assert history._budget_chars(with_persona, None) == history._budget_chars(bare, None) - 2_000

    def test_a_tiny_window_folds_a_conversation_the_flat_default_would_have_left_alone(self, db, monkeypatch):
        # A conversation well under the old 120,000-char default, on a model whose real window
        # is small enough that this is most of it.
        hist = _turns(12, 1000)  # 12,000 chars
        cfg = self._cfg(2_000)  # ~5,920-char budget at 80% share
        monkeypatch.setattr(history, "_summarize", lambda text, config, host: "BRIEF")

        messages, fresh = history.fold(hist, cfg, "")

        assert fresh is not None
        assert messages != hist


def _tool_heavy_turn(n_calls: int, size: int = 500) -> list[dict]:
    """One turn shaped like a real one that did work: one user message, then N call/result
    pairs — the shape `conversations.full_messages` actually produces, not `_turns`'s
    uniform user/assistant alternation."""
    out = [{"role": "user", "content": "go"}]
    for i in range(n_calls):
        out.append(
            {"role": "assistant", "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": f"f{i}.py"}}}]}
        )
        out.append({"role": "tool", "tool_name": "read_file", "content": "X" * size})
    return out


class TestTheCutCountsTurnsNotListItems:
    """`_turns()` can't exercise this on its own — it's uniformly 2 items per turn, so a
    flat-index cut and a turn-aware one land on the same place by coincidence. A real
    tool-heavy turn is where they'd disagree."""

    def test_a_single_tool_heavy_turn_is_never_split_across_the_cut(self):
        """10 tool calls in ONE turn is 21 flat items. A flat-index cut of `count -
        keep_recent` would happily slice into the middle of it; a turn-aware one can't —
        it only ever lands on a user-message boundary."""
        tail = [{"role": "user", "content": "next"}, {"role": "assistant", "content": "ok"}]
        hist = _tool_heavy_turn(10) + tail

        messages, summary = compact(hist, lambda t: "BRIEF", max_chars=100, keep_recent=1)

        assert summary is not None
        assert messages[1:] == tail  # only the trailing exchange survives untouched

    def test_a_tool_calls_message_is_actually_counted_not_treated_as_zero(self):
        """A `{"role": "assistant", "tool_calls": [...]}` message has no string `content` —
        the old `isinstance(content, str)` size check silently counted it as zero, which
        would leave this conversation looking empty and never fold at all."""
        hist = _tool_heavy_turn(5, size=2000) + [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "ok"},
        ]

        messages, summary = compact(hist, lambda t: "BRIEF", max_chars=500, keep_recent=1)

        assert summary is not None  # would stay None forever if tool_calls sized as 0

    def test_a_conversation_shorter_than_keep_recent_still_folds_something(self):
        """Three turns, `keep_recent=8` — fewer turns than the ceiling protects, and
        already well over budget. This must not disable folding outright, and must not
        waste a model call summarising an empty slice — both would leave the conversation
        permanently unfolded."""
        hist = _tool_heavy_turn(5, size=1000) * 3  # three tool-heavy turns, well over budget
        calls = []

        messages, summary = compact(hist, lambda t: calls.append(t) or "BRIEF", max_chars=2000, keep_recent=8)

        assert summary is not None
        assert messages != hist
        assert calls and calls[0].strip()  # not a wasted call summarising an empty slice

    def test_a_single_turn_alone_has_nothing_before_it_to_fold(self):
        """The one case that's still correctly a no-op: there is no earlier turn to fold
        into a brief, whatever `keep_recent` says."""
        hist = _tool_heavy_turn(20, size=1000)
        calls = []

        messages, summary = compact(hist, lambda t: calls.append(t) or "BRIEF", max_chars=2000, keep_recent=999)

        assert summary is None
        assert messages == hist
        assert calls == []


class TestAStaleCursorIsDistrustedRatherThanReplayedBlind:
    """Found live: a brief made before `conversations.full_messages` started replaying tool
    calls had `through=246`, an index into that older, shorter shape. Replayed against
    today's list the same number landed on a `tool` message, not a turn boundary — between a
    call and its own result — and reusing it verbatim would hand the provider a message list
    it must refuse outright. A cursor is only trustworthy if it still points at an actual
    boundary in *this* history."""

    def test_a_cursor_that_lands_inside_a_call_result_pair_is_discarded(self):
        hist = _tool_heavy_turn(3) + [{"role": "user", "content": "next"}, {"role": "assistant", "content": "ok"}]
        prior = {"through": 2, "text": "STALE"}  # index 2 is a `tool` message, not a boundary
        calls = []

        messages, fresh = compact(
            hist, lambda t: calls.append(t) or "FRESH", prior=prior, max_chars=100, keep_recent=1
        )

        assert fresh == {"through": 7, "text": "FRESH"}  # refolded from scratch, not from index 2
        assert "STALE" not in calls[0]  # the stale brief's text is dropped, not carried forward blind
        assert messages[0] == {
            "role": "system",
            "content": "[Summary of the earlier part of this conversation]\nFRESH",
        }

    def test_a_cursor_that_still_lands_on_a_real_boundary_is_kept(self):
        """The contrast case: nothing about a stored cursor is distrusted just because it is
        old — only because it stopped matching the shape it is being replayed against."""
        hist = _tool_heavy_turn(3) + [{"role": "user", "content": "next"}, {"role": "assistant", "content": "ok"}]
        prior = {"through": 7, "text": "KEPT"}  # index 7 is the tail's own user message — real

        messages, fresh = compact(hist, lambda t: "unused", prior=prior, max_chars=100_000, keep_recent=1)

        assert fresh is None  # reused outright, no model call needed
        assert "KEPT" in messages[0]["content"]


class TestOneFoldCallHasABoundedInputEvenWhenMuchMoreIsOwed:
    """Found on the same real conversation: a brief that has never successfully advanced (or
    never run at all) can owe a summarisation call millions of characters of new territory —
    replayed tool calls make old turns far bigger than a flat message count ever anticipated.
    Asking one call to read all of it either exceeds the model's own input limit outright or
    is simply too slow to be worth attempting, and fails the same way on every turn after,
    forever. Capping one call's input means catching up costs a few always-successful turns
    instead of one guaranteed-to-fail one, retried without end."""

    def test_a_huge_backlog_folds_partially_rather_than_all_at_once(self):
        hist = []
        for _ in range(20):
            hist += _tool_heavy_turn(5, size=2000)  # 220 items, 20 turns, 216,960 chars total
        seen = {}

        messages, fresh = compact(
            hist,
            lambda t: seen.setdefault("text", t) or "PARTIAL",
            max_chars=1000,
            keep_recent=1,
            max_fold_chars=20_000,
        )

        assert fresh is not None
        # One turn's worth (11 items) advanced, not the ~19 turns actually owed toward the
        # keep_recent=1 target — bounded progress, not the full, doomed-to-fail backlog.
        assert fresh["through"] == 11
        assert len(seen["text"]) <= 20_000  # bounded by the ceiling, not the 216,960-char total

    def test_a_single_oversized_turn_still_advances_rather_than_stalling_forever(self):
        """The ceiling bounds a call's input; it cannot make a real turn smaller. One turn
        whose own content alone exceeds it must still move forward once, or a conversation
        with a single huge exchange would stall here permanently."""
        huge_turn = _tool_heavy_turn(50, size=2000)  # 101 items, 108,223 chars — one turn
        tail = [{"role": "user", "content": "next"}, {"role": "assistant", "content": "ok"}]
        hist = huge_turn + tail

        messages, fresh = compact(hist, lambda t: "BRIEF", max_chars=100, keep_recent=1, max_fold_chars=1000)

        assert fresh == {"through": len(huge_turn), "text": "BRIEF"}
        assert messages[1:] == tail
