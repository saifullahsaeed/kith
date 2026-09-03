"""Every capability a merge absorbed, exercised through the tool that absorbed it.

`test_a_retired_tool_still_works.py` covers the other half — that the old *name* still gets
you there. This is the half that actually matters: that the surviving tool grew the capability
rather than a parameter it ignores. A merge that drops something is not a smaller toolset, it
is a smaller Kith, and the failure mode is quiet: the argument is accepted, something
plausible comes back, and nobody finds out until the answer was needed.

So each test below is the *old tool's* job, asked of the new one. Where the old behaviour had
an edge worth keeping — `glob('**/*.py')` meaning "everything underneath", an explicit `limit`
beating a default — the edge is asserted too, because those are what a merge quietly rounds off.
"""

from __future__ import annotations

import pytest

from kith.infra import workspace as ws
from kith.tools import registry


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


def run(tool: str, db, /, **args):
    """Positional-only, because half these tools take an argument called `name`."""
    return registry.require(tool).run(db, args)


class TestGlobStillGlobs:
    """`list_files` became "glob with no pattern", which is only safe if patterns still work."""

    @pytest.fixture(autouse=True)
    def tree(self, workspace_root):
        (workspace_root / "top.py").write_text("x = 1\n")
        (workspace_root / "deep").mkdir()
        (workspace_root / "deep" / "under.py").write_text("y = 2\n")
        (workspace_root / "notes.md").write_text("hi\n")
        return workspace_root

    def test_no_pattern_lists_the_folder(self, db):
        listed = str(run("glob", db))
        assert "top.py" in listed
        assert "deep" in listed, "a listing shows folders; a glob of files would not"

    def test_a_recursive_pattern_still_recurses(self, db):
        """The one I nearly broke. Treating `**` as "just list it" would have answered a
        request for everything underneath with the top level only — silently, and with a
        listing that looks like a real answer."""
        found = str(run("glob", db, pattern="**/*.py"))
        assert "under.py" in found, "`**` must still reach into subfolders"
        assert "top.py" in found

    def test_a_narrow_pattern_still_narrows(self, db):
        found = str(run("glob", db, pattern="**/*.md"))
        assert "notes.md" in found
        assert "top.py" not in found


class TestRepoMapStillOutlines:
    """`outline`'s job: the shape of named files, not a ranked map of a tree."""

    def test_named_files_come_back_as_shapes(self, db, workspace_root):
        (workspace_root / "m.py").write_text("class Thing:\n    def method(self):\n        pass\n")
        out = run("repo_map", db, paths=["m.py"])
        assert out["definitions"] == 2, out
        assert "class Thing" in out["outline"]

    def test_several_files_are_one_call(self, db, workspace_root):
        (workspace_root / "a.py").write_text("def alpha():\n    pass\n")
        (workspace_root / "b.py").write_text("def beta():\n    pass\n")
        out = run("repo_map", db, paths=["a.py", "b.py"])
        assert "a.py" in out["outline"] and "b.py" in out["outline"]

    def test_and_mapping_a_folder_still_maps(self, db, workspace_root):
        (workspace_root / "a.py").write_text("def alpha():\n    pass\n")
        out = run("repo_map", db)
        assert "map" in out, out
        assert "alpha" in out["map"]


class TestChangesStillReadsHistory:
    """`history`'s job. A commits window is a different question from an uncommitted diff, and
    the tool has to answer both without one shadowing the other."""

    @pytest.fixture
    def repo_with_a_commit(self, db, workspace_root):
        ws.ensure_repo()
        (workspace_root / "one.txt").write_text("first\n")
        run("commit", db, message="the first point")
        return workspace_root

    def test_the_recorded_history_comes_back(self, db, repo_with_a_commit):
        out = str(run("changes", db, commits=5))
        assert "the first point" in out

    def test_and_the_uncommitted_diff_is_still_the_default(self, db, repo_with_a_commit):
        (repo_with_a_commit / "one.txt").write_text("first\nsecond\n")
        out = str(run("changes", db))
        assert "second" in out
        assert "the first point" not in out, "the default must not have become the log"

    def test_an_explicit_limit_beats_the_default(self, db, repo_with_a_commit):
        """`history(limit=1)` translates to `changes(commits=1)`, and the translation supplies
        20 when nothing was said — so an explicit 1 has to win over it."""
        (repo_with_a_commit / "two.txt").write_text("second\n")
        run("commit", db, message="the second point")
        out = str(run("changes", db, commits=1))
        assert "the second point" in out
        assert "the first point" not in out


