"""Clicking a filename he wrote in a sentence.

From a real conversation. He was asked which spreadsheet he had used and answered, correctly:

    1. First file: `Staff.xlsx`
       Path: /Users/saifullahsaeed/Kith/inbox/Staff.xlsx
    2. Second (final) file: `Staff-SAIF.xlsx`
       Path: /Users/saifullahsaeed/Kith/inbox/Staff-SAIF.xlsx

Both files existed, exactly where he said. Clicking the name opened the viewer on
`Staff-SAIF.xlsx` — anchored at the workspace root, because a bare name has nothing else to
be anchored to — and it said **there's no Staff-SAIF.xlsx** about a file two clicks away in
Finder. The full path beside it was not clickable at all, so the only thing you could click
was the only one that could not be found.

Nothing here was a bug in the ordinary sense. `resolve` did what it is for, the viewer
reported what it was told, and the interface had no way to know that the string was a *name*
rather than a *path*. So it is given one: reads look for the file, writes never do.

The asymmetry is the whole design. A `write_file("Staff.xlsx")` that quietly retargeted a
Staff.xlsx three folders away would be data loss wearing a convenience feature's clothes.
"""

from __future__ import annotations

import pytest

from kith.infra import permissions
from kith.infra import workspace as ws
from kith.infra.workspace import paths


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """His folder, with work in subfolders — and a linked project outside it."""
    home = tmp_path / "Kith"
    project = tmp_path / "ai-play"
    (home / "inbox").mkdir(parents=True)
    (home / "work").mkdir()
    (project / "src").mkdir(parents=True)
    (home / "inbox" / "Staff-SAIF.xlsx").write_bytes(b"PK\x03\x04")
    (home / "work" / "plan.md").write_text("the plan")
    (home / "top.md").write_text("at the root")
    (project / "src" / "main.py").write_text("print()")
    monkeypatch.setattr(paths, "configured_root", lambda: home)
    monkeypatch.setattr(permissions, "linked_project_roots", lambda: (project,))
    return home, project


class TestTheNameIsEnough:
    def test_a_bare_filename_finds_the_file_in_its_subfolder(self, workspace):
        """The failure in the screenshot, in one line."""
        home, _ = workspace
        assert ws.locate("Staff-SAIF.xlsx") == str(home / "inbox" / "Staff-SAIF.xlsx")

    def test_a_path_that_is_already_right_is_left_alone(self, workspace):
        """No search runs for a path that exists. This is the overwhelmingly common case and
        it must stay a single `exists()` — the walk is the exception, not the mechanism."""
        home, _ = workspace
        assert ws.locate("work/plan.md") == str(home / "work" / "plan.md")

    def test_an_absolute_path_he_printed_is_honoured(self, workspace):
        home, _ = workspace
        target = home / "inbox" / "Staff-SAIF.xlsx"
        assert ws.locate(str(target)) == str(target)

    def test_the_sandbox_home_still_means_his_folder(self, workspace):
        """`/home/kith` appears in his own memory rows and in old task files, so it cannot
        stop meaning anything — see `resolve`. Going through `locate` must not lose that."""
        home, _ = workspace
        assert ws.locate("/home/kith/top.md") == str(home / "top.md")

    def test_a_file_in_a_linked_project_is_found_too(self, workspace):
        """Permission already says a linked folder is his. Looking for a file in his folder
        and not in yours would be the same split that made `glob` fail 61% of the time."""
        _, project = workspace
        assert ws.locate("main.py") == str(project / "src" / "main.py")


class TestWhenItCannotBeSure:
    def test_two_files_of_the_same_name_are_named_rather_than_guessed_between(self, workspace):
        """Opening the wrong Staff.xlsx silently is worse than asking which one was meant."""
        home, _ = workspace
        (home / "inbox" / "report.md").write_text("a")
        (home / "work" / "report.md").write_text("b")
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("report.md")
        said = str(refused.value)
        assert "more than one" in said
        assert "inbox/report.md" in said and "work/report.md" in said

    def test_the_folders_he_wrote_break_the_tie(self, workspace):
        """`site/report.md` says more than `report.md` does, and both are things he writes.
        The path is still wrong — nothing is at `<root>/site/report.md` — but it is wrong in
        a way that carries information, so it is used rather than thrown away."""
        home, _ = workspace
        (home / "docs" / "site").mkdir(parents=True)
        (home / "docs" / "site" / "report.md").write_text("a")
        (home / "work" / "report.md").write_text("b")
        assert ws.locate("site/report.md") == str(home / "docs" / "site" / "report.md")

    def test_a_name_that_is_nowhere_says_so(self, workspace):
        with pytest.raises(ws.WorkspaceError, match=r"there's no nothing\.md"):
            ws.locate("nothing.md")

    def test_nothing_at_all(self, workspace):
        with pytest.raises(ws.WorkspaceError):
            ws.locate("   ")


