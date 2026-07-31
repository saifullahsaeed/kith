"""Attaching something to a message, and it actually arriving.

The paperclip had never worked. Not "worked badly" — no conversation transcript on this
machine has ever contained an attachment, in either direction. Three separate faults, each
enough on its own:

* The composer used ``SimpleImageAttachmentAdapter``, which accepts ``image/*`` and nothing
  else. A spreadsheet could not be attached at all, so the server's file branch was dead code.
* That adapter puts the data URL on ``attachment.content``, and the code building the request
  read ``message.content``, then skipped images in the attachments list believing the first
  pass had taken them. Dropped twice.
* A non-image was sent as a bare filename with ``data: ""`` and announced to him as
  "[They attached: report.pdf. Read it with your own tools.]" — no path, no bytes, nothing
  written anywhere. He was told to open a file that did not exist.

So the rule now: write it into his folder first, then decide what the model can be shown. That
order is what makes the fallback real, because a picture a model cannot see is still a file he
can open.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from kith.api.routes import chat
from kith.infra import workspace as ws

#: A one-pixel PNG, as the composer sends it.
PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
        "00000049454e44ae426082"
    )
).decode()


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


def _sees_images(monkeypatch, answer: bool):
    monkeypatch.setattr(chat, "model_capabilities", lambda: {"images": answer, "known": True})


def image(name="shot.png"):
    return {"kind": "image", "name": name, "mediaType": "image/png", "data": f"data:image/png;base64,{PNG}"}


def spreadsheet(name="budget.xlsx"):
    body = base64.b64encode(b"PK\x03\x04 not really a workbook").decode()
    return {
        "kind": "file",
        "name": name,
        "mediaType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "data": f"data:application/octet-stream;base64,{body}",
    }


class TestItLandsOnDisk:
    def test_a_file_is_written_where_he_can_read_it(self, workspace_root, monkeypatch):
        _sees_images(monkeypatch, False)
        chat._with_attachments({"role": "user", "content": "have a look", "attachments": [spreadsheet()]})
        assert (workspace_root / "inbox" / "budget.xlsx").is_file()

    def test_the_path_he_is_given_is_the_path_that_exists(self, workspace_root, monkeypatch):
        _sees_images(monkeypatch, False)
        out = chat._with_attachments({"role": "user", "content": "", "attachments": [spreadsheet()]})
        # The exact failure being fixed: he used to be told to read a file by name, with no
        # path and nothing on disk. Whatever the message names has to resolve.
        assert "`inbox/budget.xlsx`" in out["content"]
        assert (workspace_root / "inbox" / "budget.xlsx").exists()

    def test_the_bytes_are_intact(self, workspace_root, monkeypatch):
        _sees_images(monkeypatch, True)
        chat._with_attachments({"role": "user", "content": "", "attachments": [image()]})
        written = (workspace_root / "inbox" / "shot.png").read_bytes()
        assert written.startswith(b"\x89PNG"), "not a PNG — the data URL was decoded wrong"

    def test_an_image_is_saved_even_when_he_can_see_it(self, workspace_root, monkeypatch):
        _sees_images(monkeypatch, True)
        chat._with_attachments({"role": "user", "content": "", "attachments": [image()]})
        # Both, not either: seeing it now does not help the tick tomorrow that wants to measure
        # it, and a file on disk is the only version that outlives this turn.
        assert (workspace_root / "inbox" / "shot.png").is_file()

    def test_two_different_files_with_one_name_both_survive(self, workspace_root, monkeypatch):
        _sees_images(monkeypatch, False)
        first = spreadsheet()
        second = {
            **spreadsheet(),
            "data": "data:application/octet-stream;base64," + base64.b64encode(b"different").decode(),
        }
        chat._with_attachments({"role": "user", "content": "", "attachments": [first]})
        out = chat._with_attachments({"role": "user", "content": "", "attachments": [second]})
        # Overwriting would silently replace something they sent earlier and may still be
        # talking about.
        assert (workspace_root / "inbox" / "budget.xlsx").read_bytes() == b"PK\x03\x04 not really a workbook"
        assert "budget-2.xlsx" in out["content"]

    def test_the_same_file_twice_is_not_duplicated(self, workspace_root, monkeypatch):
        _sees_images(monkeypatch, False)
        for _ in range(3):
            chat._with_attachments({"role": "user", "content": "", "attachments": [spreadsheet()]})
        assert sorted(p.name for p in (workspace_root / "inbox").iterdir()) == ["budget.xlsx"]

    def test_a_name_cannot_escape_the_folder(self, workspace_root, monkeypatch):
        _sees_images(monkeypatch, False)
        evil = {**spreadsheet(), "name": "../../.zshrc"}
        chat._with_attachments({"role": "user", "content": "", "attachments": [evil]})
        assert not (workspace_root.parent.parent / ".zshrc").exists()
        assert list((workspace_root / "inbox").iterdir()), "it should still have saved something"


class TestWhatTheModelIsShown:
    def test_an_image_is_inlined_when_the_model_has_vision(self, monkeypatch):
        _sees_images(monkeypatch, True)
        out = chat._with_attachments({"role": "user", "content": "what is this", "attachments": [image()]})
        parts = out["content"]
        assert isinstance(parts, list)
        assert [p["type"] for p in parts] == ["text", "image_url"]
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_it_is_not_inlined_when_the_model_cannot_see(self, monkeypatch):
        _sees_images(monkeypatch, False)
        out = chat._with_attachments({"role": "user", "content": "what is this", "attachments": [image()]})
        # A string, not parts: sending image_url to a text-only model is either dropped
        # silently or a 400, and both are worse than telling him where the file is.
        assert isinstance(out["content"], str)
        assert "inbox/shot.png" in out["content"]

    def test_and_he_is_told_why_he_cannot_see_it(self, monkeypatch):
        _sees_images(monkeypatch, False)
        out = chat._with_attachments({"role": "user", "content": "", "attachments": [image()]})
        # Otherwise he describes a picture he was never shown, which is the worst outcome
        # available here.
        assert "cannot be shown images" in out["content"]

    def test_a_spreadsheet_is_never_inlined(self, monkeypatch):
        _sees_images(monkeypatch, True)
        out = chat._with_attachments({"role": "user", "content": "", "attachments": [spreadsheet()]})
        assert isinstance(out["content"], str), "a workbook was sent as an image"

    def test_a_mixed_message_does_both(self, workspace_root, monkeypatch):
        _sees_images(monkeypatch, True)
        out = chat._with_attachments(
            {"role": "user", "content": "these two", "attachments": [image(), spreadsheet()]}
        )
        parts = out["content"]
        assert [p["type"] for p in parts] == ["text", "image_url"]
        assert "`inbox/budget.xlsx`" in parts[0]["text"]
        assert (workspace_root / "inbox" / "shot.png").exists()

    def test_a_message_with_no_attachments_is_untouched(self, monkeypatch):
        _sees_images(monkeypatch, True)
        out = chat._with_attachments({"role": "user", "content": "just talking"})
        assert out == {"role": "user", "content": "just talking"}

    def test_a_save_that_fails_does_not_lose_the_turn(self, monkeypatch):
        _sees_images(monkeypatch, False)
        broken = {"kind": "file", "name": "x.bin", "mediaType": "application/octet-stream", "data": ""}
        out = chat._with_attachments({"role": "user", "content": "here", "attachments": [broken]})
        # Their words still reach him, and the failure is named rather than swallowed.
        assert "here" in out["content"]
        assert "could not be saved" in out["content"]


class TestTheComposerAndTheServerAgree:
    """Reaches into the interface, like the Mind-feed test, and for the same reason.

    The server can only save what the composer sends. The composer restricted itself to
    ``image/*`` while the server had a whole branch for files, and nothing anywhere failed —
    the branch was simply unreachable, which is why this went unnoticed for as long as it did.
    """

    def _source(self, *parts):
        from kith import settings

        return (settings.SERVER_ROOT.parent / "ui" / "src" / Path(*parts)).read_text()

    def test_the_composer_accepts_more_than_images(self):
        adapter = self._source("lib", "attachments.ts")
        assert 'accept = "*"' in adapter, "the composer is restricting attachments by type again"

    def test_the_image_only_adapter_is_gone(self):
        workspace = self._source("components", "workspace.tsx")
        # It accepts image/* only, and its send() hides the payload where the wire step was
        # not looking. Either alone breaks the feature.
        assert "SimpleImageAttachmentAdapter" not in workspace

    def test_the_wire_step_reads_the_attachment_not_just_the_message(self):
        wire = self._source("lib", "backend", "stream.ts")
        # Where the bytes actually are. Reading only message.content is what dropped them.
        assert "message.attachments" in wire
        assert "kithData" in wire or "attachment.content" in wire

    def test_the_attach_button_is_not_hidden_by_capability(self):
        workspace = self._source("components", "workspace.tsx")
        # It used to appear only for models reporting vision, so on a text model a file could
        # not be attached at all — even though a file is exactly what a text model can use.
        assert "capabilities?.images ?" not in workspace
