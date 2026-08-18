"""Asking what is in the window, on purpose, without waiting for a turn to say.

The meter reports categories: "code he has read, 831k". That is the number and not the answer —
831k of files he needed once and 831k of one file read sixty times are the same figure and
completely different problems, and only the second is something you can do anything about.

Deliberately its own endpoint rather than more fields on the `context` event the loop already
streams. That event is emitted every turn and persisted into the transcript: one real
conversation here holds 167 of them. Hanging two and a half thousand items off each would grow
every stored turn forever to serve a screen that is open for a few seconds a month, and would put
the cost of the detail on every person who never opens it. A reading is streamed; a breakdown is
asked for.

The totals it reports are the stored reading's own, untouched — the same numbers the meter in the
rail is showing. A detail screen that recomputes them would eventually disagree with the meter
beside it, and two numbers that disagree are worse than one number with no detail.
"""

from __future__ import annotations

import itertools

import flask
import pytest

from kith.api.routes import chat
from kith.services import conversations

#: One id per call — the transcript is an append-only file and the data directory is
#: session-scoped, so a shared id makes each test read whatever the last one left behind.
_next_conversation = itertools.count(1)


@pytest.fixture
def app_context():
    """Just enough Flask for `jsonify`. The route is called as a function, not served."""
    with flask.Flask(__name__).app_context():
        yield


def _read(conversation_id: str, path: str, size: int) -> None:
    """One file read, recorded the way a turn records one."""
    call_id = f"c{next(_next_conversation)}"
    conversations.record_event(
        conversation_id, "tool_call", {"id": call_id, "name": "read_file", "arguments": {"path": path}}
    )
    conversations.record_event(
        conversation_id, "tool_result", {"id": call_id, "name": "read_file", "result": "x" * size}
    )


def _conversation(reading: dict | None = None) -> str:
    conversation_id = f"what-is-in-the-window-{next(_next_conversation)}"
    conversations.record_event(conversation_id, "message", {"role": "user", "content": "have a look"})
    _read(conversation_id, "src/app.tsx", 4_000)
    _read(conversation_id, "src/app.tsx", 4_000)
    _read(conversation_id, "src/other.tsx", 4_000)
    if reading is not None:
        conversations.record_event(conversation_id, "context", {"context": reading})
    return conversation_id


#: A stored reading, in the shape `Ledger.as_wire` produces.
READING = {
    "window": 1_000_000,
    "used": 40_000,
    "free": 960_000,
    "share": 0.04,
    "charsPerToken": 4.0,
    "lines": [
        {"key": "code", "label": "Code he has read", "tokens": 30_000, "share": 0.03},
        {"key": "messages", "label": "Messages", "tokens": 10_000, "share": 0.01},
    ],
}


class TestItAnswersWithTheMeterSOwnNumbers:
    def test_the_totals_are_the_stored_reading_untouched(self, app_context):
        body = chat.context_detail(_conversation(READING)).get_json()

        assert body["window"] == 1_000_000
        assert body["used"] == 40_000
        assert body["free"] == 960_000
        assert [line["key"] for line in body["lines"]] == ["code", "messages"]

    def test_it_names_the_file_that_was_read_twice(self, app_context):
        body = chat.context_detail(_conversation(READING)).get_json()

        repeated = [item for item in body["items"] if item["subject"] == "src/app.tsx"]
        assert len(repeated) == 1
        assert repeated[0]["calls"] == 2
        assert repeated[0]["wasted"] > 0

    def test_a_file_read_once_is_not_reported_as_waste(self, app_context):
        body = chat.context_detail(_conversation(READING)).get_json()

        once = next(item for item in body["items"] if item["subject"] == "src/other.tsx")
        assert once["calls"] == 1
        assert once["wasted"] == 0

    def test_the_worst_offender_comes_first(self, app_context):
        body = chat.context_detail(_conversation(READING)).get_json()

        assert body["items"][0]["subject"] == "src/app.tsx"

    def test_it_totals_the_waste_so_the_screen_can_lead_with_it(self, app_context):
        body = chat.context_detail(_conversation(READING)).get_json()

        assert body["wasted"] == sum(item["wasted"] for item in body["items"])
        assert body["wasted"] > 0


class TestItIsHonestWhenItCannotAnswer:
    def test_a_conversation_that_has_never_had_a_turn_reports_no_reading(self, app_context):
        """The honest answer, and the one the screen needs in order to say so rather than
        drawing an empty chart at 0%. A first reading is the next turn's to take."""
        body = chat.context_detail(_conversation(reading=None)).get_json()

        assert body["reading"] is False
        assert body["window"] == 0

    def test_it_still_itemises_what_it_can_see(self, app_context):
        """The reads happened whether or not a turn ever measured them. Reporting nothing at all
        would hide real repeats behind a missing measurement."""
        body = chat.context_detail(_conversation(reading=None)).get_json()

        assert any(item["subject"] == "src/app.tsx" for item in body["items"])

    def test_an_unknown_conversation_is_empty_rather_than_an_error(self, app_context):
        body = chat.context_detail("no-such-conversation").get_json()

        assert body["reading"] is False
        assert body["items"] == []


class TestTheItemsAreTheConversationNotTheWindow:
    """The correction that the finished screen forced, by printing "121% of the window".

    A reading measures the request a turn actually sent, which is the conversation *after* the
    fold — old turns replaced by a brief, their tool results gone with them. `full_messages` is
    the transcript, which is everything that ever happened. On one real conversation here those
    are 556,392 tokens and 2,361,997 tokens: the window is a quarter of the history.

    So the items cannot be a breakdown *of* the reading, and the first version's claim that they
    were produced a waste figure larger than the window it was a share of. Reproducing the folded
    list is not an option either — folding is a summarisation call, and a screen you open to look
    at something must never spend money to draw itself.

    Both numbers are worth having, and the fix is to stop conflating them: the reading says what
    is in the window, the items say what he has read across the conversation. The second is still
    the actionable one — a file read fifty-one times is a habit that will refill the window
    whether or not this particular copy survived the last fold.
    """

    def test_the_items_are_totalled_on_their_own_scale(self, app_context):
        """So the screen can say "of what he has read" rather than "of the window", which is the
        share that went over 100%."""
        body = chat.context_detail(_conversation(READING)).get_json()

        assert body["itemsTotal"] == sum(item["tokens"] for item in body["items"])
        assert body["wasted"] <= body["itemsTotal"]

    def test_the_items_may_exceed_the_window_without_lying(self, app_context):
        """A transcript larger than the window is the normal state of a folded conversation, not
        a bug — and nothing in the response should imply otherwise."""
        small = {**READING, "used": 10, "free": 999_990, "share": 0.00001}
        body = chat.context_detail(_conversation(small)).get_json()

        assert body["used"] == 10
        assert body["itemsTotal"] > body["used"]

    def test_the_lines_still_come_from_the_reading_untouched(self, app_context):
        """The window half of the screen must keep agreeing with the meter in the rail."""
        body = chat.context_detail(_conversation(READING)).get_json()

        assert body["lines"] == READING["lines"]
        assert body["used"] == READING["used"]
