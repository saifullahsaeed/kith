"""Every way a merged tool was found to answer a *different* question than it was asked.

`test_the_merged_tools_do_both_jobs.py` asks whether the absorbing tool can still do the job.
A review of the whole change asked a harder question — whether it does the job *right* — and
found nine places where it did not, all of the same species: the call succeeds, something
plausible comes back, and nothing in the result says the question was changed on the way
through. `aliases.py`'s own docstring names this the thing not to do: "a translation that
quietly answers a different question is worse than 'no such tool' — it looks like it worked."

Every test below is one of those nine, and every one of them shipped.
"""

from __future__ import annotations

import json

import pytest

from kith.infra import workspace as ws
from kith.services import conversations, touched
from kith.tools import registry


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


def run(tool: str, db, /, **args):
    return registry.require(tool).run(db, args)


class TestCheckCodeNeverClaimsCleanWithoutChecking:
    """`clean: True` is the field a model acts on. "I did not check" must not use it."""

    @pytest.fixture(autouse=True)
    def nothing_installed(self, monkeypatch):
        """No language server, which is the branch the bug was in. With one, `check_code` on a
        file answers from the server and never reaches the project checker at all — so testing
        this on a machine that happens to have pyright would test the wrong path."""
        import sys

        from kith.engine.code.lsp.manager import Manager

        one = Manager()
        monkeypatch.setattr(one, "find_binary", lambda *_a, **_k: None)
        for name, module in list(sys.modules.items()):
            if name.startswith("kith.") and isinstance(getattr(module, "manager", None), Manager):
                monkeypatch.setattr(module, "manager", one, raising=False)
        yield one
        one.shutdown()

    def test_a_file_finds_the_project_above_it(self, db, workspace_root):
        """It looked in the file's own directory and nowhere else, so a file two levels below
        `pyproject.toml` came back `clean: True` having run no checker at all."""
        (workspace_root / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
        (workspace_root / "pkg").mkdir()
        (workspace_root / "pkg" / "mod.py").write_text("import os\nprint(undefined_name)\n")

        out = run("check_code", db, path="pkg/mod.py")

        assert out.get("ran"), f"nothing was run: {out}"
        assert out.get("clean") is not True, "a file with two real errors is not clean"

    def test_no_checker_at_all_says_so_rather_than_clean(self, db, workspace_root):
        (workspace_root / "notes.txt").write_text("nothing to check here\n")

        out = run("check_code", db, path=".")

        assert out.get("clean") is not True, out
        assert out.get("checked") is False
        assert "not a clean result" in out.get("note", "")


class TestFindSymbolDoesNotNarrowSilently:
    """A false "nothing uses this" is the conclusion that precedes deleting shared code."""

    @pytest.fixture(autouse=True)
    def fresh_manager(self, monkeypatch):
        """A manager of its own, shut down afterwards.

        `find_symbol` starts a language server when one is installed, and this repository's own
        venv has pyright — so without this the server outlived the file and
        `test_the_suite_does_not_start_loops` failed two files later, naming a thread rather
        than the test that leaked it.
        """
        import sys

        from kith.engine.code.lsp.manager import Manager

        one = Manager()
        for name, module in list(sys.modules.items()):
            if name.startswith("kith.") and isinstance(getattr(module, "manager", None), Manager):
                monkeypatch.setattr(module, "manager", one, raising=False)
        yield one
        one.shutdown()

    @pytest.fixture(autouse=True)
    def project(self, workspace_root):
        (workspace_root / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
        (workspace_root / "pkg").mkdir()
        (workspace_root / "pkg" / "target.py").write_text("def target():\n    pass\n")
        (workspace_root / "other").mkdir()
        (workspace_root / "other" / "caller.py").write_text("from pkg.target import target\n\ntarget()\n")
        return workspace_root

    def test_naming_the_defining_file_still_finds_callers_elsewhere(self, db):
        """`path` as a file meant "search that file's folder", so a caller one directory over
        came back as no callers — under a plain `engine: "parser"` with nothing saying the
        scope had been cut to one directory."""
        out = run("find_symbol", db, name="target", path="pkg/target.py")

        assert out["definitions"], out
        assert out["references"], "the caller in other/ was not found"

    def test_and_the_parser_says_what_it_searched(self, db, monkeypatch):
        """Only the parser needs to: a language server answers across the project it has
        loaded, so there is no narrowed scope to disclose. The parser's scope is a decision
        this code makes, and an answer that hides it cannot be read."""
        import sys

        from kith.engine.code.lsp.manager import Manager

        one = Manager()
        monkeypatch.setattr(one, "find_binary", lambda *_a, **_k: None)
        for name, module in list(sys.modules.items()):
            if name.startswith("kith.") and isinstance(getattr(module, "manager", None), Manager):
                monkeypatch.setattr(module, "manager", one, raising=False)

        out = run("find_symbol", db, name="target", path="pkg/target.py")

        assert out["engine"] == "parser", out
        assert "searched" in out, "the scope has to be in the answer to read the answer"
        one.shutdown()

    def test_a_folder_search_is_unchanged(self, db):
        out = run("find_symbol", db, name="target")

        assert len(out["definitions"]) == 1
        assert len(out["references"]) == 1


class TestChangesKeepsItsPath:
    def test_a_scoped_history_is_scoped(self, db, workspace_root):
        """`commits` threw `path` away, so the history of one folder came back as the history
        of everything — read as that folder's, with nothing to reveal the swap."""
        ws.ensure_repo()
        (workspace_root / "inside").mkdir()
        (workspace_root / "inside" / "a.txt").write_text("a\n")
        run("commit", db, message="touched inside")
        (workspace_root / "outside.txt").write_text("b\n")
        run("commit", db, message="touched outside")

        scoped = str(run("changes", db, path="inside", commits=10))

        assert "touched inside" in scoped
        assert "touched outside" not in scoped, "the path was dropped"

    def test_a_nonsense_count_falls_back_rather_than_raising(self, db, workspace_root):
        ws.ensure_repo()
        (workspace_root / "a.txt").write_text("a\n")
        run("commit", db, message="one")

        out = run("changes", db, commits="twenty")

        assert "one" in str(out), out


class TestRetiredCallsThatQuietlyDidNothing:
    def test_unlinking_a_folder_through_the_old_name_unlinks_it(self, db, workspace_root, tmp_path):
        """`link_folder` with no folder meant "unlink". Translated, it produced no `directory`
        key at all — and `update_project` gates on the key being present, so the call reported
        success with the folder still linked and its write grant still live."""
        project = run("create_project", db, name="The App", description="an app")
        codebase = tmp_path / "elsewhere" / "the-app"
        codebase.mkdir(parents=True)
        run("update_project", db, id=project["id"], directory=str(codebase))

        from kith import tools

        result = tools.run_tool("link_folder", {"id": int(project["id"])}, db)

        assert result["ok"] is True, result
        assert not result["result"].get("directory"), "the folder is still linked"

    def test_the_old_singular_outline_argument_is_translated(self, db, workspace_root):
        """`outline` took `paths`; it also accepted a bare `path`, and plenty of calls used it.
        Without the rename that spelling fell into `repo_map`'s *folder* branch and tried to
        map a source file as if it were a directory."""
        (workspace_root / "m.py").write_text("def f():\n    pass\n")

        from kith import tools

        result = tools.run_tool("outline", {"path": "m.py"}, db)

        assert result["ok"] is True, result
        assert "f" in str(result["result"]), result


class TestUpdateProjectKeepsWhatItWasHanded:
    @pytest.fixture
    def linked(self, db, tmp_path):
        project = run("create_project", db, name="The App", description="an app")
        codebase = tmp_path / "elsewhere" / "the-app"
        codebase.mkdir(parents=True)
        return {"id": int(project["id"]), "dir": str(codebase)}

    def test_a_directory_does_not_swallow_the_other_fields(self, db, linked):
        """It returned from `_link_folder` the moment `directory` was present, dropping
        `name`, `description` and `status` — all advertised on the same schema with only `id`
        required and no hint that they were exclusive."""
        run(
            "update_project",
            db,
            id=linked["id"],
            directory=linked["dir"],
            name="Renamed",
            description="new desc",
        )

        from kith.infra.db import repositories as repo

        row = repo.projects.get_project(db, linked["id"])
        assert row["name"] == "Renamed", row
        assert row["description"] == "new desc", row
        assert row["directory"] == linked["dir"], row

    def test_the_human_only_completion_backstop_still_fires(self, db, linked):
        """`status='done'` alongside a directory skipped the guard entirely and returned ok."""
        out = run("update_project", db, id=linked["id"], directory=linked["dir"], status="done")

        assert "error" in out, out
        assert "person" in out["error"]


class TestBrowsePageReachesTheBrowser:
    def test_a_failed_download_is_a_reason_to_render(self, db, monkeypatch):
        """The fetch was outside any try, so a curl timeout failed the whole call and the
        headless browser — the entire reason the tool exists — was never started."""
        monkeypatch.setattr(
            ws, "fetch_url", lambda _url: (_ for _ in ()).throw(ws.WorkspaceError("fetch failed"))
        )
        monkeypatch.setattr(ws, "browse_page", lambda _url: "the rendered page, at length" * 40)

        out = run("browse_page", db, url="https://example.com")

        assert out["how"] == "rendered in a browser", out
        assert "fetch failed" in out.get("note", "")

    def test_both_failing_says_both(self, db, monkeypatch):
        monkeypatch.setattr(ws, "fetch_url", lambda _url: (_ for _ in ()).throw(ws.WorkspaceError("no curl")))
        monkeypatch.setattr(
            ws, "browse_page", lambda _url: (_ for _ in ()).throw(ws.WorkspaceError("no browser"))
        )

        out = run("browse_page", db, url="https://example.com")

        assert "no curl" in out["error"] and "no browser" in out["error"]


class TestTheManifestSeesARetiredEdit:
    def test_an_edit_through_the_old_name_is_recorded(self, db, workspace_root):
        """`RETIRED` translates the *name* and leaves the arguments alone, so the hot edit path
        arrived as `edit_files({path, old, new})` — and `_paths` only understood `edits`. The
        file he had just written never entered the manifest, and a file previously *read* kept
        only its read row, so the next look computed `stale` and told him it had changed under
        him because of his own edit."""
        (workspace_root / "m.py").write_text("x = 1\n")

        from kith import tools
        from kith.kernel import session_context

        # `record` files a touch against the conversation the turn is in and stays silent when
        # there is none — a tick, a script, a test. So the test has to be in one.
        with session_context.working_in("c-1"):
            tools.run_tool("edit_file", {"path": "m.py", "old": "x = 1", "new": "x = 2"}, db)

        manifest = touched.manifest(db, "c-1")
        assert "m.py" in manifest, f"the edit never entered the manifest: {manifest!r}"


class TestListSchedulesCannotHideAKind:
    def test_a_page_is_not_all_reminders(self, db):
        """The two lists were concatenated and then paged, so twenty pending reminders filled
        the first page and every standing job was off the end — the "asked once and answered
        half" failure the merge exists to prevent, reproduced by the merge itself."""
        for i in range(20):
            run("schedule", db, note=f"reminder {i}", in_minutes=60 + i)
        run("schedule", db, note="the daily briefing", daily_at="09:00")

        shown = str(run("list_schedules", db, limit=10))

        assert "the daily briefing" in shown, "a whole kind fell off the first page"


class TestTheWindowedTimelineIsStillWhole:
    def test_no_turn_is_lost_between_pages(self, tmp_path, monkeypatch):
        """Paging is only safe if the pages join. Asserted against the unwindowed build of the
        same conversation, so a boundary that dropped or duplicated a turn shows up."""
        monkeypatch.setattr(conversations, "transcript_path", lambda cid: tmp_path / f"{cid}.jsonl")
        lines = []
        for turn in range(50):
            lines.append({"type": "message", "role": "user", "text": f"ask {turn}", "at": f"t{turn}"})
            lines.append({"type": "said", "text": f"answer {turn}", "at": f"t{turn}"})
        (tmp_path / "c.jsonl").write_text("\n".join(json.dumps(one) for one in lines) + "\n")

        whole = conversations.timeline("c")
        gathered: list[dict] = []
        before: int | None = None
        while True:
            page = conversations.timeline_window("c", turns=7, before=before)
            if not page["turns"]:
                break
            gathered = page["turns"] + gathered
            before = page["start"]
            if not page["hasMore"]:
                break

        assert gathered == whole, "the pages do not reassemble the conversation"
