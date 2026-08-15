"""What counts as "this project's code" when mapping or searching a folder.

Two filters, answering different questions, and the reason both are kept is measured rather
than argued. On this repository the walk parsed 718 files and 251 of them — 35% — were ones
git ignores. The two causes show the limits of each approach:

* `desktop/release/`, a packaged application full of minified bundles, was simply missing from
  `SKIP_DIRS`. That is the list rotting, and it will keep rotting.
* `server/data/`, 150 files of Kith's own working notes, **could never be in that list**. It is
  a path, not a name, and adding `data` would skip a legitimate `data/` folder in every other
  project anyone points him at.

So git is asked when there is a repository, and `SKIP_DIRS` is the whole answer when there is
not. `.gitignore` is deliberately *not* parsed here: this repository's own file already uses a
negation, path-anchored directories and globs, and a nearly-correct parser fails silently.
`git ls-files` is that parser, already correct.

The case that would rot quietly is the last one — a folder that is not a repository at all has
to keep working, because a downloaded tarball is a perfectly ordinary thing to be asked about.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from kith.engine.code import repomap, search

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


class TestWhenThereIsNoRepository:
    def test_every_source_file_is_still_found(self, tmp_path):
        """A downloaded folder, an extracted archive, someone else's project. Git having no
        opinion is an ordinary condition, not a reason to return nothing."""
        (tmp_path / "a.py").write_text("def f(): pass\n")
        (tmp_path / "b.ts").write_text("export function g() {}\n")
        found = {p.name for p in repomap.candidates(tmp_path)}
        assert found == {"a.py", "b.ts"}

    def test_the_skip_list_still_applies(self, tmp_path):
        (tmp_path / "a.py").write_text("def f(): pass\n")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "junk.py").write_text("def g(): pass\n")
        assert {p.name for p in repomap.candidates(tmp_path)} == {"a.py"}

    def test_git_is_asked_and_says_nothing(self, tmp_path):
        """`_git_knows` returning None is the "carry on" signal, and it must not be confused
        with an empty set, which would mean "the project contains no files"."""
        assert repomap._git_knows(tmp_path) is None


@needs_git
class TestWhenThereIsARepository:
    def test_an_ignored_file_is_not_walked(self, tmp_path):
        _repo(tmp_path)
        (tmp_path / "keep.py").write_text("def keep(): pass\n")
        (tmp_path / "generated.py").write_text("def generated(): pass\n")
        (tmp_path / ".gitignore").write_text("generated.py\n")
        assert {p.name for p in repomap.candidates(tmp_path)} == {"keep.py"}

    def test_an_ignored_folder_is_not_walked(self, tmp_path):
        """The `desktop/release/` case: a directory name nobody thought to add to SKIP_DIRS."""
        _repo(tmp_path)
        (tmp_path / "src.py").write_text("def src(): pass\n")
        (tmp_path / "release").mkdir()
        (tmp_path / "release" / "bundle.py").write_text("def bundled(): pass\n")
        (tmp_path / ".gitignore").write_text("release/\n")
        assert {p.name for p in repomap.candidates(tmp_path)} == {"src.py"}

    def test_a_path_anchored_ignore_is_honoured(self, tmp_path):
        """The `server/data/` case — the one a name list cannot express, because `data` as a
        bare name would skip somebody else's legitimate data folder."""
        _repo(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "scratch.py").write_text("def scratch(): pass\n")
        (tmp_path / "keep").mkdir()
        (tmp_path / "keep" / "data.py").write_text("def real(): pass\n")
        (tmp_path / ".gitignore").write_text("/data/\n")
        found = {p.name for p in repomap.candidates(tmp_path)}
        assert found == {"data.py"}, "the folder goes, the similarly-named file stays"

    def test_a_file_written_and_not_yet_committed_is_still_found(self, tmp_path):
        """`--others --exclude-standard`. Without it this only works on a clean checkout, and
        everything he just wrote would be invisible until committed — which is precisely when
        he most wants to find it."""
        _repo(tmp_path)
        (tmp_path / "brand_new.py").write_text("def fresh(): pass\n")
        assert {p.name for p in repomap.candidates(tmp_path)} == {"brand_new.py"}

    def test_the_skip_list_still_applies_inside_a_repository(self, tmp_path):
        """Git says *tracked*, not *interesting*. `vendor/` and sometimes `node_modules` are
        committed, and they are still noise."""
        _repo(tmp_path)
        (tmp_path / "a.py").write_text("def f(): pass\n")
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.py").write_text("def vendored(): pass\n")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A", "-f"], check=True, capture_output=True)
        assert {p.name for p in repomap.candidates(tmp_path)} == {"a.py"}

    def test_a_subdirectory_of_a_repository_resolves_its_own_paths(self, tmp_path):
        """`git ls-files` returns paths relative to the directory it was pointed at, not to the
        repository root. Joining them to the wrong base silently matches nothing, which would
        look like "this folder has no code in it"."""
        _repo(tmp_path)
        inner = tmp_path / "sub" / "deeper"
        inner.mkdir(parents=True)
        (inner / "here.py").write_text("def here(): pass\n")
        assert {p.name for p in repomap.candidates(inner)} == {"here.py"}


