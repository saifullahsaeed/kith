"""The prompt, as a list of messages, with what each one costs.

The context endpoint answered in categories: eleven lines, "code he has read: 260k". That is the
shape of the ledger, and it is not the shape of the question. Nobody opens a context screen to
learn that a category is large — they open it to see *what is actually being sent*, in order,
message by message, and to read the one that looks wrong.

So: the same list a turn would send (`prompt.as_sent`, which never pays for a fold), each entry
costed with the same `message_chars` the ledger uses, plus the provider's own figures for the
last round — cached, written, and what it cost — which are measured rather than estimated and
are already sitting in the transcript as `stats` events.

Full message text is deliberately *not* in this payload. One real conversation here is 2.36M
tokens of transcript; shipping all of it to draw a list of previews would be a several-megabyte
response for a screen that shows one message at a time. The list carries previews, and
`/context/message/<i>` fetches the one you clicked.
"""

from __future__ import annotations

import itertools

import flask
import pytest

from kith.api.routes import chat
from kith.services import conversations

_next = itertools.count(1)


@pytest.fixture
def app_context():
    with flask.Flask(__name__).app_context():
        yield


def _conversation(*, with_stats: bool = True) -> str:
    conversation_id = f"message-by-message-{next(_next)}"
    conversations.record_event(conversation_id, "message", {"role": "user", "content": "hey"})
    conversations.record_event(
        conversation_id, "message", {"role": "assistant", "content": "Hey — Kith here."}
    )
    call_id = f"c{next(_next)}"
    conversations.record_event(
        conversation_id,
        "tool_call",
        {"id": call_id, "name": "read_file", "arguments": {"path": "src/App.tsx"}},
    )
    conversations.record_event(
        conversation_id, "tool_result", {"id": call_id, "name": "read_file", "result": "x" * 2_000}
    )
    if with_stats:
        conversations.record_event(
            conversation_id,
            "stats",
            {
                "stats": {
                    "model": "openai/gpt-5.6-luna",
                    "provider": "OpenAI",
                    "promptTokens": 17_963,
                    "responseTokens": 110,
                    "cachedTokens": 11_474,
                    "cacheWriteTokens": 11_176,
                    "costUsd": 0.0021417,
                }
            },
        )
    return conversation_id


class TestEveryMessageIsListedInOrder:
    def test_it_lists_the_messages_a_turn_would_send(self, app_context):
        body = chat.context_detail(_conversation()).get_json()

        roles = [m["role"] for m in body["sent"]["messages"]]
        assert roles[0] == "system", "the persona leads"
        assert "user" in roles and "assistant" in roles and "tool" in roles

    def test_each_message_is_numbered_from_one_as_the_screen_shows_it(self, app_context):
        body = chat.context_detail(_conversation()).get_json()

        numbers = [m["index"] for m in body["sent"]["messages"]]
        assert numbers == list(range(1, len(numbers) + 1))

    def test_each_message_carries_what_it_costs(self, app_context):
        body = chat.context_detail(_conversation()).get_json()

        assert all(m["tokens"] > 0 for m in body["sent"]["messages"])
        assert body["sent"]["tokens"] == sum(m["tokens"] for m in body["sent"]["messages"])

    def test_a_tool_result_says_which_tool(self, app_context):
        """ "tool" is a role, not an answer. Which tool ran is the part that makes the row
        identifiable in a list of forty of them."""
        body = chat.context_detail(_conversation()).get_json()

        tools = [m["tool"] for m in body["sent"]["messages"] if m["role"] == "tool"]
        assert tools == ["read_file"]

    def test_the_list_carries_previews_rather_than_whole_messages(self, app_context):
        """A 2,000-character tool result is small. One real conversation's transcript is 2.36M
        tokens, and shipping it to draw a list of previews is a several-megabyte response."""
        body = chat.context_detail(_conversation()).get_json()

        assert all(len(m["preview"]) <= 240 for m in body["sent"]["messages"])
        assert all("text" not in m for m in body["sent"]["messages"])


