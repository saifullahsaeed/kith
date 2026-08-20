"""An image goes to the model as an image, and to nobody as base64.

The original leak sent a 600KB data URI into the conversation as text — twice — and cost
2.8 million tokens to look at three pages of a CV. That was fixed in the conversation, then
in the transcript, and the browser was missed: the live stream still carried the whole data
URI, where the generic result renderer printed it.

Which matters more than the bytes. Looking at the interface showed forty thousand characters
of base64 sitting in a tool result — indistinguishable from the bug that is actually fixed,
and reasonable grounds to think it had come back. A fix nobody can see is not finished.
"""

from __future__ import annotations

import json

from kith.services.agent_loop import _image_from, without_image
from kith.services.turn.history import _carries_image


def an_image_result(size: int = 44_019) -> dict:
    """What `run_tool` hands back for `read_file` on a screenshot."""
    return {
        "ok": True,
        "result": {
            "path": "/tmp/shot.png",
            "bytes": size,
            "image": "data:image/png;base64," + "A" * size,
            "note": "Look at the image below and describe what you actually see.",
        },
    }


class TestWhatTheModelReceives:
    def test_the_picture_arrives_as_a_picture(self):
        """Not as text. This is the whole point — his model takes images, and he spent hours
        redesigning a UI he could not see because `read_file` decodes as text."""
        image = _image_from(an_image_result())
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Here is /tmp/shot.png:"},
                {"type": "image_url", "image_url": {"url": image}},
            ],
        }
        assert image.startswith("data:image/png;base64,")
        assert _carries_image(message)

    def test_the_tool_result_beside_it_carries_no_base64(self):
        """It used to carry the same 600KB a second time — the leak was a dict spread that
        replaced the key on the outer envelope while the data sat on the inner one."""
        text = json.dumps(without_image(an_image_result()))
        assert "base64,AAAA" not in text
        assert len(text) < 400

    def test_what_is_left_still_says_which_image(self):
        left = without_image(an_image_result())["result"]
        assert left["path"] == "/tmp/shot.png"
        assert left["bytes"] == 44_019
        assert "picture" in left["image"].lower()


class TestWhatTheBrowserReceives:
    def test_the_live_stream_is_scrubbed_too(self):
        """The one that was missed, and the one you can actually see."""
        from kith.api.routes.chat import _readable

        event = {"type": "tool_result", "id": "c1", "name": "read_file", "result": an_image_result()}

        out = json.dumps(_readable(event))

        assert "base64,AAAA" not in out
        assert len(out) < 500, "44KB per image was crossing the wire and being rendered"

    def test_it_still_says_what_he_looked_at(self):
        from kith.api.routes.chat import _readable

        out = json.dumps(_readable({"type": "tool_result", "result": an_image_result()}))

        assert "/tmp/shot.png" in out
        assert "describe what you actually see" in out

    def test_an_ordinary_result_is_untouched(self):
        from kith.api.routes.chat import _readable

        event = {"type": "tool_result", "id": "c2", "name": "grep", "result": {"ok": True, "hits": 3}}
        assert _readable(event) == event
