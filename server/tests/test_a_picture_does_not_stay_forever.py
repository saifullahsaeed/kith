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
from kith.services.agent_loop import _image_from, _without_image
from kith.services.turn.history import _carries_image, _compact_images

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


class TestAnOldWriteLetsGoOfTheFile:
    """The third channel, and the last one nothing was watching.

    `read_file` and `shell` cap their output at 8,000 characters and the compactor trims
    those results as they age. But `write_file`'s `content` argument *is* the file, and it
    travels on the assistant message — past every limit in the system, because none of them
    were looking at the calls.
    """

    def call(self, path: str, body: str) -> dict:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": path, "content": body}),
                    },
                }
            ],
        }

    def args(self, message: dict) -> dict:
        return json.loads(message["tool_calls"][0]["function"]["arguments"])

    def test_an_old_write_keeps_its_path_and_drops_its_content(self):
        from kith.services.turn.history import _compact_call_arguments

        tuning.apply({"keep_full_tool_results": 1, "tool_stub_chars": 100})
        convo = [self.call("/big.py", "x" * 40_000), self.call("/small.py", "y" * 40_000)]
        _compact_call_arguments(convo)

        old = self.args(convo[0])
        assert old["path"] == "/big.py"  # still says what he wrote, and where
        assert "40,000 characters" in old["content"]
        assert "xxxx" not in old["content"]
        # The most recent one is untouched — he may still be working on it.
        assert self.args(convo[1])["content"] == "y" * 40_000

    def test_the_arguments_stay_valid_json(self):
        """A provider rejects the whole request otherwise, which would turn a saving into
        an outage."""
        from kith.services.turn.history import _compact_call_arguments

        tuning.apply({"keep_full_tool_results": 1, "tool_stub_chars": 100})
        convo = [self.call("/x.py", "z" * 5_000), self.call("/y.py", "z" * 5_000)]
        _compact_call_arguments(convo)
        assert isinstance(self.args(convo[0]), dict)
        assert "5,000 characters" in self.args(convo[0])["content"]

    def test_a_small_write_is_left_alone(self):
        from kith.services.turn.history import _compact_call_arguments

        tuning.apply({"keep_full_tool_results": 1, "tool_stub_chars": 1_200})
        convo = [self.call("/tiny.py", "print('hi')"), self.call("/other.py", "pass")]
        before = json.dumps(convo)
        _compact_call_arguments(convo)
        assert json.dumps(convo) == before

    def test_an_edit_drops_both_halves_of_the_replacement(self):
        from kith.services.turn.history import _compact_call_arguments

        tuning.apply({"keep_full_tool_results": 1, "tool_stub_chars": 100})
        edit = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "edit_file",
                        "arguments": json.dumps({"path": "/a.py", "old": "o" * 9_000, "new": "n" * 9_000}),
                    },
                }
            ],
        }
        convo = [edit, self.call("/later.py", "x")]
        _compact_call_arguments(convo)
        args = self.args(convo[0])
        assert "9,000 characters" in args["old"] and "9,000 characters" in args["new"]
        assert args["path"] == "/a.py"

    def test_unparseable_arguments_are_left_alone_rather_than_corrupted(self):
        from kith.services.turn.history import _compact_call_arguments

        tuning.apply({"keep_full_tool_results": 1, "tool_stub_chars": 10})
        convo = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "write_file", "arguments": "{not json at all"}}
                ],
            },
            self.call("/later.py", "x"),
        ]
        before = json.dumps(convo)
        _compact_call_arguments(convo)
        assert json.dumps(convo) == before

    def test_running_it_twice_changes_nothing(self):
        from kith.services.turn.history import _compact_call_arguments

        tuning.apply({"keep_full_tool_results": 1, "tool_stub_chars": 100})
        convo = [self.call("/x.py", "q" * 20_000), self.call("/later.py", "x")]
        _compact_call_arguments(convo)
        once = json.dumps(convo)
        _compact_call_arguments(convo)
        assert json.dumps(convo) == once

    def test_the_floor_cannot_be_set_to_zero_so_a_lone_write_survives(self):
        """`keep_full_tool_results` has a minimum of 1, which three earlier versions of these
        tests did not know — they set it to 0, got 1, and asserted against a conversation
        where nothing was old enough to trim. All three passed without exercising a line."""
        from kith.domain.tuning import for_key

        assert for_key("keep_full_tool_results").coerce(0) == 1
