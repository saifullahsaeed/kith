"""Opening a conversation must not cost the whole conversation.

The measurement this exists to hold: the largest real transcript is 492 turns and 12,957
parts, and `GET /api/conversations/<id>` sent all of it — 22.83 MB — so that the interface
could render the last 40 turns, 1.06 MB of it. The 95% it threw away was still downloaded,
`JSON.parse`d, and kept in React state, because the window was applied client-side after the
payload had already arrived.

The server was never slow: it builds those 492 turns in 121 ms. The cost was the wire and the
browser, so the fix is where the slice happens, not how the turns are built.

This is the second time the same endpoint has grown this shape. Its own comment records
`messages` and `entries` being removed to save 25.6 MB of a 48.1 MB response; `timeline` was
left whole. So the tests below assert the *default*, not merely that a window is available —
an endpoint whose default ships everything grows this back the first time someone writes a
caller without reading the query string.
"""

from __future__ import annotations

import json

import pytest

from kith.services import conversations


@pytest.fixture
def client():
    """A test client on the real blueprint — `create_app`, so the URL map is this project's."""
    from kith import settings
    from kith.api import auth
    from kith.app import create_app

    made = create_app()
    made.config["TESTING"] = True
    given = made.test_client()
    given.environ_base[f"HTTP_{auth.HEADER.upper().replace('-', '_')}"] = auth.token(settings.DATA_DIR)
    return given


@pytest.fixture
def long_conversation(tmp_path, monkeypatch):
    """A hundred turns, each with a user message and a reply."""
    monkeypatch.setattr(conversations, "transcript_path", lambda cid: tmp_path / f"{cid}.jsonl")
    lines = []
    for turn in range(100):
        lines.append({"type": "message", "role": "user", "text": f"ask {turn}", "at": f"t{turn}"})
        lines.append({"type": "said", "text": f"answer {turn}", "at": f"t{turn}"})
    (tmp_path / "big.jsonl").write_text("\n".join(json.dumps(one) for one in lines) + "\n")
    return "big"


class TestTheWindow:
    def test_the_whole_timeline_is_still_available(self, long_conversation):
        """`services/checkpoints` maps a checkpoint onto a turn index and needs every turn to
        do it. Windowing underneath that would misalign restores silently."""
        assert len(conversations.timeline(long_conversation)) == 200

    def test_a_window_is_the_last_n_turns(self, long_conversation):
        window = conversations.timeline_window(long_conversation, turns=40)

        assert len(window["turns"]) == 40
        assert window["total"] == 200
        assert window["start"] == 160
        assert window["hasMore"] is True
        assert window["turns"][-1]["parts"][0]["text"] == "answer 99"

    def test_the_previous_page_joins_on_with_no_gap_and_no_overlap(self, long_conversation):
        last = conversations.timeline_window(long_conversation, turns=40)
        earlier = conversations.timeline_window(long_conversation, turns=40, before=last["start"])

        assert earlier["start"] == 120
        assert len(earlier["turns"]) == 40
        whole = conversations.timeline(long_conversation)
        assert earlier["turns"] + last["turns"] == whole[120:200], "the two pages must join"

    def test_the_first_page_says_there_is_no_more(self, long_conversation):
        first = conversations.timeline_window(long_conversation, turns=1000)

        assert first["start"] == 0
        assert first["hasMore"] is False
        assert len(first["turns"]) == 200

    def test_asking_before_the_beginning_is_empty_rather_than_an_error(self, long_conversation):
        """A client that keeps pulling gets an empty page, not a 500 and not the last page
        again — the second of which is an infinite scroll that never ends."""
        page = conversations.timeline_window(long_conversation, turns=40, before=0)

        assert page["turns"] == []
        assert page["hasMore"] is False

    def test_a_conversation_that_is_not_there_is_an_empty_window(self, long_conversation):
        page = conversations.timeline_window("nothing-here", turns=40)

        assert page == {"turns": [], "total": 0, "start": 0, "hasMore": False}


class TestTheEndpointDefault:
    """The part that actually fixes it. A window nobody asks for is a window nobody gets."""

    def test_the_default_is_windowed_not_whole(self, client, long_conversation, monkeypatch):
        monkeypatch.setattr(conversations, "get", lambda _db, cid: {"id": cid, "messages": 200})

        body = client.get(f"/api/conversations/{long_conversation}").get_json()

        assert len(body["timeline"]) == 40, "the default must not ship the whole conversation"
        assert body["turnCount"] == 200
        assert body["windowStart"] == 160
        assert body["hasMore"] is True

    def test_everything_is_still_reachable_on_request(self, client, long_conversation, monkeypatch):
        monkeypatch.setattr(conversations, "get", lambda _db, cid: {"id": cid, "messages": 200})

        body = client.get(f"/api/conversations/{long_conversation}?turns=0").get_json()

        assert len(body["timeline"]) == 200
        assert body["hasMore"] is False

    def test_a_page_can_be_asked_for_by_where_the_last_one_started(
        self, client, long_conversation, monkeypatch
    ):
        monkeypatch.setattr(conversations, "get", lambda _db, cid: {"id": cid, "messages": 200})

        body = client.get(f"/api/conversations/{long_conversation}?turns=40&before=160").get_json()

        assert body["windowStart"] == 120
        assert len(body["timeline"]) == 40

    def test_nonsense_in_the_query_string_falls_back_rather_than_failing(
        self, client, long_conversation, monkeypatch
    ):
        monkeypatch.setattr(conversations, "get", lambda _db, cid: {"id": cid, "messages": 200})

        body = client.get(f"/api/conversations/{long_conversation}?turns=lots&before=x").get_json()

        assert len(body["timeline"]) == 40, "a bad number is the default, not a 400"
