"""Showing a file as itself, and opening it where it lives.

Two bugs in one screenshot. He had taken a PNG, and the viewer answered "This one needs its
own application — it's not text, so there's nothing to show here", which is nonsense about a
picture: the browser draws PNGs. Under that, in red, the copy that was supposed to make
"Open in Preview" work had failed with ``[Errno 17] File exists``.

The second one is the more interesting bug, because it was never a bug in the copy. The copy
itself was vestigial. His files used to live in a Docker container, where ``docker cp`` was
the only way anything else on the machine could see them; the container went away and the
copy stayed, quietly duplicating every artifact into ``~/Kith files`` and opening the
duplicate — so "open" could show a stale version of a file he had since changed. Deleting it
is what fixes Errno 17, and these tests pin the absence: that opening reaches the real path,
and that nothing lands beside it.
"""

from __future__ import annotations

import pytest

from kith.infra import workspace as ws
from kith.services import handoff


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


#: A one-pixel PNG. Real bytes, because the point of every one of these is that the file is
#: handled as bytes rather than decoded as text — a fake made of ASCII would pass tests that
#: the actual failure would still fail.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


class TestServingTheBytes:
    def test_a_png_comes_back_as_a_png(self, workspace_root):
        (workspace_root / "shot.png").write_bytes(PNG)
        target, kind = ws.media_file("shot.png")
        # The content type is the whole point: the browser decides how to render from it,
        # and application/octet-stream downloads instead of showing.
        assert kind == "image/png"
        assert target.read_bytes() == PNG

    def test_a_pdf_is_labelled_as_one(self, workspace_root):
        (workspace_root / "report.pdf").write_bytes(b"%PDF-1.4\n")
        assert ws.media_file("report.pdf")[1] == "application/pdf"

    def test_it_returns_a_path_not_the_bytes(self, workspace_root):
        # So the response can stream and answer range requests. A PDF viewer reads the
        # trailer first and seeks; handing it one buffer means loading a whole document to
        # show page one.
        (workspace_root / "report.pdf").write_bytes(b"%PDF-1.4\n")
        target, _ = ws.media_file("report.pdf")
        assert target.is_file()

    def test_something_with_no_recognisable_type(self, workspace_root):
        (workspace_root / "thing.wat").write_bytes(b"\x00\x01")
        # Refusing would be wrong — the caller asked for bytes — but so would guessing.
        assert ws.media_file("thing.wat")[1] == "application/octet-stream"

    def test_a_file_that_is_not_there(self):
        with pytest.raises(ws.WorkspaceError) as caught:
            ws.media_file("nope.png")
        assert "no nope.png" in str(caught.value)

    def test_a_directory_is_not_a_file_to_serve(self, workspace_root):
        (workspace_root / "shots").mkdir()
        with pytest.raises(ws.WorkspaceError):
            ws.media_file("shots")

    def test_something_too_big_says_what_to_do_instead(self, workspace_root, monkeypatch):
        monkeypatch.setattr(ws, "_MAX_MEDIA_BYTES", 100)
        (workspace_root / "huge.png").write_bytes(b"x" * 500)
        with pytest.raises(ws.WorkspaceError) as caught:
            ws.media_file("huge.png")
        # A limit with no way past it is a dead end, and "open it in another application"
        # is a real way past it.
        assert "another application" in str(caught.value)

    def test_the_limit_is_generous_enough_for_a_screenshot(self):
        # He takes full-page Playwright captures at 1440 and 390 wide. Several megabytes is
        # ordinary, and a limit tuned for text would refuse the exact files this is for.
        assert ws._MAX_MEDIA_BYTES >= 20_000_000

    def test_it_is_gated_like_every_other_read(self, workspace_root, tmp_path, monkeypatch):
        """Serving raw bytes over HTTP is how a file viewer becomes "read any file"."""
        from kith.infra.db import config_store
        from kith.services import permissions

        db = tmp_path / "config.db"
        config_store.init(db)
        monkeypatch.setattr("kith.config.CONFIG_DB_PATH", db, raising=False)
        permissions.revoke_all()

        outside = tmp_path.parent / "private.png"
        outside.write_bytes(PNG)
        with pytest.raises(Exception) as caught:
            ws.media_file(str(outside))
        assert "allow" in str(caught.value).lower() or "not allowed" in str(caught.value).lower()


