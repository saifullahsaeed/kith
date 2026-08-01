"""What a look at an image costs, and how many times it gets charged.

Measured on a real turn — "review my CV" — before any of this existed:

    round  5   prompt    61,844
    round  6   prompt   616,415   <- three page renders arrive
    round 20   prompt   686,748   <- and are still there, fifteen rounds later

    TOTALS     prompt 10,105,837   uncached 2,759,572

Two separate faults, and each one alone would have been enough.

The first is that the base64 was never removed from the tool result. The line meant to do
it spread the *outer* envelope — `{**result, "image": "..."}` — while the data URI sat on
the inner dict, so it added a decorative key and left 228,000 characters exactly where they
were. Every picture therefore went in twice: once as an image part, which a provider counts
as a few hundred tokens, and once as text, which it counts at about one token per character.

The second is that an image rides as its own user message, and the compactor only ever
walked `role == "tool"`. Nothing could take a picture out of the conversation once it was
in, so looking at one page cost the same as looking at it on every round that followed.
"""

from __future__ import annotations

import json

import pytest

from kith.services import tuning
from kith.services.agent_loop import (
    _carries_image,
    _compact_images,
    _image_from,
    _without_image,
)

BIG = "data:image/jpeg;base64," + "A" * 228_000


def wrapped(path: str = "/x/page-1.jpg") -> dict:
    """A tool result in the shape the loop actually receives: inside the {ok, result}
    envelope the runner wraps every handler's return value in."""
    return {
        "ok": True,
        "result": {"path": path, "bytes": 171_000, "image": BIG, "note": "Look at it."},
    }


def looked_at(path: str) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": f"Here is {path}:"},
            {"type": "image_url", "image_url": {"url": BIG}},
        ],
    }


class TestTheBase64LeavesTheToolResult:
    def test_the_picture_is_still_found_inside_the_envelope(self):
        assert _image_from(wrapped()) == BIG

    def test_and_is_gone_from_the_result_that_goes_into_the_conversation(self):
        text = json.dumps(_without_image(wrapped()))
        assert "AAAA" not in text
        # The 228,169-character message that used to be sent every round.
        assert len(text) < 1_000

    def test_it_says_where_the_picture_went_rather_than_going_quiet(self):
        cleaned = _without_image(wrapped())
        assert "shown to you" in cleaned["result"]["image"]

    def test_the_rest_of_the_result_survives(self):
        cleaned = _without_image(wrapped())
        assert cleaned["result"]["path"] == "/x/page-1.jpg"
        assert cleaned["result"]["bytes"] == 171_000
        assert cleaned["ok"] is True

    def test_an_unwrapped_result_is_handled_too(self):
        """Being right about only today's shape is what caused this in the first place."""
        text = json.dumps(_without_image({"path": "/x.png", "image": BIG}))
        assert "AAAA" not in text

    def test_a_result_with_no_picture_is_untouched(self):
        same = {"ok": True, "result": {"output": "hello", "exitCode": 0}}
        assert _without_image(same) == same

    def test_a_result_that_is_not_a_dict_is_returned_as_is(self):
        assert _without_image("just text") == "just text"


class TestAPictureIsSetDownOnceItHasBeenSeen:
    def test_the_newest_are_kept(self):
        tuning.apply({"keep_images": 2})
        convo = [looked_at(f"/p{n}.jpg") for n in range(4)]
        _compact_images(convo)
        assert [_carries_image(m) for m in convo] == [False, False, True, True]

    def test_an_older_one_keeps_its_name(self):
        """So he can tell a picture he looked at from one he never opened."""
        tuning.apply({"keep_images": 1})
        convo = [looked_at("/work/page-1.jpg"), looked_at("/work/page-2.jpg")]
        _compact_images(convo)
        assert "/work/page-1.jpg" in convo[0]["content"]
        assert "read the file again" in convo[0]["content"].lower()

    def test_dropping_it_is_what_makes_the_turn_affordable(self):
        tuning.apply({"keep_images": 1})
        convo = [looked_at(f"/p{n}.jpg") for n in range(3)]
        before = sum(len(json.dumps(m)) for m in convo)
        _compact_images(convo)
        after = sum(len(json.dumps(m)) for m in convo)
        # Three page renders is 650KB; this is the difference between paying for it once
        # and paying for it on every remaining round.
        assert before > 600_000
        assert after < before / 2

    def test_zero_means_he_looks_and_lets_go_immediately(self):
        tuning.apply({"keep_images": 0})
        convo = [looked_at("/p.jpg")]
        _compact_images(convo)
        assert not _carries_image(convo[0])

    def test_running_it_twice_changes_nothing_the_second_time(self):
        """It rewrites history, and every rewrite costs the cache from that point on. Doing
        it once per picture is affordable; doing it every round would not be."""
        tuning.apply({"keep_images": 1})
        convo = [looked_at("/a.jpg"), looked_at("/b.jpg")]
        _compact_images(convo)
        once = json.dumps(convo)
        _compact_images(convo)
        assert json.dumps(convo) == once

    def test_ordinary_messages_are_left_alone(self):
        tuning.apply({"keep_images": 0})
        convo = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "tool_name": "shell", "content": '{"output": "ok"}'},
        ]
        before = json.dumps(convo)
        _compact_images(convo)
        assert json.dumps(convo) == before


class TestTheKnob:
    def test_it_is_a_declared_setting_so_it_can_be_changed_without_a_restart(self):
        from kith.domain.tuning import for_key

        assert for_key("keep_images").default == 2

    @pytest.mark.parametrize("raw,expected", [(-5, 0), (999, 20)])
    def test_it_is_clamped_rather_than_refused(self, raw, expected):
        from kith.domain.tuning import for_key

        assert for_key("keep_images").coerce(raw) == expected
