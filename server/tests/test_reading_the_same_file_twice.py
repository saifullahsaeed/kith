"""The second identical read carries nothing the first lacked, so it does not go in twice.

Measured on one real chat session: `ModelsSettings.tsx` read 15 times, `.kith/memory.md` 13,
`models.py` 10 — and 54% of every read Kith made was of a file he had already read in that same
turn. `read_file` results were 58.6% of all tool output.

The obvious implementation is a sweep — walk the turn and collapse the older copies — and it is
wrong for a reason that took a measurement to see: it rewrites a message that has already been
sent, so the prompt prefix changes and everything after that point is re-billed uncached. On a
long turn, breaking the prefix early to save one duplicate costs far more than the duplicate.

So the question is asked *before appending*, and the history stays append-only. Nothing already
sent is ever touched. That is the property these tests pin, more than the saving itself.

This also has to be distinguished from stubbing, which it superficially resembles. A stub throws
content away and leaves him a 1,200-character fragment — which is what *causes* fifteen reads,
because the fragment tells him he saw something he can no longer see. A pointer keeps the content
in the turn, once, and says where it is.
"""

from __future__ import annotations

import itertools
import json

from kith.services import agent_loop
from kith.services.turn import history

BIG = json.dumps({"path": "/ModelsSettings.tsx", "text": "x" * 4_000})
ALSO_BIG = json.dumps({"path": "/api.ts", "text": "y" * 4_000})
SMALL = json.dumps({"ok": True})


def _appended(convo: list[dict], name: str, payload: str) -> dict:
    message = agent_loop._tool_result_message(convo, name, payload)
    convo.append(message)
    return message


class TestTheDuplicateBecomesAPointer:
    def test_the_first_read_goes_in_whole(self):
        convo: list[dict] = []
        assert _appended(convo, "read_file", BIG)["content"] == BIG

    def test_the_second_identical_read_does_not(self):
        convo: list[dict] = []
        _appended(convo, "read_file", BIG)
        second = _appended(convo, "read_file", BIG)
        assert second["content"] != BIG
        assert second["_deduped"] is True
        assert len(second["content"]) < 300

    def test_the_pointer_tells_him_it_is_still_there(self):
        """A stub that reads like a loss invites him to go and read the file again — which is
        how a 1,200-character stub becomes fifteen full reads."""
        convo: list[dict] = []
        _appended(convo, "read_file", BIG)
        pointer = _appended(convo, "read_file", BIG)["content"]
        assert "still" in pointer and "above" in pointer
        assert "no need to read it again" in pointer

    def test_fifteen_reads_cost_one(self):
        convo: list[dict] = []
        for _ in range(15):
            _appended(convo, "read_file", BIG)
        whole = [m for m in convo if m["content"] == BIG]
        assert len(whole) == 1
        assert sum(len(m["content"]) for m in convo) < len(BIG) * 2


class TestWhatItRefusesToCollapse:
    def test_a_file_he_edited_in_between_is_not_a_duplicate(self):
        """Both survive. The difference between them is the record of his own change, which is
        the last thing to throw away."""
        convo: list[dict] = []
        _appended(convo, "read_file", BIG)
        changed = json.dumps({"path": "/ModelsSettings.tsx", "text": "x" * 3_999 + "z"})
        assert _appended(convo, "read_file", changed)["content"] == changed

    def test_a_different_file_is_not_a_duplicate(self):
        convo: list[dict] = []
        _appended(convo, "read_file", BIG)
        assert _appended(convo, "read_file", ALSO_BIG)["content"] == ALSO_BIG

    def test_the_same_text_from_a_different_tool_is_not_a_duplicate(self):
        """`grep` returning what `read_file` returned is a different fact about the codebase."""
        convo: list[dict] = []
        _appended(convo, "read_file", BIG)
        assert _appended(convo, "grep", BIG)["content"] == BIG

    def test_a_small_result_is_left_alone(self):
        """A pointer is not free, and a short result carries its own answer. Collapsing
        `{"ok": true}` into a sentence about `{"ok": true}` makes the prompt bigger."""
        convo: list[dict] = []
        for _ in range(5):
            _appended(convo, "check_item", SMALL)
        assert all(m["content"] == SMALL for m in convo)

    def test_a_pointer_is_never_itself_used_as_the_original(self):
        """Otherwise the third read would point at the second read's pointer, and the content
        would be unreachable from where he is looking."""
        convo: list[dict] = []
        _appended(convo, "read_file", BIG)
        _appended(convo, "read_file", BIG)
        assert history._already_in(convo, "read_file", BIG) == 0  # the whole one, at index 0
        assert convo[0]["content"] == BIG

    def test_index_zero_is_found_rather_than_read_as_absent(self):
        """`None`, not `0`, for "not present" — 0 is a real index. In a live turn the system
        prompt occupies it so a tool result never does, which would have made a falsy sentinel
        correct in production and silently broken everywhere else."""
        convo: list[dict] = [{"role": "tool", "tool_name": "read_file", "content": BIG}]
        assert history._already_in(convo, "read_file", BIG) == 0
        assert history._already_in([], "read_file", BIG) is None
        assert agent_loop._tool_result_message(convo, "read_file", BIG)["_deduped"] is True


class TestTheHistoryStaysAppendOnly:
    def test_nothing_already_sent_is_touched(self):
        """The property that makes this safe, and the one a sweep implementation would break."""
        convo: list[dict] = []
        _appended(convo, "read_file", BIG)
        _appended(convo, "read_file", ALSO_BIG)
        before = [dict(m) for m in convo]
        _appended(convo, "read_file", BIG)
        assert convo[: len(before)] == before

    def test_the_prefix_survives_a_run_of_repeated_reads(self):
        convo: list[dict] = []
        seen: list[list[dict]] = []
        for _ in range(10):
            _appended(convo, "read_file", BIG)
            seen.append([dict(m) for m in convo])
        for earlier, later in itertools.pairwise(seen):
            assert later[: len(earlier)] == earlier