class TestOpeningItWhereItLives:
    def test_the_path_is_the_real_file(self, workspace_root):
        (workspace_root / "shot.png").write_bytes(PNG)
        found = handoff.locate("shot.png")
        # Not a copy of it somewhere else. This is the fix for Errno 17 and for opening a
        # stale duplicate, and it is one assertion.
        assert found.host_path == workspace_root / "shot.png"

    def test_nothing_is_copied_anywhere(self, workspace_root):
        (workspace_root / "shot.png").write_bytes(PNG)
        before = sorted(p.name for p in workspace_root.iterdir())
        handoff.locate("shot.png")
        assert sorted(p.name for p in workspace_root.iterdir()) == before

    def test_there_is_no_handoff_folder_left_to_copy_into(self):
        # The name of the thing that broke. Its absence is the load-bearing part, and an
        # attribute check is the only way to notice it being reintroduced.
        assert not hasattr(handoff, "HANDOFF_DIR")
        assert not hasattr(handoff, "COMPANION_SUFFIXES")
        assert not hasattr(ws, "copy_out")

    def test_a_page_needs_no_widening_now(self, workspace_root):
        """The mechanism that existed to compensate for copying, and can now be deleted.

        Copying `index.html` alone left its stylesheet behind, so the page opened unstyled;
        the answer was to copy the whole parent folder. Opening in place makes the problem
        not exist — the stylesheet is already next to it — and one file is again one file.
        """
        (workspace_root / "site").mkdir()
        (workspace_root / "site" / "index.html").write_text("<link href=styles.css>")
        (workspace_root / "site" / "styles.css").write_text("body{}")

        found = handoff.locate("site/index.html")
        assert found.host_path.name == "index.html"
        assert (found.host_path.parent / "styles.css").exists()

    def test_a_missing_file_says_so_in_english(self):
        with pytest.raises(handoff.HandoffError) as caught:
            handoff.locate("nope.png")
        assert "no nope.png" in str(caught.value).lower()

    def test_a_script_is_revealed_rather_than_run(self, workspace_root):
        (workspace_root / "deploy.sh").write_text("#!/bin/sh\nrm -rf /\n")
        found = handoff.locate("deploy.sh")
        # Handing this to the OS would execute it. The one capability here worth being
        # careful with, and it survived the simplification.
        assert not found.openable
        assert "folder instead" in found.note

    def test_a_png_is_openable(self, workspace_root):
        (workspace_root / "shot.png").write_bytes(PNG)
        assert handoff.locate("shot.png").openable

    def test_opening_reveals_instead_of_running_an_executable(self, workspace_root, monkeypatch):
        launched: list[tuple] = []
        monkeypatch.setattr(handoff, "_launch", lambda target, *, reveal: launched.append((target, reveal)))
        (workspace_root / "deploy.sh").write_text("#!/bin/sh\n")

        handoff.open_workspace_file("deploy.sh")

        # Asked to open, and it revealed — decided on the server, where the file is, rather
        # than by a renderer that would have to ask first and could forget to.
        assert launched == [(workspace_root / "deploy.sh", True)]

    def test_opening_a_normal_file_opens_it(self, workspace_root, monkeypatch):
        launched: list[tuple] = []
        monkeypatch.setattr(handoff, "_launch", lambda target, *, reveal: launched.append((target, reveal)))
        (workspace_root / "book.xlsx").write_bytes(b"PK\x03\x04")

        handoff.open_workspace_file("book.xlsx")

        assert launched == [(workspace_root / "book.xlsx", False)]

    def test_reveal_is_honoured(self, workspace_root, monkeypatch):
        launched: list[tuple] = []
        monkeypatch.setattr(handoff, "_launch", lambda target, *, reveal: launched.append((target, reveal)))
        (workspace_root / "book.xlsx").write_bytes(b"PK\x03\x04")

        handoff.open_workspace_file("book.xlsx", reveal=True)

        assert launched == [(workspace_root / "book.xlsx", True)]


class TestWhatMayStillBeOpened:
    def test_the_gate_did_not_widen_when_the_copy_went_away(self):
        """The check that used to be "inside ~/Kith files" now has to hold on its own.

        Removing the copy removed the folder the old check was named after, and a gate that
        no longer applies is worse than one that never existed: the endpoint hands a path to
        the operating system.
        """
        with pytest.raises(handoff.HandoffError) as caught:
            handoff.open_with_default_app("/etc/passwd")
        assert "can be opened from here" in str(caught.value)

    def test_a_traversal_is_resolved_before_it_is_checked(self, workspace_root):
        with pytest.raises(handoff.HandoffError):
            handoff.open_with_default_app(workspace_root / ".." / ".." / "etc" / "passwd")

    def test_the_handoff_folder_is_no_longer_privileged(self):
        # ~/Kith files still exists on this machine, holding copies an older build made. It
        # is now an ordinary folder in someone's home directory, and it must not stay on the
        # list of places this endpoint will open something from.
        assert not any(root.name == "Kith files" for root in handoff._openable_roots())


