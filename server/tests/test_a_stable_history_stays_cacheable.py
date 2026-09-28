"""Trimming the history and caching the history are the same fix, and they cancel out.

Two generations of solution to one problem — "tool results re-send in full on every round".
The first stubbed the old ones (`_compact_tool_history`). The second put a cache breakpoint on
the last message, so round N reads what round N-1 wrote (`llm/caching.py`). Caching needs a
byte-identical prefix; stubbing rewrites the middle of the array. The older fix wins by
default and silently disables the newer one.

It is worse than a wash, because the eviction point *moves forward* as the conversation grows,
so a different slice of the middle is rewritten every round. Measured on a 12-result
conversation before the gate: the prefix diverged at message 11 of 26, leaving 7% of the turn
byte-identical and 93% re-billed uncached — by the mechanism installed to stop it being
re-billed.

And it was firing for nothing. The budget is 80,000 characters against a 1,050,000-token
window: 1.9% of the room. In one real session Kith read `ModelsSettings.tsx` fifteen times,
`.kith/memory.md` thirteen times, and 54% of every read he made was of a file he had already
read. The knob's own help text predicted it — "trimming it makes him fetch the same pages
again" — and the default was simply never raised when the model moved to a million tokens.

So compaction is gated on actually being short of room. These tests pin both directions: it
stays out of the way when there is space, and it still fires when there is not.

The suite passed unchanged when the gate was added, which is why this file exists — nothing
here exercised compaction against a *known* window, so every existing test ran the
window == 0 branch and could not tell the two behaviours apart.
"""

from __future__ import annotations

import json
from typing import Any

from kith.llm import ledger
from kith.services import agent_loop
from kith.services import history as fold_policy

#: A million-token model, as configured on this install.
BIG = 1_050_000
#: What a default local install reports.
SMALL = 40_960


def _convo(results: int, size: int = 9_000) -> list[dict[str, Any]]:
    """A turn that has read `results` files of `size` characters each."""
    convo: list[dict[str, Any]] = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "go"},
    ]
    for i in range(results):
        convo.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": f"/f{i}.tsx"}),
                        },
                    }
                ],
            }
        )
        convo.append(
            {"role": "tool", "tool_call_id": f"c{i}", "name": "read_file", "content": f"F{i}:" + "x" * size}
        )
    return convo


def _past_fold_point(convo: list[dict[str, Any]], window: int) -> bool:
    """`_make_room`'s own question: the ledger's reading of the whole request, against the one
    fold point both folds use."""
    return fold_policy.fold_due(ledger.take(convo, window=window).used, window)


def _compact_as_the_loop_does(convo: list[dict[str, Any]], window: int) -> None:
    """Exactly what one round of `_make_room` does to the history, short of a fold call.

    Kept faithful to the loop deliberately. This helper used to gate *everything* behind
    `_room_is_tight`, which stopped being what the loop does when tool history moved to an
    unconditional pass sized by the window — and because the helper duplicated the loop rather
    than calling it, every test in this file went on passing while pinning a contract the code
    no longer had. If `_make_room` changes, this changes with it.

    Images and call arguments are shaved only when `room.is_tight`, which never fires before a
    round has been measured — so a round with no measurement behind it, which is what this is,
    shaves neither. Past the fold point alone is a reason to fold, not to shave.
    """
    agent_loop._compact_tool_history(convo, window=window)


def _first_divergence(a: list[dict], b: list[dict]) -> int | None:
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return None


