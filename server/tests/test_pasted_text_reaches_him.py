"""Text that was attached is given to him, not pointed at.

Every attachment is written into his folder and named in the message, which is the right answer
for a PDF or a spreadsheet: he has a computer and can open one. It was the wrong answer for text.

The composer lifts a large paste out of the message and carries it alongside as a file — so under
the old behaviour, pasting a log meant he was handed a path to a log whose entire contents were
already sitting in the request. He would spend a tool call opening what he had just been given, on
the one kind of attachment that never needed opening.

The other half of this is the ceiling. Inlining is bounded, and a bounded thing that stays quiet
about its bound is worse than one that refuses: he would read what he was given, believe it was
the whole file, and answer confidently about a config whose second half he never saw.
"""

from __future__ import annotations

import base64

import pytest

from kith.services.turn import prompt
from kith.services.turn.prompt import TEXT_INLINE_CHARS, _with_attachments


def _attachment(body: str, name: str = "pasted-1.txt", media: str = "text/plain") -> dict:
    return {
        "kind": "file",
        "name": name,
        "mediaType": media,
        "data": f"data:{media};base64,{base64.b64encode(body.encode()).decode()}",
    }


def _message(*attachments: dict, text: str = "have a look at this") -> dict:
    return {"role": "user", "content": text, "attachments": list(attachments)}


@pytest.fixture(autouse=True)
def _no_vision(monkeypatch):
    """Images are a different path entirely, and one that asks the model config a question."""
    monkeypatch.setattr(prompt, "model_capabilities", lambda: {"images": False})


class TestTextIsGiven:
    def test_the_contents_arrive_in_the_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)

        out = _with_attachments(_message(_attachment("the log line that matters")))

        assert "the log line that matters" in out["content"]

    def test_it_is_fenced_and_named(self, tmp_path, monkeypatch):
        """Named because he is going to talk about it back to them, and fenced because a paste
        dropped into the prose is indistinguishable from the prose."""
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)

        out = _with_attachments(_message(_attachment("body", name="pasted-1.txt")))

        assert "`pasted-1.txt`" in out["content"]
        assert "```\nbody\n```" in out["content"]

    def test_the_path_is_still_given(self, tmp_path, monkeypatch):
        """Inlining replaces nothing. The file is on disk either way, and the path is what makes
        it something they can open again from his reply."""
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)

        out = _with_attachments(_message(_attachment("body")))

        assert "inbox/pasted-1.txt" in out["content"]
        assert (tmp_path / "inbox" / "pasted-1.txt").read_text() == "body"

    def test_what_they_actually_wrote_comes_first(self, tmp_path, monkeypatch):
        """The message is the message; the paste is the material. Putting the material first
        buries the instruction under it."""
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)

        out = _with_attachments(_message(_attachment("x" * 100), text="what is wrong here"))

        assert out["content"].index("what is wrong here") < out["content"].index("```")

    def test_json_counts_as_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)

        out = _with_attachments(_message(_attachment('{"a": 1}', "conf.json", "application/json")))

        assert '{"a": 1}' in out["content"]


class TestTheCeiling:
    def test_a_huge_paste_is_cut(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)

        out = _with_attachments(_message(_attachment("y" * (TEXT_INLINE_CHARS * 3))))

        assert len(out["content"]) < TEXT_INLINE_CHARS * 2

    def test_it_says_that_it_cut(self, tmp_path, monkeypatch):
        """Out loud, with both numbers and the path. A silently shortened file is the worst
        version of this: he reads what he was given, believes it is everything, and answers with
        confidence about a second half he never saw."""
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)
        body = "y" * (TEXT_INLINE_CHARS + 500)

        out = _with_attachments(_message(_attachment(body)))

        assert f"{TEXT_INLINE_CHARS:,} characters of {len(body):,}" in out["content"]
        assert "inbox/pasted-1.txt" in out["content"]

    def test_a_file_that_fits_is_not_described_as_cut(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)

        out = _with_attachments(_message(_attachment("short enough")))

        assert "characters of" not in out["content"]


class TestWhatIsNotText:
    def test_a_binary_file_is_still_only_pointed_at(self, tmp_path, monkeypatch):
        """Unchanged, and deliberately. A spreadsheet is something he opens with python; inlining
        its bytes would fill the window with nothing readable."""
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)
        sheet = _attachment("ignored", "books.xlsx", "application/vnd.ms-excel")

        out = _with_attachments(_message(sheet))

        assert "inbox/books.xlsx" in out["content"]
        assert "```" not in out["content"]

    def test_an_empty_file_says_nothing_at_all(self, tmp_path, monkeypatch):
        """An empty fence is noise that looks like a mistake he has to account for."""
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)

        out = _with_attachments(_message(_attachment("   ")))

        assert "```" not in out["content"]

    def test_bytes_that_are_not_really_text_fall_back_to_the_path(self, tmp_path, monkeypatch):
        """A block of replacement characters is worse than a path — the path he can act on."""
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)
        broken = {
            "kind": "file",
            "name": "weird.txt",
            "mediaType": "text/plain",
            "data": "data:text/plain;base64," + base64.b64encode(b"\xff\xfe\x00\x01").decode(),
        }

        out = _with_attachments(_message(broken))

        assert "inbox/weird.txt" in out["content"]

    def test_a_message_with_no_attachments_is_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prompt.sandbox, "root", lambda: tmp_path)

        out = _with_attachments({"role": "user", "content": "just a sentence"})

        assert out == {"role": "user", "content": "just a sentence"}