class TestTheViewerAndTheServerAgree:
    """The interface decides what it can draw; the server decides what it will send.

    Same shape of drift as the Mind panel's tool table, and the same reason to test across
    the boundary: a file type added to one side and not the other fails at runtime, in front
    of whoever opened that file, as either a broken-image icon or a download prompt. Neither
    says which half is wrong.
    """

    def _viewer_extensions(self) -> set[str]:
        import re

        from kith import settings

        # `components/files/kinds.ts` — what the viewer decides a file *is*, from its name.
        # It was `components/file-view.tsx` until that 1,073-line module was split; the table
        # itself is unchanged.
        source = (
            settings.SERVER_ROOT.parent / "ui" / "src" / "components" / "files" / "kinds.ts"
        ).read_text()
        assert "const IMAGES" in source, "the IMAGES table has moved or been renamed; update this test"
        table = source.split("const IMAGES", 1)[1].split("\n};", 1)[0]
        return set(re.findall(r"^\s{2}(\w+):", table, re.M)) | {"pdf"}

    def test_everything_the_viewer_draws_has_a_content_type(self):
        import mimetypes

        unknown = sorted(ext for ext in self._viewer_extensions() if not mimetypes.guess_type(f"x.{ext}")[0])
        # Without a type the browser is handed application/octet-stream, which downloads the
        # file rather than showing it — the exact failure this work was meant to remove.
        assert not unknown, f"the viewer would render these but the server can't name them: {unknown}"

    def test_each_one_is_served_as_the_kind_of_thing_it_is(self):
        import mimetypes

        wrong = []
        for ext in sorted(self._viewer_extensions()):
            kind = mimetypes.guess_type(f"x.{ext}")[0] or ""
            expected = "application/pdf" if ext == "pdf" else "image/"
            if not kind.startswith(expected):
                wrong.append((ext, kind))
        assert not wrong, f"these would arrive as the wrong sort of thing: {wrong}"


class TestThePolicyThatLetsAPdfRender:
    """``object-src`` is the difference between a PDF and a blank rectangle.

    Verified rather than reasoned about: with ``object-src 'none'`` the ``<embed>`` renders
    as an empty box in Electron — no error, no console message, nothing to connect the blank
    space to a header. With ``object-src blob:`` the full reader appears, toolbar and page
    thumbnails included. So this is pinned, because "tighten the CSP" is a reasonable-looking
    change that would silently take the feature away.
    """

    def _policy(self) -> str:
        from kith.api import csp

        return "; ".join(csp._BASE)

    def test_a_blob_may_be_rendered_as_an_object(self):
        assert "object-src blob:" in self._policy()

    def test_and_nothing_else_may_be(self):
        # Not 'self', not a host. The only thing this permits is bytes the page already
        # fetched through the API — which it had to authenticate to do.
        import re

        directive = re.search(r"object-src ([^;]*)", self._policy()).group(1).strip()
        assert directive == "blob:", f"object-src widened beyond blob: — now {directive!r}"

    def test_remote_images_are_still_refused(self):
        # Unchanged by any of this, and worth keeping true: it is what stops markdown he
        # wrote from phoning home by referencing an image.
        assert "img-src 'self' data: blob:" in self._policy()


class TestAskingAboutASkillRatherThanAPath:
    """He can author a skill; the prompt has to say that is what he is doing.

    Skills live outside his workspace by design — a skill is a capability, not work product —
    so writing one lands in the generic out-of-folder branch. In ask-mode that produced "he
    wants to write to something outside his workspace" about a directory nobody recognises,
    for an action he has a dedicated tool and a dedicated skill for.

    The gate stays. A skill steers him, so installing one should be a decision rather than a
    default. What changes is that the decision is legible: you are approving a capability, and
    the sentence should name it.
    """

    def _ask(self, kind, target):
        from kith.infra import workspace as ws
        from kith.services import permissions

        before = permissions.mode()
        try:
            permissions.set_mode("ask")
            return permissions.check_path(kind, target, ws.root())
        finally:
            permissions.set_mode(str(before).split(".")[-1].lower())

    def test_writing_a_new_skill_names_it(self):
        from pathlib import Path

        from kith.services import skills

        decision = self._ask("write", Path(skills.root()) / "money-bugs" / "SKILL.md")
        assert not decision.allowed
        text = str(getattr(decision, "reason", "") or getattr(decision, "why", ""))
        assert "`money-bugs` skill" in text, text
        # And not the sentence that says nothing.
        assert "outside his workspace" not in text

    def test_removing_one_says_remove_not_write(self):
        from pathlib import Path

        from kith.services import skills

        decision = self._ask("delete", Path(skills.root()) / "ts-bug-scanner")
        text = str(getattr(decision, "reason", "") or getattr(decision, "why", ""))
        assert "remove" in text and "`ts-bug-scanner` skill" in text, text

    def test_an_ordinary_outside_file_is_unchanged(self):
        from pathlib import Path

        decision = self._ask("write", Path.home() / "Documents" / "notes.txt")
        text = str(getattr(decision, "reason", "") or getattr(decision, "why", ""))
        # The general case still reads as the general case; this did not widen.
        assert "outside his workspace" in text, text

    def test_reading_a_skill_is_not_gated_by_this(self):
        from pathlib import Path

        from kith.services import skills

        # read_skill has to keep working. Only authoring is a decision.
        decision = self._ask("read", Path(skills.root()) / "skill-creator" / "SKILL.md")
        text = str(getattr(decision, "reason", "") or getattr(decision, "why", ""))
        assert "skill — one of his own" not in text