class TestWhereItWillNotLook:
    def test_his_own_records_are_not_his_work(self, workspace):
        """A click means "show me my file". `.kith` holds transcripts, and a conversation
        named after a document is not that document."""
        home, _ = workspace
        internal = home / paths.INTERNAL_DIR / "conversations"
        internal.mkdir(parents=True)
        (internal / "hidden.md").write_text("a transcript")
        with pytest.raises(ws.WorkspaceError, match="there's no"):
            ws.locate("hidden.md")

    def test_dependencies_are_not_searched(self, workspace):
        """Otherwise one click walks a node_modules, and every `index.js` is ambiguous."""
        home, _ = workspace
        modules = home / "site" / "node_modules" / "left-pad"
        modules.mkdir(parents=True)
        (modules / "vendored.md").write_text("someone else's")
        with pytest.raises(ws.WorkspaceError, match="there's no"):
            ws.locate("vendored.md")


class TestTheDesignFoldersAreWork:
    """`.kith` stopped being only bookkeeping, and the search did not notice.

    From a screenshot, and a second report of the same thing. One session worked on two trials
    in a row — `trials/sprout`, then `trials/oregon` — and wrote a screen into each. Clicking
    `.kith/screens/home.html` afterwards produced *there's no
    /Users/…/trials/sprout/.kith/screens/home.html*, and the file was real: it was in `oregon`,
    written eighteen minutes earlier by the same conversation.

    Anchoring picked sprout because that is the project the session is bound to, which is the
    right guess and does not have to be the right answer — that is exactly the case the name
    search exists for. It found nothing, because `.kith` is pruned from the walk twice over: it
    is in `_LOOKUP_SKIP`, and the walk also drops every folder starting with a dot.

    That pruning was written when `.kith` held transcripts, tasks and memory, and for those it
    is still right — a conversation named after a document is not that document. `screens/` and
    `design/` arrived later and are the opposite kind of thing: they are the work, the same as
    a report in `work/` or a spreadsheet in `inbox/`, and they are the *only* place the design
    surface puts anything. Hiding his output in the folder his records happen to share.
    """

    def test_a_screen_in_another_project_is_found(self, workspace):
        """The screenshot. Bound to one trial, clicking a screen written in another."""
        home, _ = workspace
        oregon = home / "trials" / "oregon" / paths.INTERNAL_DIR / "screens"
        oregon.mkdir(parents=True)
        (oregon / "home.html").write_text("<p data-k=a>oregon</p>")
        assert ws.locate(".kith/screens/home.html") == str(oregon / "home.html")

    def test_the_design_system_is_work_too(self, workspace):
        home, _ = workspace
        design = home / "trials" / "oregon" / paths.INTERNAL_DIR / "design"
        design.mkdir(parents=True)
        (design / "conventions.md").write_text("# how screens are built here")
        assert ws.locate("conventions.md") == str(design / "conventions.md")

    def test_two_trials_with_the_same_screen_are_named_rather_than_guessed_between(self, workspace):
        """Every trial calls its first screen `home.html`, so this is the common case, not the
        exotic one — and naming both is the answer that lets somebody pick."""
        home, _ = workspace
        for trial in ("oregon", "ledgerline"):
            folder = home / "trials" / trial / paths.INTERNAL_DIR / "screens"
            folder.mkdir(parents=True)
            (folder / "home.html").write_text("<p data-k=a>x</p>")
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate(".kith/screens/home.html")
        said = str(refused.value)
        assert "more than one" in said
        assert "oregon" in said and "ledgerline" in said

    def test_the_records_beside_them_are_still_not_searched(self, workspace):
        """The rule this narrows rather than removes. Everything else under `.kith` is
        bookkeeping and stays invisible to a search by name."""
        home, _ = workspace
        internal = home / paths.INTERNAL_DIR
        (internal / "conversations").mkdir(parents=True)
        (internal / "conversations" / "hidden.md").write_text("a transcript")
        (internal / "scratch").mkdir()
        (internal / "scratch" / "pass1.png").write_bytes(b"\x89PNG")
        for name in ("hidden.md", "pass1.png"):
            with pytest.raises(ws.WorkspaceError, match="there's no"):
                ws.locate(name)

    def test_his_own_memory_is_not_a_document_of_yours(self, workspace):
        """`memory.md` sits directly in `.kith`. Entering the folder to reach `screens/` must
        not make the files at its top level findable by name."""
        home, _ = workspace
        internal = home / paths.INTERNAL_DIR
        internal.mkdir(exist_ok=True)
        (internal / "memory.md").write_text("what he knows")
        with pytest.raises(ws.WorkspaceError, match="there's no"):
            ws.locate("memory.md")


