"""A turn that is running is delivered by exactly one channel, and both ends can say which.

The interface had two sources for the same words and no way to tell they were the same words.

* `GET /api/conversations/<id>` builds its timeline from the append-only transcript, and the
  recorder writes `reasoning` and `said` rows *as the turn runs* — so during a turn this endpoint
  already returns the half-written reply.
* `GET /api/chat/<id>/attach` hands back the live backlog, which is the same half-written reply.

The client rendered both — `thread.reset()` for one, `thread.resumeRun()` for the other — and
nothing correlated them, because nothing on the wire said "these are the same turn". So it
reconciled them by *timing*, and every interleaving was a bug somebody had reported: the transcript
arriving first meant the reply appeared twice, and the stream arriving first meant the history was
dropped on a guard that was never retried.

Identity is the fix, and it belongs here rather than in a client-side heuristic: a turn gets an id
when it begins, the transcript records it, and every channel that carries the turn says which turn
it is carrying. Then "is this the turn I am already rendering" is a question with an answer instead
of a race.
"""

from __future__ import annotations

import json

import pytest

from kith.kernel import events, live_turns
from kith.services import conversations


@pytest.fixture(autouse=True)
def no_leftover_turns():
    yield
    live_turns._LIVE.clear()
    events.log._subscribers.clear()


@pytest.fixture
def client():
    from kith import settings
    from kith.api import auth
    from kith.app import create_app

    made = create_app()
    made.config["TESTING"] = True
    given = made.test_client()
    given.environ_base[f"HTTP_{auth.HEADER.upper().replace('-', '_')}"] = auth.token(settings.DATA_DIR)
    return given


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    """Write a transcript by hand and read it back as a timeline."""
    monkeypatch.setattr(conversations, "transcript_path", lambda cid: tmp_path / f"{cid}.jsonl")

    def write(conversation_id: str, entries: list[dict]) -> str:
        path = tmp_path / f"{conversation_id}.jsonl"
        path.write_text("\n".join(json.dumps(one) for one in entries) + "\n")
        return conversation_id

    return write


class TestTheTranscriptNamesItsTurns:
    """A turn marker in the transcript, and every assistant entry stamped with it."""

    def test_an_assistant_turn_carries_the_id_of_the_turn_that_wrote_it(self, transcript):
        made = transcript(
            "c1",
            [
                {"type": "message", "role": "user", "content": "hello"},
                {"type": "turn", "turn": "T1"},
                {"type": "reasoning", "text": "thinking"},
                {"type": "said", "text": "hello back"},
            ],
        )
        turns = conversations.timeline(made)

        assert [one["role"] for one in turns] == ["user", "assistant"]
        # The person's message is not part of the turn's output and must never be stripped
        # with it — it is what he said, and it is history the moment he said it.
        assert turns[0].get("turn") is None
        assert turns[1]["turn"] == "T1"

    def test_a_reply_recorded_before_turns_had_ids_simply_has_none(self, transcript):
        """Every transcript on disk predates this. An entry with no id is never claimed by a
        stream, which is the safe direction: it renders as history, which is what it is."""
        made = transcript("c2", [{"type": "said", "text": "from before"}])

        assert conversations.timeline(made)[0].get("turn") is None

    def test_a_steered_turn_stamps_every_part_of_itself_with_one_id(self, transcript):
        """A steer records a user message mid-turn, which closes one assistant entry and opens
        another. Both were written by the same turn and both must say so, or attaching to that
        turn strips half of what it is about to replay."""
        made = transcript(
            "c3",
            [
                {"type": "turn", "turn": "T7"},
                {"type": "said", "text": "starting"},
                {"type": "message", "role": "user", "content": "actually, wait"},
                {"type": "said", "text": "alright"},
            ],
        )
        turns = conversations.timeline(made)

        assert [one["role"] for one in turns] == ["assistant", "user", "assistant"]
        assert turns[0]["turn"] == "T7"
        assert turns[2]["turn"] == "T7"

    def test_a_new_turn_takes_over_from_the_one_before(self, transcript):
        made = transcript(
            "c4",
            [
                {"type": "turn", "turn": "T1"},
                {"type": "said", "text": "first"},
                {"type": "message", "role": "user", "content": "again"},
                {"type": "turn", "turn": "T2"},
                {"type": "said", "text": "second"},
            ],
        )
        turns = conversations.timeline(made)

        assert turns[0]["turn"] == "T1"
        assert turns[2]["turn"] == "T2"

    def test_the_marker_is_not_itself_something_that_happened(self, transcript):
        """It is bookkeeping, not a part. A turn whose only entry is its own marker never
        reached the model and must not render as an empty reply."""
        made = transcript("c5", [{"type": "turn", "turn": "T1"}])

        assert conversations.timeline(made) == []


class TestTheStreamSaysWhichTurnItIs:
    """Both streaming routes name their turn in a header, so a client knows before it reads a
    byte which turn it has taken ownership of."""

    def test_attaching_names_the_turn_it_joined(self, client):
        live = live_turns.begin("c-live")
        try:
            response = client.get("/api/chat/c-live/attach")
            assert response.status_code == 200
            assert response.headers.get("X-Kith-Turn") == live.id
        finally:
            response.close()

    def test_a_turn_has_an_id_the_moment_it_begins(self):
        live = live_turns.begin("c-id")
        assert live.id
        assert live_turns.begin("c-id-2").id != live.id

    def test_nothing_running_is_still_an_empty_answer(self, client):
        assert client.get("/api/chat/c-idle/attach").status_code == 204