@needs_git
class TestMappingOnlyWhatChanged:
    """A whole-repo map is right for orientation and wrong for review — reviewing a twelve-file
    change against three hundred files spends the budget describing code nobody touched."""

    def test_only_the_changed_files_are_mapped(self, tmp_path):
        _repo(tmp_path)
        (tmp_path / "old.py").write_text("def untouched(): pass\n")
        (tmp_path / "also_old.py").write_text("def also(): pass\n")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "first",
            ],
            check=True,
            capture_output=True,
        )
        (tmp_path / "old.py").write_text("def untouched(): pass\ndef added_later(): pass\n")

        mapped = repomap.build(tmp_path, changed_since="HEAD")
        assert [one["path"] for one in mapped["entries"]] == ["old.py"]

    def test_a_file_written_and_never_added_still_counts_as_changed(self, tmp_path):
        """Without `--others` a review of work in progress omits exactly the files just
        created, which are the ones most worth looking at."""
        _repo(tmp_path)
        (tmp_path / "committed.py").write_text("def a(): pass\n")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "first",
            ],
            check=True,
            capture_output=True,
        )
        (tmp_path / "brand_new.py").write_text("def fresh(): pass\n")
        mapped = repomap.build(tmp_path, changed_since="HEAD")
        assert [one["path"] for one in mapped["entries"]] == ["brand_new.py"]

    def test_a_ref_that_does_not_exist_is_refused_rather_than_ignored(self, tmp_path):
        """Silently widening back to the whole repository would be read as "the change is
        enormous", which is a worse answer than an error."""
        _repo(tmp_path)
        (tmp_path / "a.py").write_text("def a(): pass\n")
        with pytest.raises(repomap.RepoMapError) as caught:
            repomap.build(tmp_path, changed_since="no-such-ref")
        assert "no-such-ref" in str(caught.value)

    def test_nothing_changed_says_so(self, tmp_path):
        _repo(tmp_path)
        (tmp_path / "a.py").write_text("def a(): pass\n")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "first",
            ],
            check=True,
            capture_output=True,
        )
        with pytest.raises(repomap.RepoMapError) as caught:
            repomap.build(tmp_path, changed_since="HEAD")
        assert "nothing has changed" in str(caught.value)


@needs_git
class TestWhatItDoesNotFilter:
    def test_search_stops_reporting_ignored_files(self, tmp_path):
        _repo(tmp_path)
        (tmp_path / "real.py").write_text("def target(): pass\n")
        (tmp_path / "built.py").write_text("def target(): pass\n")
        (tmp_path / ".gitignore").write_text("built.py\n")
        found = search.find(tmp_path, "target")
        assert [one["path"] for one in found["definitions"]] == ["real.py"]

    def test_an_explicitly_named_file_is_still_readable(self, tmp_path):
        """Only the *walk* is filtered. Sometimes the generated client is exactly the thing you
        want to look at, and asking for it by name has to keep working."""
        from kith.engine.code import excerpt, outline

        _repo(tmp_path)
        (tmp_path / "generated.py").write_text("def generated(): pass\n")
        (tmp_path / ".gitignore").write_text("generated.py\n")
        assert outline.of_file(tmp_path / "generated.py")["symbols"], "outline still works"
        assert excerpt.locate(tmp_path / "generated.py", "generated").line == 1