class TestTheFolderYouWerePointingAtWins:
    """Seven files of the same name, and one of them was obviously meant.

    From the second screenshot, which the widening above caused. The interface asked for
    `sadeef-v2/.kith/design/system.json` while bound to the project whose folder *is*
    `sadeef-v2`, so `routes/workspace._anchor` joined the folder on again and produced
    `…/Desktop/sadeef-v2/sadeef-v2/.kith/design/system.json`. Nothing is there, so the name
    search ran — and every project has exactly one `.kith/design/system.json`, so it matched
    seven of them and gave up.

    The tie-break that exists could not help: it compares against the parts of the *requested*
    path, which by then was a machine-built absolute one matching nothing. But the request
    still says where it was looking. Walk up until a folder that actually exists, and if some
    of the matches are inside it, those are the ones meant — here that folder is the project
    and exactly one match is inside it.

    Deliberately silent when it would change nothing: if none of the matches are under that
    folder, or all of them are, the answer is the same as before and a rule that fires anyway
    would only be a rule that can be wrong later.
    """

    def test_the_project_folder_joined_on_twice_still_finds_the_file(self, workspace):
        """The screenshot, in one assertion."""
        home, project = workspace
        real = project / ".kith" / "design"
        real.mkdir(parents=True)
        (real / "system.json").write_text("{}")
        for other in ("one", "two"):
            elsewhere = home / "trials" / other / ".kith" / "design"
            elsewhere.mkdir(parents=True)
            (elsewhere / "system.json").write_text("{}")
        asked = project / project.name / ".kith" / "design" / "system.json"
        assert ws.locate(str(asked)) == str(real / "system.json")

    def test_it_stays_out_of_the_way_when_the_folder_holds_none_of_them(self, workspace):
        """The first screenshot: bound to sprout, whose `screens/` is real and simply does not
        have that file. Preferring "under sprout" would narrow to nothing and lose the answer."""
        home, _ = workspace
        sprout = home / "trials" / "sprout" / ".kith" / "screens"
        sprout.mkdir(parents=True)
        (sprout / "today.html").write_text("<p data-k=a>today</p>")
        oregon = home / "trials" / "oregon" / ".kith" / "screens"
        oregon.mkdir(parents=True)
        (oregon / "home.html").write_text("<p data-k=a>oregon</p>")
        asked = sprout / "home.html"
        assert ws.locate(str(asked)) == str(oregon / "home.html")


class TestSayingWhichOne:
    """The list has to be a list of different things.

    Also from the second screenshot, and the worse half of it. `display` names a path relative
    to the innermost root containing it, which is right for one path and destroys a list of
    them: four separate design systems, in four separate projects, all rendered
    `.kith/design/system.json`. The sentence ended "say which one" while making that
    impossible to do.
    """

    @pytest.fixture
    def two_design_systems(self, workspace, tmp_path, monkeypatch):
        """Two linked projects, each with the one design system every project has.

        Both are roots in their own right, which is what makes the names collide: `display`
        measures from the innermost root *containing* the path, so each is `.kith/design/
        system.json` and neither says which project it belongs to.
        """
        made = []
        for name in ("alpha", "beta"):
            folder = tmp_path / name / ".kith" / "design"
            folder.mkdir(parents=True)
            (folder / "system.json").write_text("{}")
            made.append(folder / "system.json")
        monkeypatch.setattr(
            permissions, "linked_project_roots", lambda: (tmp_path / "alpha", tmp_path / "beta")
        )
        return made

    def test_the_names_shown_are_all_different(self, two_design_systems):
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("system.json")
        said = str(refused.value)
        shown = said.split(": ", 1)[1].split(" — ")[0]
        names = [part.strip() for part in shown.split(", ")]
        assert len(names) == len(set(names)), said
        assert any("alpha" in n for n in names) and any("beta" in n for n in names), said

    def test_the_matches_come_back_on_the_error_itself(self, two_design_systems):
        """So the interface can offer them rather than ask somebody to re-type a path they
        never typed in the first place — it came out of his prose."""
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("system.json")
        candidates = refused.value.candidates
        assert {c["path"] for c in candidates} == {str(item) for item in two_design_systems}
        assert all(c["name"] for c in candidates)
        assert len({c["name"] for c in candidates}) == len(candidates)

    def test_an_ordinary_refusal_carries_none(self, workspace):
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("nothing.md")
        assert refused.value.candidates == []


class TestWritesAreUntouched:
    def test_resolve_still_anchors_rather_than_searches(self, workspace):
        """The guard on the whole idea. `resolve` is what a write goes through, and it must
        keep answering "under the working base" for a name that exists elsewhere — otherwise
        writing `Staff-SAIF.xlsx` overwrites the one in `inbox/` instead of making a new one."""
        home, _ = workspace
        assert ws.resolve("Staff-SAIF.xlsx") == str(home / "Staff-SAIF.xlsx")