class TestPublishStillChecksTheRemote:
    """`check_remote`'s job: ask the remote what it has, without moving anything here. Its
    whole point was working on a dirty tree, which `pull` refuses outright."""

    def test_checking_does_not_touch_the_working_tree(self, db, workspace_root):
        ws.ensure_repo()
        (workspace_root / "wip.txt").write_text("half-finished\n")

        out = run("publish", db, direction="check")

        assert "remote" in out, out
        assert (workspace_root / "wip.txt").read_text() == "half-finished\n"

    def test_it_is_reached_without_pulling(self, db, workspace_root, monkeypatch):
        """The distinction the merge had to preserve: 'check' fetches and must never merge."""
        ws.ensure_repo()
        pulled = []
        monkeypatch.setattr(ws, "pull", lambda *_a: pulled.append(1) or "pulled")
        monkeypatch.setattr(ws, "fetch", lambda *_a, **_k: "fetched")

        assert run("publish", db, direction="check") == {"remote": "fetched"}
        assert not pulled, "'check' must not pull"


class TestScheduleDoesBothKinds:
    """Three tools of the six were there to make the model classify its own intent first."""

    def test_a_time_makes_a_one_off(self, db):
        out = run("schedule", db, note="look at the log", in_minutes=30)
        assert out["kind"] == "once", out

    def test_a_cadence_makes_a_standing_job(self, db):
        out = run("schedule", db, note="daily briefing", daily_at="09:00")
        assert out["kind"] == "repeating", out

    def test_one_listing_shows_both(self, db):
        run("schedule", db, note="one-off thing", in_minutes=30)
        run("schedule", db, note="every-day thing", daily_at="09:00")

        rows = run("list_schedules", db)
        notes = str(rows)
        assert "one-off thing" in notes and "every-day thing" in notes
        kinds = {row["kind"] for row in rows["items"]} if "items" in rows else set()
        assert kinds == {"once", "repeating"} or ("once" in notes and "repeating" in notes)

    def test_cancelling_a_reminder_needs_no_kind(self, db):
        made = run("schedule", db, note="one-off thing", in_minutes=30)
        out = run("cancel_schedule", db, id=int(made["id"]))
        assert out["cancelled"], out
        assert out["kind"] == "once"

    def test_cancelling_a_standing_job_needs_no_kind_either(self, db):
        made = run("schedule", db, note="daily briefing", daily_at="09:00")
        out = run("cancel_schedule", db, id=int(made["id"]))
        assert out["cancelled"], out
        assert out["kind"] == "repeating"

    def test_an_id_that_is_both_asks_which(self, db):
        """Ids are per-table, so this is reachable rather than theoretical — and guessing
        would sometimes cancel the daily briefing instead of a reminder set an hour ago."""
        once = run("schedule", db, note="one-off thing", in_minutes=30)
        twice = run("schedule", db, note="daily briefing", daily_at="09:00")
        if int(once["id"]) != int(twice["id"]):
            pytest.skip("the two tables did not collide on an id here")

        out = run("cancel_schedule", db, id=int(once["id"]))

        assert out["cancelled"] is False
        assert "kind" in out["error"]

    def test_and_kind_settles_it(self, db):
        once = run("schedule", db, note="one-off thing", in_minutes=30)
        out = run("cancel_schedule", db, id=int(once["id"]), kind="once")
        assert out["cancelled"], out


