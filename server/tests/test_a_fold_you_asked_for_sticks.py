"""A fold you asked for has to survive the turn after it.

`/fold` summarises the older turns on demand. It did the summarising — a real model call, on a
real conversation, reporting the hundreds of thousands of characters it had removed — and then
threw the brief away. `history.fold` is pure but for the summariser: it returns the folded list
*and* the brief it made, and says in its own docstring that the caller persists it. The turn path
does. This one did not.

From outside, that is a command with no effect: the meter drops the moment the fold answers, then
the very next message replays the untouched transcript and it climbs straight back to where it
was. Worse than doing nothing, because the summarisation call was paid for.

These tests are about persistence, not about summarising — the summariser is stubbed. What is
being checked is that the brief reaches the transcript, and that the next turn's own fold finds it
there and replays it instead of the conversation it covers.
"""

from __future__ import annotations

import itertools

import flask
import pytest

from kith.api.routes import chat
from kith.services import conversations, history

BRIEF = "They agreed the meter reads the last turn's reading."


@pytest.fixture
def app_context():
    """Just enough Flask for `jsonify`. The route is being called as a function, not served."""
    with flask.Flask(__name__).app_context():
        yield


@pytest.fixture
def no_model(monkeypatch):
    """The summariser, stubbed. A fold must never make a network call in a test."""
    monkeypatch.setattr(history, "_summarize", lambda text, config, host: BRIEF)


#: One id per call, because the transcript is a file and `record_event` appends to it.
#:
#: Every test here used the same `"folded-on-purpose"`, and the data directory is session-scoped
#: — so the second test to run found the first test's twelve turns already in the file and added
#: twelve more. By the fourth there were forty-eight, which is past the fold threshold, and
#: `test_without_the_brief_the_next_turn_sends_everything_again` failed because a fold it had
#: asserted would not happen did. The third test's own docstring says "this conversation sits
#: well under it", which was true only for whichever test ran first.
#:
#: It stayed hidden because `pytest-randomly` shuffles the order, so the run's verdict depended
#: on the seed — green most times, red when the wrong test drew first place. A counter rather
#: than a uuid so a failure reproduces on the next run.
_next_conversation = itertools.count(1)


def _conversation(turns: int = 12, size: int = 2_000) -> str:
    conversation_id = f"folded-on-purpose-{next(_next_conversation)}"
    for n in range(turns):
        conversations.record_event(
            conversation_id, "message", {"role": "user", "content": f"q{n} " + "x" * size}
        )
        conversations.record_event(
            conversation_id, "message", {"role": "assistant", "content": f"a{n} " + "y" * size}
        )
    return conversation_id


class TestTheBriefReachesTheTranscript:
    def test_a_fold_records_the_brief_it_made(self, app_context, no_model):
        conversation_id = _conversation()
        assert conversations.latest_summary(conversation_id) == {}

        body = chat.fold_now(conversation_id).get_json()

        assert body["folded"] is True
        stored = conversations.latest_summary(conversation_id)
        assert stored["text"] == BRIEF
        # The cursor matters as much as the text: it is how the next turn knows which messages
        # the brief already covers, and a brief without one covers nothing.
        assert stored["through"] > 0

    def test_asking_twice_is_answered_honestly(self, app_context, no_model):
        """The second `/fold` has nothing new to fold, and now knows why — which is only true
        because the first one left a record of itself."""
        conversation_id = _conversation()
        chat.fold_now(conversation_id)

        body = chat.fold_now(conversation_id).get_json()

        assert body["folded"] is False
        assert "Already folded" in body["note"]


class TestTheNextTurnHonoursIt:
    def test_the_turn_after_a_fold_replays_the_brief_instead_of_the_conversation(
        self, app_context, no_model, monkeypatch
    ):
        """The whole point, and the thing that was broken.

        The next turn folds with its *budget* threshold rather than `force`, and this
        conversation sits well under it — which is exactly the case that used to replay
        everything. `compact` skips its "short and never folded" exit once a brief exists, so
        the stored one is reused and the turns it covers are not sent again.
        """
        conversation_id = _conversation()
        chat.fold_now(conversation_id)

        replayed, fresh = history.fold(
            conversations.full_messages(conversation_id), chat.default_config(), conversation_id
        )

        assert fresh is None, "reused the stored brief rather than paying for another summary"
        assert replayed[0]["role"] == "system"
        assert BRIEF in replayed[0]["content"]
        assert len(replayed) < len(conversations.full_messages(conversation_id))

    def test_without_the_brief_the_next_turn_sends_everything_again(self, app_context, no_model):
        """The bug, pinned. Fold, then lose the record of it, and the conversation comes back
        whole — which is what the meter was reporting when it climbed back to where it started."""
        conversation_id = _conversation()
        chat.fold_now(conversation_id)
        # Every trace of the fold, removed — the state the route used to leave behind.
        path = conversations.transcript_path(conversation_id)
        path.write_text(
            "\n".join(line for line in path.read_text().splitlines() if '"type": "summary"' not in line)
            + "\n"
        )
        assert conversations.latest_summary(conversation_id) == {}

        stored = conversations.full_messages(conversation_id)
        replayed, _ = history.fold(stored, chat.default_config(), conversation_id)

        assert replayed == stored
