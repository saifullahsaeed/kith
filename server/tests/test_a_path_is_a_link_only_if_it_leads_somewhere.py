"""Deciding whether a path he wrote is something you can click, before offering the click.

Two screenshots, one complaint. A page opened on a path that was not there and showed
*there's no /Users/…/trials/sprout/.kith/screens/home.html*; then a design system opened on a
name seven files answered to. Both were made prettier — a panel instead of red text, a list
instead of a sentence — and both were still a click that went nowhere.

The link should not have been a link. `linkTarget` routes every path-shaped href into the
viewer on purpose, because the alternative was the browser resolving it against this origin and
opening a second copy of the app, and it accepted the dead ends as the lesser of two bad
answers. This is the third answer: ask first.

`openable` is deliberately not "the file exists". A name matching several files resolves to a
*choice*, which the viewer can offer, so it stays a link. Only a path that leads to nothing at
all stops being one.
"""

from __future__ import annotations

import pytest

from kith.api.routes.workspace import resolution
from kith.infra import permissions
from kith.infra.workspace import paths


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    home = tmp_path / "Kith"
    (home / "inbox").mkdir(parents=True)
    (home / "inbox" / "Staff.xlsx").write_bytes(b"PK\x03\x04")
    for trial in ("sprout", "oregon"):
        design = home / "trials" / trial / paths.INTERNAL_DIR / "design"
        design.mkdir(parents=True)
        (design / "system.json").write_text("{}")
    monkeypatch.setattr(paths, "configured_root", lambda: home)
    monkeypatch.setattr(permissions, "linked_project_roots", lambda: ())
    return home


class TestWhatCountsAsALink:
    def test_a_file_that_is_there(self, workspace):
        answer = resolution("inbox/Staff.xlsx", None)
        assert answer["openable"] is True
        assert answer["path"] == str(workspace / "inbox" / "Staff.xlsx")

    def test_a_bare_name_the_search_can_still_place(self, workspace):
        """The whole reason this cannot be a plain `exists()` on the client's guess."""
        answer = resolution("Staff.xlsx", None)
        assert answer["openable"] is True

    def test_a_name_several_files_answer_to_is_still_a_link(self, workspace):
        """It resolves to a choice, and the viewer can offer the choice."""
        answer = resolution("system.json", None)
        assert answer["openable"] is True
        assert answer["path"] is None
        candidates = answer["candidates"]
        assert isinstance(candidates, list)
        assert len(candidates) == 2

    def test_a_path_that_leads_nowhere_is_not(self, workspace):
        """The screenshot. This is the one that must stop being offered."""
        answer = resolution(".kith/screens/home.html", None)
        assert answer["openable"] is False
        assert answer["path"] is None

    def test_a_shape_that_was_never_a_file(self, workspace):
        answer = resolution("object.method", None)
        assert answer["openable"] is False
