"""Leaving a conversation mid-answer must not cost you the answer.

The turn already survived you leaving — it runs on its own thread. What did not survive was
everything it said while you were gone, because the only reader was the request that started
it and its queue died with the connection. Switching conversations therefore looked exactly
like the turn being killed and restarted: silence, then the finished reply in one piece.

The tests that matter here are about the join, because that is where a fan-out gets it wrong:
a line published while somebody is attaching has to land in the backlog or in their queue, and
never in neither.
"""

from __future__ import annotations

import threading

from kith.kernel import live_turns


class TestArrivingLate:
    def test_a_watcher_gets_what_it_missed_then_what_follows(self):
        turn = live_turns.begin("c1")
        live_turns.publish(turn, "one\n")
        live_turns.publish(turn, "two\n")

        watcher = live_turns.watch(turn)
        caught_up = [next(watcher), next(watcher)]

        live_turns.publish(turn, "three\n")
        assert caught_up == ["one\n", "two\n"], "the backlog is what you missed"
        assert next(watcher) == "three\n", "and then it follows along"
        live_turns.finish(turn)

    def test_two_watchers_see_the_same_turn(self):
        """The request that started it is not privileged — it is just the first reader."""
        turn = live_turns.begin("c1")
        first = live_turns.watch(turn)
        second = live_turns.watch(turn)
        live_turns.publish(turn, "shared\n")

        assert next(first) == "shared\n"
        assert next(second) == "shared\n"
        live_turns.finish(turn)

    def test_finishing_ends_every_watcher(self):
        """A reader left waiting on a turn that is over is a hung request."""
        turn = live_turns.begin("c1")
        watcher = live_turns.watch(turn)
        live_turns.publish(turn, "only\n")
        assert next(watcher) == "only\n"

        live_turns.finish(turn)
        assert list(watcher) == [], "the watcher should have been released, not blocked"

    def test_nothing_to_attach_to_once_it_is_over(self):
        """Afterwards the transcript is the record, and it is the authoritative one. Handing
        back a stale replay would put a second copy of the reply on screen."""
        turn = live_turns.begin("c1")
        assert live_turns.current("c1") is turn
        live_turns.finish(turn)
        assert live_turns.current("c1") is None


class TestTheJoinDoesNotDropAnything:
    def test_a_line_published_while_attaching_is_not_lost(self):
        """The race this is all about.

        Snapshot the backlog and register the queue under one lock, or a line published in
        between belongs to neither and is simply gone — which shows up as a missing word in
        the middle of a sentence, long after anyone would think to blame the plumbing.
        """
        turn = live_turns.begin("c1")
        seen: list[str] = []
        stop = threading.Event()

        def chatter():
            i = 0
            while not stop.is_set():
                live_turns.publish(turn, f"{i}\n")
                i += 1

        writer = threading.Thread(target=chatter, daemon=True)
        writer.start()
        try:
            watcher = live_turns.watch(turn)
            for _ in range(200):
                seen.append(next(watcher))
        finally:
            stop.set()
            writer.join(timeout=2)
            live_turns.finish(turn)

        numbers = [int(line) for line in seen]
        assert numbers == list(range(numbers[0], numbers[0] + len(numbers))), (
            f"the sequence has a hole in it: {numbers[:12]}…"
        )

    def test_a_watcher_that_leaves_is_forgotten(self):
        """Otherwise `publish` fills a queue nobody reads for the rest of the turn."""
        turn = live_turns.begin("c1")
        watcher = live_turns.watch(turn)
        live_turns.publish(turn, "a\n")
        # The queue is registered when the generator starts, not when it is created.
        assert next(watcher) == "a\n"
        assert len(turn.watchers) == 1

        watcher.close()
        assert turn.watchers == set(), "a departed reader must not be published to"
        live_turns.finish(turn)


class TestOneConversationAtATime:
    def test_a_second_turn_replaces_the_first(self):
        first = live_turns.begin("c1")
        second = live_turns.begin("c1")
        assert live_turns.current("c1") is second

        # And the older one finishing must not evict the newer one's record — the same identity
        # check `_disarm` needs in the chat route, for the same reason.
        live_turns.finish(first)
        assert live_turns.current("c1") is second
        live_turns.finish(second)