class TestUpdateMilestoneDoesTheRoadmap:
    """`order_milestones` and `unlink_milestones` were a schema each for one graph edge."""

    @pytest.fixture
    def roadmap(self, db):
        project = run("create_project", db, name="The App", description="an app")
        first = run("add_milestone", db, project_id=project["id"], title="foundations")
        second = run("add_milestone", db, project_id=project["id"], title="the feature")
        return {"project": project, "first": int(first["id"]), "second": int(second["id"])}

    def test_ids_put_them_in_order(self, db, roadmap):
        out = run("update_milestone", db, ids=[roadmap["first"], roadmap["second"]])

        assert out["ordered"] == [roadmap["first"], roadmap["second"]], out
        second = next(one for one in out["roadmap"]["milestones"] if one["id"] == roadmap["second"])
        assert second["waits_for"] == [roadmap["first"]], second
        assert second["ready"] is False, "work under it must not be offered yet"

    def test_and_the_order_can_be_undone(self, db, roadmap):
        run("update_milestone", db, ids=[roadmap["first"], roadmap["second"]])
        run("update_milestone", db, id=roadmap["second"], no_longer_waits_for=roadmap["first"])

        after = run("list_projects", db)
        second = next(
            one
            for project in after["items"]
            for one in project.get("milestones", [])
            if one["id"] == roadmap["second"]
        )
        assert not second.get("blocked_by"), second

    def test_an_ordinary_update_still_works(self, db, roadmap):
        out = run("update_milestone", db, id=roadmap["first"], status="done")
        assert out["status"] == "done", out

    def test_neither_argument_says_so_rather_than_guessing(self, db):
        out = run("update_milestone", db, status="done")
        assert "error" in out


class TestUpdateProjectLinksAFolder:
    """`link_folder`'s job — one column, and the grant that comes with it."""

    def test_a_folder_is_linked_and_seeded(self, db, workspace_root, tmp_path):
        project = run("create_project", db, name="The App", description="an app")
        codebase = tmp_path / "elsewhere" / "the-app"
        codebase.mkdir(parents=True)

        out = run("update_project", db, id=project["id"], directory=str(codebase))

        assert out["directory"] == str(codebase), out
        assert (codebase / ".kith" / "memory.md").exists(), "the project memory is the point"

    def test_an_empty_string_unlinks(self, db, workspace_root, tmp_path):
        project = run("create_project", db, name="The App", description="an app")
        codebase = tmp_path / "elsewhere" / "the-app"
        codebase.mkdir(parents=True)
        run("update_project", db, id=project["id"], directory=str(codebase))

        out = run("update_project", db, id=project["id"], directory="")

        assert not out.get("directory"), out
        assert "no longer yours" in out.get("note", "")

    def test_an_ordinary_update_still_works(self, db):
        project = run("create_project", db, name="The App", description="an app")
        out = run("update_project", db, id=project["id"], name="The Other App")
        assert out["name"] == "The Other App", out


class TestFindSymbolAndCheckCodeWithoutAServer:
    """The pair that used to be hidden entirely when nothing was installed. Whatever else
    changed, the question must now always have an answer.

    `nothing_installed` is not decoration. Without it these tests found the pyright in this
    repository's own venv, started it, and answered from the language server — so the class
    asserting the no-server path was exercising the served one, and left the server running
    into the next file. `test_the_suite_does_not_start_loops` caught it.
    """

    @pytest.fixture(autouse=True)
    def nothing_installed(self, monkeypatch):
        import sys

        from kith.engine.code.lsp.manager import Manager

        one = Manager()
        monkeypatch.setattr(one, "find_binary", lambda *_a, **_k: None)
        for name, module in list(sys.modules.items()):
            if name.startswith("kith.") and isinstance(getattr(module, "manager", None), Manager):
                monkeypatch.setattr(module, "manager", one, raising=False)
        yield one
        one.shutdown()

    def test_find_symbol_answers_from_the_parser(self, db, workspace_root):
        (workspace_root / "m.py").write_text("def target():\n    pass\n\n\ntarget()\n")
        out = run("find_symbol", db, name="target")
        assert out["engine"] == "parser", out
        assert len(out["definitions"]) == 1
        assert len(out["references"]) == 1

    def test_both_engines_answer_under_the_same_keys(self, db, workspace_root):
        """The merge's real risk: one tool, two shapes. `definitions` holding a count from one
        engine and a list from the other would be unusable to anything reading the result."""
        (workspace_root / "m.py").write_text("def target():\n    pass\n")
        out = run("find_symbol", db, name="target")
        assert isinstance(out["definitions"], list)
        assert isinstance(out["references"], list)

    def test_check_code_on_a_file_still_answers(self, db, workspace_root):
        (workspace_root / "m.py").write_text("def f():\n    pass\n")
        out = run("check_code", db, path="m.py")
        assert "unavailable" not in str(out), out