class TestThePrefixSurvivesTheNextRound:
    """The property the cache actually needs, stated directly."""

    def test_a_turn_with_room_to_spare_is_never_rewritten(self):
        """Round N+1's history must contain round N's, unchanged, as a prefix. Anything else
        and the breakpoint written at round N cannot be read at round N+1."""
        at_n, at_n_plus_1 = _convo(12), _convo(13)
        _compact_as_the_loop_does(at_n, BIG)
        _compact_as_the_loop_does(at_n_plus_1, BIG)
        assert _first_divergence(at_n, at_n_plus_1) is None
        assert at_n_plus_1[: len(at_n)] == at_n

    def test_it_holds_across_many_rounds_of_growth(self):
        """The failure moved with the eviction point, so one round's worth of comparison could
        miss it. This walks a turn the way a real one grows."""
        previous: list[dict] | None = None
        for results in range(1, 40):  # 40 x 9,000 = 360,000, inside the live budget
            current = _convo(results)
            _compact_as_the_loop_does(current, BIG)
            if previous is not None:
                assert current[: len(previous)] == previous, f"prefix broke at {results} results"
            previous = current

    def test_a_big_turn_is_left_whole(self):
        """Still true, and now true for a stated reason rather than because nothing ever ran.

        Forty nine-thousand-character reads is 360,000 characters, which is inside the live
        budget a million-token window buys (~466,000). The September property this file exists
        to protect — a real research turn is never rewritten — is unchanged.

        A hundred reads is not: 900,000 characters is past the budget and is now aged out, which
        is the whole point of the change. See `test_the_live_set_is_sized_to_the_window`."""
        convo = _convo(40)
        held_before = sum(len(str(m.get("content") or "")) for m in convo)
        _compact_as_the_loop_does(convo, BIG)
        held_after = sum(len(str(m.get("content") or "")) for m in convo)
        assert held_after == held_before


class TestTheValveStillOpens:
    def test_an_unknown_window_still_ages_out_tool_output(self):
        """0 is load-bearing — it means nobody can say how much room there is. The tool pass
        falls back to its absolute budget, so a long unknown-window turn is still trimmed."""
        convo = _convo(12)
        _compact_as_the_loop_does(convo, 0)
        stubbed = [m for m in convo if m.get("role") == "tool" and len(str(m.get("content"))) < 9_000]
        assert stubbed, "an unknown window must still be compacted"

    def test_a_small_model_over_its_share_is_still_reduced(self):
        """14 results is ~126k characters, past 80% of a 40,960-token window."""
        convo = _convo(14)
        assert _past_fold_point(convo, SMALL) is True
        _compact_as_the_loop_does(convo, SMALL)
        assert sum(len(str(m.get("content") or "")) for m in convo) < 14 * 9_000

    def test_a_small_model_with_room_is_left_alone_too(self):
        """The fold point caps at 80% of the window, not a special case for big models. 12 results
        is ~108k characters — about 29k tokens of a 32,768-token fold point — and fits."""
        convo = _convo(12)
        assert _past_fold_point(convo, SMALL) is False

    def test_a_turn_that_really_is_enormous_gets_trimmed(self):
        """The relief valve. Past the fold point, losing the cache beats losing the turn."""
        convo = _convo(350)
        held = sum(len(str(m.get("content") or "")) for m in convo)
        assert held > fold_policy.fold_at(BIG) * 3.7
        assert _past_fold_point(convo, BIG) is True
        _compact_as_the_loop_does(convo, BIG)
        assert sum(len(str(m.get("content") or "")) for m in convo) < held

    def test_the_threshold_is_where_it_claims_to_be(self):
        """Just under and just over, so the boundary is pinned rather than implied."""
        budget_chars = fold_policy.fold_at(BIG) * 3.7
        under = [{"role": "tool", "content": "x" * int(budget_chars * 0.9)}]
        over = [{"role": "tool", "content": "x" * int(budget_chars * 1.1)}]
        assert _past_fold_point(under, BIG) is False
        assert _past_fold_point(over, BIG) is True

    def test_an_unknown_window_folds_at_the_setting(self):
        """Not "always compact" — that was the old behaviour and it meant a two-message
        conversation on a local model was run through the compactor on every round — and not
        "never", which is what a threshold made of an unknown window would be."""
        assert _past_fold_point(_convo(1), 0) is False
        enormous = [{"role": "tool", "content": "x" * int(fold_policy.fold_at(0) * 3.7 * 1.1)}]
        assert _past_fold_point(enormous, 0) is True
