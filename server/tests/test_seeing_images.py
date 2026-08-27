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
from kith.services.agent_loop import _images_from


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
    monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


class TestReadingAnImage:
    def test_it_comes_back_as_a_data_uri(self, workspace_root):
        tiny_png(workspace_root / "shot.png")
        out = ws.read_image("shot.png")
        assert out["image"].startswith("data:image/png;base64,")
        assert out["bytes"] > 0

    @pytest.mark.parametrize("name", ["a.png", "b.jpg", "c.jpeg", "d.gif", "e.webp"])
    def test_the_types_worth_showing(self, workspace_root, name):
        assert ws.paths.Path(name).suffix.lower() in ws.files._IMAGE_SUFFIXES

    def test_something_enormous_is_refused_with_advice(self, workspace_root, monkeypatch):
        monkeypatch.setattr(ws.files, "_MAX_IMAGE_BYTES", 100, raising=False)
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
        assert _images_from(ws.read_image("shot.png"))

    def test_it_looks_inside_a_wrapped_result(self, workspace_root):
        # Tool results arrive wrapped as {"ok": true, "result": {...}} in the loop.
        tiny_png(workspace_root / "shot.png")
        assert _images_from({"ok": True, "result": ws.read_image("shot.png")})

    def test_it_ignores_everything_else(self):
        for value in (None, "text", 3, {}, {"image": "not a data uri"}, {"image": 5}):
            assert _images_from(value) == []

    def test_a_data_uri_that_is_not_an_image_is_ignored(self):
        # Only image data is turned into a picture; anything else claiming the key is not
        # something to hand a vision model.
        assert _images_from({"image": "data:text/html;base64,PGgxPg=="}) == []


class TestWhenTheModelCannotSee:
    def test_read_file_says_so_instead_of_failing(self, workspace_root, monkeypatch):
        from kith.tools import registry

        tiny_png(workspace_root / "shot.png")
        monkeypatch.setattr("kith.config.model_capabilities", lambda: {"images": False, "known": True})
        # Every tool handler takes `(path, args)`; `read_file` resolves through the sandbox
        # and never touches the database, so the path is irrelevant here — but it has to be one.
        out = registry.require("read_file").run(workspace_root, {"path": "shot.png"})

        # Not an error and not silence: a model that cannot see needs to be told to check
        # another way, or it will keep asking.
        assert "cannot see images" in out["note"]
        assert "image" not in out


@pytest.fixture
def a_model_that_sees(monkeypatch):
    """`read_file` refuses an image outright when the configured model has no vision.

    Named rather than stubbed quietly, because it is the other reason a batch of screenshots can
    come back as prose with no picture in it — and on a text-only model that is the correct
    answer rather than the bug. The suite's default configuration has no model at all.
    """
    import kith.config

    monkeypatch.setattr(kith.config, "model_capabilities", lambda *a, **k: {"images": True})


class TestABatchOfScreenshots:
    """Reading several at once has to deliver several, not none.

    The bug this closes was live and self-reported. Mid-way through a UI review he read seven
    screenshots in one call — because `35-how-you-spend-a-round.md` tells him to batch what is
    independent — and wrote: "The screenshots are saved but I need to actually view them as
    images to judge the UI." He was right. `_read_many` flattened each image dict to its `note`
    and dropped the data URI, so the result came back a plain string, the loop's extractor only
    looked at dicts, and the three sentences that arrived all read "Look at the image below"
    with nothing below.

    Exactly the failure the module docstring above describes, in the path that the advice to
    batch makes the common one.
    """

    def test_every_picture_in_the_batch_arrives(self, workspace_root, a_model_that_sees):
        from pathlib import Path

        from kith.tools import computer

        for name in ("dashboard.png", "funds.png", "investors.png"):
            tiny_png(workspace_root / name)
        (workspace_root / "notes.txt").write_text("not a picture\n")

        out = computer.read_file(
            Path(), {"paths": ["dashboard.png", "funds.png", "investors.png", "notes.txt"]}
        )

        pictures = _images_from(out)
        assert [Path(one["path"]).name for one in pictures] == [
            "dashboard.png",
            "funds.png",
            "investors.png",
        ]
        assert "not a picture" in out["text"], "the text files still come back as text"

    def test_the_base64_does_not_also_travel_as_text(self, workspace_root, a_model_that_sees):
        """The same doubling `without_image` exists to prevent, at the plural shape."""
        import json
        from pathlib import Path

        from kith.services.agent_loop import without_image
        from kith.tools import computer

        for name in ("a.png", "b.png"):
            tiny_png(workspace_root / name)

        out = computer.read_file(Path(), {"paths": ["a.png", "b.png"]})
        assert "data:image/" not in json.dumps(without_image(out))

    def test_a_batch_of_text_is_shaped_exactly_as_before(self, workspace_root):
        """No picture, no new shape — a batch that reads code must not change on anyone."""
        from pathlib import Path

        from kith.tools import computer

        (workspace_root / "a.py").write_text("print(1)\n")
        (workspace_root / "b.py").write_text("print(2)\n")

        out = computer.read_file(Path(), {"paths": ["a.py", "b.py"]})
        assert isinstance(out, str)
        assert _images_from(out) == []

    def test_a_model_without_vision_still_gets_the_plain_refusal(self, workspace_root):
        """The gate above this is not the bug — on a text-only model, prose is the right answer."""
        from pathlib import Path

        from kith.tools import computer

        tiny_png(workspace_root / "a.png")
        out = computer.read_file(Path(), {"paths": ["a.png", "a.png"]})
        assert isinstance(out, str)
        assert "cannot see images" in out
