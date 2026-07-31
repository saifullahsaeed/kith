"""He can look at the pictures he makes.

He spent hours redesigning a UI, took Playwright screenshots at 1440 and 390 on every pass,
attached them as deliverables — and could not see one of them, because `read_file` decodes
bytes as UTF-8 and reported "not text". His model takes images. He was working blind on the
one kind of task where looking *is* the job, and he closed those tasks as verified.

The proof this was worth doing: with it wired up, asked what was wrong with a modal he had
already shipped and marked verified, he said "bluntly, it looks unfinished — the entire left
half is empty, there's a stray clipped carrot graphic in the corner, the title wraps poorly".
All three are visible in the file and none were in the code he had been reading.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from kith.infra import workspace as ws
from kith.services.agent_loop import _image_from


def tiny_png(path, size=(4, 4)):
    """A real PNG, written without a dependency."""
    w, h = size
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


class TestReadingAnImage:
    def test_it_comes_back_as_a_data_uri(self, workspace_root):
        tiny_png(workspace_root / "shot.png")
        out = ws.read_image("shot.png")
        assert out["image"].startswith("data:image/png;base64,")
        assert out["bytes"] > 0

    @pytest.mark.parametrize("name", ["a.png", "b.jpg", "c.jpeg", "d.gif", "e.webp"])
    def test_the_types_worth_showing(self, workspace_root, name):
        assert ws.Path(name).suffix.lower() in ws._IMAGE_SUFFIXES

    def test_something_enormous_is_refused_with_advice(self, workspace_root, monkeypatch):
        monkeypatch.setattr(ws, "_MAX_IMAGE_BYTES", 100, raising=False)
        tiny_png(workspace_root / "big.png", size=(40, 40))
        with pytest.raises(ws.WorkspaceError) as caught:
            ws.read_image("big.png")
        # A payload nobody meant to send, with the way out named.
        assert "Shrink it" in str(caught.value)

    def test_a_missing_file(self, workspace_root):
        with pytest.raises(ws.WorkspaceError):
            ws.read_image("nope.png")


class TestTheLoopTurnsItIntoSomethingHeCanSee:
    def test_it_recognises_an_image_result(self, workspace_root):
        tiny_png(workspace_root / "shot.png")
        assert _image_from(ws.read_image("shot.png"))

    def test_it_looks_inside_a_wrapped_result(self, workspace_root):
        # Tool results arrive wrapped as {"ok": true, "result": {...}} in the loop.
        tiny_png(workspace_root / "shot.png")
        assert _image_from({"ok": True, "result": ws.read_image("shot.png")})

    def test_it_ignores_everything_else(self):
        for value in (None, "text", 3, {}, {"image": "not a data uri"}, {"image": 5}):
            assert _image_from(value) == ""

    def test_a_data_uri_that_is_not_an_image_is_ignored(self):
        # Only image data is turned into a picture; anything else claiming the key is not
        # something to hand a vision model.
        assert _image_from({"image": "data:text/html;base64,PGgxPg=="}) == ""


class TestWhenTheModelCannotSee:
    def test_read_file_says_so_instead_of_failing(self, workspace_root, monkeypatch):
        from kith.tools import registry

        tiny_png(workspace_root / "shot.png")
        monkeypatch.setattr(
            "kith.config.model_capabilities", lambda: {"images": False, "known": True}
        )
        tool = registry.get("read_file")
        handler = tool.run if hasattr(tool, "run") else tool

        out = handler(None, {"path": "shot.png"})

        # Not an error and not silence: a model that cannot see needs to be told to check
        # another way, or it will keep asking.
        assert "cannot see images" in out["note"]
        assert "image" not in out
