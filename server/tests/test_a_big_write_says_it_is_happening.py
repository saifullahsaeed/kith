"""Writing a large file used to look exactly like doing nothing.

A tool call's arguments arrive as a stream of fragments and were accumulated in silence. For a
small call that is invisible and right. For a `write_file` carrying a 21,591-character page it is
close to a minute in which the interface shows nothing at all — no prose, no tool row, no counter —
so watching a file be written is indistinguishable from watching a turn hang. The completed call
then arrives stamped `21,591 chars`, which is the one moment the size no longer needs reporting.

So the transport says so while it happens, and says it rarely enough to still be about the turn.
"""

from __future__ import annotations

from kith.llm.openai_compat import _WRITING_EVERY, _writing


def _slot(arguments: str, said: int = 0, name: str = "write_file") -> dict:
    return {"name": name, "arguments": arguments, "said": said}


class TestSayingSo:
    def test_it_reports_once_the_call_has_grown_enough(self):
        event = _writing(_slot("x" * _WRITING_EVERY))
        assert event is not None
        assert event["type"] == "writing"
        assert event["chars"] == _WRITING_EVERY
        assert event["name"] == "write_file"

    def test_it_says_nothing_about_a_small_call(self):
        """Most calls are a path and a flag. A progress line for those is noise on every round."""
        assert _writing(_slot('{"path": "notes.md"}')) is None

    def test_it_says_nothing_again_until_it_has_grown_again(self):
        """A page arrives as roughly a thousand fragments. One event each would put a thousand
        frames on the wire to report one action."""
        slot = _slot("x" * _WRITING_EVERY)
        assert _writing(slot) is not None
        slot["arguments"] += "x" * (_WRITING_EVERY - 1)
        assert _writing(slot) is None
        slot["arguments"] += "x"
        assert _writing(slot) is not None

    def test_the_count_is_what_has_arrived_so_far(self):
        slot = _slot("x" * (_WRITING_EVERY * 3))
        assert _writing(slot)["chars"] == _WRITING_EVERY * 3


class TestNamingTheFile:
    def test_the_path_is_read_out_of_the_half_written_json(self):
        """ "writing wukong-site/index.html" is a different sentence from "writing". `path` is
        conventionally the first key of the tools that write, so it is complete long before the
        body is."""
        slot = _slot('{"path": "wukong-site/index.html", "content": "' + "x" * _WRITING_EVERY)
        assert _writing(slot)["path"] == "wukong-site/index.html"

    def test_a_path_that_has_not_arrived_yet_is_simply_absent(self):
        """Degrades to the tool's own name rather than guessing at a filename."""
        slot = _slot('{"content": "' + "x" * _WRITING_EVERY)
        written = _writing(slot)
        assert written is not None
        assert "path" not in written

    def test_other_spellings_of_the_argument_are_found_too(self):
        for key in ("path", "file", "target"):
            slot = _slot(f'{{"{key}": "a/b.txt", "content": "' + "x" * _WRITING_EVERY)
            assert _writing(slot)["path"] == "a/b.txt", key

    def test_an_escaped_quote_in_the_path_does_not_end_it_early(self):
        r"""A regex over half-written JSON has to survive `\"` inside the value, or a path with
        one in it is truncated to something that looks like a real but different file."""
        slot = _slot(r'{"path": "odd\"name.txt", "content": "' + "x" * _WRITING_EVERY)
        assert _writing(slot)["path"] == r"odd\"name.txt"
