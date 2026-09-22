"""The dead end at the end of a click.

From a screenshot. A page was opened on ``.kith/screens/home.html`` and the viewer showed one
line of red text on an otherwise empty window:

    there's no /Users/saifullahsaeed/Kith/trials/sprout/.kith/screens/home.html

Two separate failures of manners in one sentence, and neither of them is that the file was
missing — the file really was missing, and saying so is the right answer.

The first is *the name*. Nobody clicked that string. What was clicked was
``.kith/screens/home.html``; the absolute path is an internal step — :func:`_anchor` joining a
relative path onto its project's folder — and printing it hands somebody the working notes of
the lookup instead of an answer about their file. The function two lines below this one already
knows better: the ambiguous branch formats with :func:`display`, and this one did not.

The second is that it *stops there*. The folder existed and had exactly one screen in it. The
lookup had already walked to the file's parent to find out the file was absent, so the useful
half of the answer — ``screens/`` is right there and it has ``today.html`` in it — was a
``listdir`` away and went unasked.

Only when folders were written. A bare name goes through the name search above, and that
search failing means nothing of that name exists anywhere, so there is no folder to describe
and nothing to add.
"""

from __future__ import annotations

import pytest

from kith.infra import permissions
from kith.infra import workspace as ws
from kith.infra.workspace import paths


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """His folder, and a linked project with the design folder from the screenshot."""
    home = tmp_path / "Kith"
    project = tmp_path / "sprout"
    (home / "inbox").mkdir(parents=True)
    (home / "inbox" / "notes.md").write_text("something")
    screens = project / ".kith" / "screens"
    screens.mkdir(parents=True)
    (screens / "today.html").write_text("<p data-k=a>today</p>")
    monkeypatch.setattr(paths, "configured_root", lambda: home)
    monkeypatch.setattr(permissions, "linked_project_roots", lambda: (project,))
    return home, project


class TestItIsNamedTheWayItWasClicked:
    def test_the_absolute_path_does_not_reach_the_message(self, workspace):
        """The screenshot, in one assertion."""
        _, project = workspace
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate(str(project / ".kith" / "screens" / "home.html"))
        said = str(refused.value)
        assert str(project) not in said
        assert ".kith/screens/home.html" in said

    def test_a_relative_path_is_left_as_it_reads(self, workspace):
        home, _ = workspace
        (home / "reports").mkdir()
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("reports/q3.md")
        assert "reports/q3.md" in str(refused.value)


class TestItSaysWhatIsThere:
    def test_the_folder_that_does_exist_lists_what_it_holds(self, workspace):
        _, project = workspace
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate(str(project / ".kith" / "screens" / "home.html"))
        assert "today.html" in str(refused.value)

    def test_a_long_folder_is_cut_rather_than_poured_out(self, workspace):
        home, _ = workspace
        (home / "many").mkdir()
        for i in range(9):
            (home / "many" / f"file{i}.md").write_text("x")
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("many/missing.md")
        said = str(refused.value)
        assert "more" in said
        assert said.count(".md") <= 6

    def test_a_folder_that_is_also_missing_just_says_the_one_thing(self, workspace):
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("nowhere/at/all.md")
        said = str(refused.value)
        assert "there's no nowhere/at/all.md" in said
        assert "has" not in said

    def test_an_empty_folder_is_not_described_as_holding_nothing(self, workspace):
        home, _ = workspace
        (home / "empty").mkdir()
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("empty/gone.md")
        assert str(refused.value) == "there's no empty/gone.md"


class TestTheBareNameIsUnchanged:
    def test_a_name_with_no_folder_in_it_still_says_only_that(self, workspace):
        """It went through the name search, which looked everywhere. There is no folder it
        was expected in, so there is nothing to describe."""
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("nothing.md")
        assert str(refused.value) == "there's no nothing.md"

    def test_finding_it_is_still_the_normal_answer(self, workspace):
        home, _ = workspace
        assert ws.locate("notes.md") == str(home / "inbox" / "notes.md")