class TestACallCarriesTheArgumentsItWasMadeWith:
    """A tool call is two messages and one thing that happened, so the screen folds them into one
    row — and the first version of that row showed only the result. The arguments went missing.

    They are *sent*: an assistant turn carrying `tool_calls` is a real message in the prompt,
    costed and billed like any other, and the command inside it is usually the only part that says
    what the row is. Hiding it on a screen whose whole purpose is showing what gets sent is the
    one mistake this screen cannot make.

    Structured rather than folded into the preview text, because the pairing test reads it too:
    matching a call to its result on `calls[0].name` is exact, where matching on a preview that
    happens to read "calls grep" is a string that could be anything.
    """

    def test_an_assistant_turn_reports_the_call_it_carries(self, app_context):
        listed = chat.context_detail(_conversation()).get_json()["sent"]["messages"]

        calling = next(m for m in listed if m["calls"])
        assert calling["calls"][0]["name"] == "read_file"

    def test_the_arguments_are_readable_rather_than_a_shape(self, app_context):
        """ "1 argument" is not the command. The path is."""
        listed = chat.context_detail(_conversation()).get_json()["sent"]["messages"]

        calling = next(m for m in listed if m["calls"])
        assert "src/App.tsx" in calling["calls"][0]["args"]

    def test_a_message_that_calls_nothing_says_so_plainly(self, app_context):
        listed = chat.context_detail(_conversation()).get_json()["sent"]["messages"]

        assert all(m["calls"] == [] for m in listed if m["role"] in ("user", "tool"))

    def test_every_call_in_a_multi_call_turn_is_listed(self, app_context):
        """Two calls answered by two results. The row cannot be folded — pairing the first result
        onto it would attach it to the wrong call — and the screen needs to see both to know."""
        conversation_id = f"two-calls-{next(_next)}"
        conversations.record_event(conversation_id, "message", {"role": "user", "content": "both"})
        for name, path in (("read_file", "a.tsx"), ("grep", "b.tsx")):
            call = f"c{next(_next)}"
            conversations.record_event(
                conversation_id,
                "tool_call",
                {"id": call, "name": name, "arguments": {"path": path}},
            )
            conversations.record_event(
                conversation_id, "tool_result", {"id": call, "name": name, "result": "x" * 200}
            )

        listed = chat.context_detail(conversation_id).get_json()["sent"]["messages"]

        names = [c["name"] for m in listed for c in m["calls"]]
        assert names == ["read_file", "grep"]


class TestItSaysWhereTheCostSits:
    def test_it_totals_by_role(self, app_context):
        """The shape of the answer to "why is this prompt so big" is almost always one role."""
        body = chat.context_detail(_conversation()).get_json()

        by_role = {row["role"]: row for row in body["sent"]["byRole"]}
        assert by_role["system"]["count"] >= 1
        assert sum(row["tokens"] for row in body["sent"]["byRole"]) == body["sent"]["tokens"]

    def test_the_roles_are_ordered_by_what_they_cost(self, app_context):
        body = chat.context_detail(_conversation()).get_json()

        tokens = [row["tokens"] for row in body["sent"]["byRole"]]
        assert tokens == sorted(tokens, reverse=True)


class TestTheProvidersOwnFiguresForTheLastRound:
    def test_it_reports_what_was_actually_billed(self, app_context):
        """Measured, not estimated. Everything else on this screen is `message_chars` divided by
        a ratio; these four came back from the provider."""
        body = chat.context_detail(_conversation()).get_json()

        assert body["lastRound"]["promptTokens"] == 17_963
        assert body["lastRound"]["cachedTokens"] == 11_474
        assert body["lastRound"]["costUsd"] == pytest.approx(0.0021417)
        assert body["lastRound"]["model"] == "openai/gpt-5.6-luna"

    def test_a_conversation_with_no_round_yet_reports_nothing_rather_than_zeroes(self, app_context):
        """A cost of $0.00 and a model of "" read as facts. They are the absence of one."""
        body = chat.context_detail(_conversation(with_stats=False)).get_json()

        assert body["lastRound"] is None


class TestReadingOneMessageWhole:
    def test_it_returns_the_message_the_list_pointed_at(self, app_context):
        conversation_id = _conversation()
        listed = chat.context_detail(conversation_id).get_json()["sent"]["messages"]
        wanted = next(m for m in listed if m["role"] == "tool")

        body = chat.context_message(conversation_id, wanted["index"]).get_json()

        assert body["index"] == wanted["index"]
        assert body["role"] == "tool"
        assert len(body["text"]) > len(wanted["preview"])

    def test_an_index_past_the_end_is_empty_rather_than_an_error(self, app_context):
        body = chat.context_message(_conversation(), 9_999).get_json()

        assert body["text"] == ""
