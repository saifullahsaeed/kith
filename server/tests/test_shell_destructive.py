"""The gate on a shell command that destroys something.

Written after a real failure, and the shape of it is worth keeping. Asked to delete a file
from the Desktop, he wrote::

    rm -- "$HOME/Desktop/sadeef-library-export.zip"

and it ran. Ask mode, a file outside his folder, no prompt, unrecoverable. Three things
were wrong at once:

* The ``rm`` pattern in ``_DANGEROUS`` insists on ``-r`` or ``-f``, so a plain ``rm`` of
  one named file matched nothing.
* Beyond that list, a shell command was checked against *nothing* — the dangerous patterns
  were the only gate, so where a command pointed was never considered at all.
* There was no delete tool, so the shell was the only route available to him. The least
  supervised path in the system was also the only one.

The first two are what this file covers. The tests are written in both directions on
purpose: a gate that fires on ordinary work inside his own folder gets switched off, and a
switched-off gate protects nothing, so "must not prompt" is as much a requirement here as
"must prompt".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import permissions


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """Ask mode, no standing grants, an empty workspace under tmp."""
    from kith.infra.db import config_store

    db = tmp_path / "config.db"
    config_store.init(db)
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", db, raising=False)
    permissions.revoke_all()
    root = tmp_path / "Kith"
    root.mkdir()
    return root


def asks(command: str, root: Path) -> bool:
    return not permissions.check_command(command, root).allowed


class TestTheOneThatGotThrough:
    def test_the_exact_command_he_ran(self, gate):
        assert asks('rm -- "$HOME/Desktop/sadeef-library-export.zip"', gate)

    def test_a_plain_rm_of_one_file_outside_his_folder(self, gate):
        # The whole hole in one line: no -r, no -f, nothing for the dangerous list to see.
        assert asks(f"rm {Path.home()}/Desktop/thing.zip", gate)

    def test_tilde_form(self, gate):
        assert asks("rm ~/Documents/report.pdf", gate)

    def test_home_variable_form(self, gate):
        assert asks('rm "${HOME}/Documents/report.pdf"', gate)

    @pytest.mark.parametrize("verb", ["rm", "rmdir", "unlink", "shred"])
    def test_every_way_of_saying_delete(self, gate, verb):
        assert asks(f"{verb} {Path.home()}/Desktop/thing", gate)

    def test_a_redirect_onto_your_desktop(self, gate):
        # Not a delete, but it overwrites whatever was there, which is the same loss.
        assert asks(f"echo hacked > {Path.home()}/Desktop/notes.txt", gate)

    def test_moving_your_files_around(self, gate):
        assert asks(f"mv {Path.home()}/Desktop/a {Path.home()}/Desktop/b", gate)

    def test_deleting_a_program(self, gate):
        # Program locations are exempt from the *write* check because interpreter paths
        # appear in every other command; deletes are not exempt, because nothing about
        # `rm /usr/local/bin/x` is routine.
        assert asks("rm /usr/local/bin/sometool", gate)

    def test_it_is_a_prompt_and_not_a_wall(self, gate):
        decision = permissions.check_command("rm ~/Desktop/x", gate)
        assert not decision.allowed
        assert decision.request is not None
        # He has to be able to repeat the reason to his person in his own words.
        assert "delete" in decision.reason or "delete" in decision.request.why


class TestWhatMustStillJustRun:
    """Every one of these is a false prompt someone would learn to click through."""

    def test_inside_his_own_folder(self, gate):
        assert not asks(f"rm {gate}/scratch.txt", gate)

    def test_relative_paths(self, gate):
        assert not asks("rm ./build/tmp.o", gate)

    def test_scratch_space(self, gate):
        assert not asks("rm /tmp/scratch", gate)
        assert not asks("rm /var/folders/ab/cd/T/thing", gate)

    def test_the_null_device(self, gate):
        assert not asks("some-command > /dev/null", gate)

    def test_stderr_redirection_is_not_a_write(self, gate):
        # `2>&1` is the most common idiom in shell. Reading it as "writes to a file called
        # &1" would have made this gate fire on nearly everything.
        assert not asks("make build 2>&1", gate)

    def test_an_interpreter_path_in_a_command_that_writes_locally(self, gate):
        assert not asks("python3 /usr/bin/thing.py > out.txt 2>&1", gate)

    def test_copying_a_program_into_his_folder(self, gate):
        assert not asks("cp /opt/homebrew/bin/rg ./bin/rg", gate)

    def test_a_command_that_destroys_nothing(self, gate):
        assert not asks(f"ls -la {Path.home()}/Desktop", gate)
        assert not asks("git status && echo done", gate)


class TestReadsAreStillOpen:
    def test_reading_outside_the_workspace_through_the_shell_is_not_gated(self, gate):
        # Documented, not accidental: gating reads would prompt on every path in every
        # script, and the harm being fixed here is silent *destruction*. Recorded as a test
        # so that if it ever changes, it changes deliberately.
        assert not asks(f"cat {Path.home()}/Desktop/notes.txt", gate)


class TestReadingPathsOutOfACommand:
    def test_a_relative_path_contributes_no_phantom_absolute_one(self):
        # An early cut matched the `/b` inside `a/b` and prompted about `/b`.
        assert permissions.paths_named("cp a/b c/d") == []

    def test_quoted_and_bare_forms_both_count(self, tmp_path):
        found = permissions.paths_named('rm "/one/two" /three/four', home=tmp_path)
        assert Path("/one/two") in found and Path("/three/four") in found

    def test_dot_dot_is_flattened(self, tmp_path):
        found = permissions.paths_named("rm /one/two/../three", home=tmp_path)
        assert Path("/one/three") in found

    def test_home_is_expanded_against_the_real_home(self, tmp_path):
        found = permissions.paths_named("rm ~/thing", home=tmp_path)
        assert found == [tmp_path / "thing"]

    def test_a_bare_slash_is_not_a_path(self, tmp_path):
        # Python spells path joins as `Path.home() / "Kith"`, so a heredoc full of pathlib
        # gives space-slash-space over and over. Reading those as the filesystem root refused
        # a real command whose own paths were all relative and inside his folder.
        found = permissions.paths_named(
            'cat > out.py <<PY\np = Path.home() / "Kith" / "x"\nPY', home=tmp_path
        )
        assert found == []

    def test_the_alarming_ways_to_name_the_root_are_still_caught(self, gate):
        # Dropping the bare slash must not drop `rm -rf /`. It is matched by the dangerous
        # list, which is where a blast radius that size belongs.
        assert asks("rm -rf /", gate)
        assert asks("chmod 777 /", gate)

    def test_a_computed_path_is_invisible_and_that_is_known(self, tmp_path):
        # The limit of reading literals. It is why the check looks at every path in the
        # command rather than trying to find "the" target: in the real failure the file
        # itself was `$d/...`, and what saved it was the loop naming the Desktop out loud.
        assert permissions.paths_named('rm "$d/thing.zip"', home=tmp_path) == []


class TestDeletingRecoverably:
    """The other half of the same failure: it was gone, not in the Trash.

    A prompt is the first line and it will not always catch things — the paths a command
    computes at runtime are invisible to any text check. So the operation itself has to be
    survivable. macOS has a recoverable delete; every other app on the machine uses it;
    there was no reason this one was calling ``unlink``.
    """

    @pytest.fixture
    def workspace(self, tmp_path, monkeypatch):
        from kith.infra import workspace as ws
        from kith.infra.db import config_store

        db = tmp_path / "config.db"
        config_store.init(db)
        monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", db, raising=False)
        monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", "", raising=False)
        root = tmp_path / "Kith"
        root.mkdir()
        config_store.update_settings(db, {ws.ROOT_KEY: str(root)})
        # A Trash of our own, so the suite never puts anything in the real one.
        home = tmp_path / "home"
        (home / ".Trash").mkdir(parents=True)
        monkeypatch.setattr(ws.paths.Path, "home", staticmethod(lambda: home), raising=False)
        return ws, root, home

    def test_a_file_goes_to_the_trash_and_still_exists(self, workspace):
        ws, root, home = workspace
        (root / "report.md").write_text("months of work")

        ws.remove("report.md")

        assert not (root / "report.md").exists()
        assert (home / ".Trash" / "report.md").read_text() == "months of work"

    def test_it_says_where_it_went(self, workspace):
        ws, root, _ = workspace
        (root / "a.txt").write_text("x")
        # The answer he gives should be able to say "in the Trash" truthfully.
        assert "Trash" in ws.remove("a.txt")

    def test_a_folder_goes_too(self, workspace):
        ws, root, home = workspace
        (root / "notes").mkdir()
        (root / "notes" / "one.md").write_text("hi")

        ws.remove("notes")

        assert (home / ".Trash" / "notes" / "one.md").read_text() == "hi"

    def test_a_same_named_file_does_not_overwrite_what_is_already_there(self, workspace):
        ws, root, home = workspace
        (home / ".Trash" / "report.md").write_text("the older one")
        (root / "report.md").write_text("the newer one")

        ws.remove("report.md")

        # Landing on top of it would delete, from the Trash, the thing the Trash existed
        # to protect — the one place a delete must not happen.
        assert (home / ".Trash" / "report.md").read_text() == "the older one"
        assert (home / ".Trash" / "report 2.md").read_text() == "the newer one"

    def test_his_whole_folder_is_refused(self, workspace):
        ws, root, _ = workspace
        with pytest.raises(ws.WorkspaceError):
            ws.remove(".")
        assert root.is_dir()

    def test_nothing_there_says_so(self, workspace):
        ws, _, _ = workspace
        with pytest.raises(ws.WorkspaceError) as caught:
            ws.remove("never-existed.txt")
        assert "nothing at" in str(caught.value)

    def test_no_trash_on_this_machine_is_said_out_loud(self, workspace, monkeypatch):
        ws, root, home = workspace
        (root / "a.txt").write_text("x")
        import shutil as real_shutil

        real_shutil.rmtree(home / ".Trash")

        answer = ws.remove("a.txt")

        # Reporting "moved to the Trash" about a file that is gone for good would be the
        # one lie in this system that actually costs someone something.
        assert "permanently" in answer
        assert not (root / "a.txt").exists()


class TestSkillScriptsAreNotOutOfFolderWrites:
    """Found by running it, not by thinking about it.

    Asked for a spreadsheet, he read the xlsx skill, followed its instructions, and the
    command was refused — because the skill's own ``scripts/recalc.py`` lives in the skills
    directory, which is outside his workspace, and the command also contained a redirect. One
    intent is assigned to the whole command and applied to every path in it, so a script being
    *run* was read as a file being written.

    The skill told him to run it and the person installed it deliberately. A gate that fires
    on the feature working correctly is the exact failure this design is trying to avoid.
    """

    @pytest.fixture
    def with_skills(self, tmp_path, monkeypatch):
        from kith.services import skills

        # pytest's tmp_path lives under /var/folders, which the gate deliberately treats as
        # scratch space — so every path in these tests would be exempt for the wrong reason
        # and the assertions would pass without testing anything. Narrowing the scratch list
        # is what makes a temp directory stand in for a real one.
        monkeypatch.setattr(permissions, "_UNREMARKABLE_PREFIXES", ("/dev",), raising=False)
        place = tmp_path / "skills"
        (place / "xlsx" / "scripts").mkdir(parents=True)
        (place / "xlsx" / "SKILL.md").write_text(
            "---\nname: xlsx\ndescription: Spreadsheets. Use for spreadsheets.\n---\n"
        )
        (place / "xlsx" / "scripts" / "recalc.py").write_text("print('recalculated')")
        monkeypatch.setenv(skills.DIR_KEY, str(place))
        return place

    def test_running_a_bundled_script_in_a_writing_command(self, gate, with_skills):
        command = (
            "cat > ./build.py <<'PY'\nout = 1\nPY\n"
            f"python3 {with_skills}/xlsx/scripts/recalc.py ./sheet.xlsx 30"
        )
        assert not asks(command, gate)

    def test_deleting_inside_a_skill_folder_still_asks(self, gate, with_skills):
        # Uninstalling a capability sideways with `rm` is worth a question — the exemption is
        # for writes only, and deletes deliberately do not get it.
        assert asks(f"rm {with_skills}/xlsx/SKILL.md", gate)

    def test_the_exemption_does_not_leak_to_the_rest_of_the_data_directory(self, gate, with_skills):
        # The skills folder sits beside the databases. Widening this to the whole data
        # directory would exempt his memory and his transcripts from the write check.
        assert asks(f"echo corrupt > {with_skills.parent}/agent.db", gate)
