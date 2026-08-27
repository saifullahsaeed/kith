"""Whether somebody else's work has arrived, and whether anybody has looked.

`board_sync.waiting_here` reads the *working tree*, and nothing in this app has ever fetched.
So "the folder is quiet" and "nobody has fetched in a week" produced the same silence and meant
opposite things — and the local mtimes it fell back on cannot tell them apart either, because a
checkout rewrites them: work that arrived from somebody else last week looks like it was
written just now.

Two operations close it, and they are deliberately separate. `standing` reads refs already on
disk — no network, cheap enough to sit in the system prompt and be paid for every turn — and
says how old that reading is, because a commit count from a fetch nobody has run since Tuesday
is not information. `fetch` is the network half, and it is not `pull`: it cannot conflict and
cannot half-merge, so asking "has anything arrived" never requires putting the work down first.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from kith.infra.workspace import git


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def clone(tmp_path: Path, monkeypatch):
    """Two checkouts of one repository — which is what "somebody else pushed" actually is."""
    origin = tmp_path / "origin.git"
    # `-b main` on the bare repository as well as the seed. Without it the bare HEAD points at
    # `master`, the push creates `main`, and every clone comes out on an empty `master` with no
    # upstream — so the readings below would have been about a broken checkout.
    _run("init", "--bare", "-q", "-b", "main", str(origin), cwd=tmp_path)

    # Seeded before anything is cloned. A clone of an empty repository has no HEAD and no
    # upstream, so every reading below would have been about that instead of about the thing
    # being tested.
    seed = tmp_path / "seed"
    seed.mkdir()
    _run("init", "-q", "-b", "main", cwd=seed)
    _run("config", "user.email", "t@example.com", cwd=seed)
    _run("config", "user.name", "T", cwd=seed)
    _run("config", "commit.gpgsign", "false", cwd=seed)
    (seed / "a.txt").write_text("one\n")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "first", cwd=seed)
    _run("remote", "add", "origin", str(origin), cwd=seed)
    _run("push", "-q", "-u", "origin", "main", cwd=seed)

    def _checkout(name: str) -> Path:
        where = tmp_path / name
        _run("clone", "-q", str(origin), str(where), cwd=tmp_path)
        _run("config", "user.email", "t@example.com", cwd=where)
        _run("config", "user.name", "T", cwd=where)
        _run("config", "commit.gpgsign", "false", cwd=where)
        return where

    mine, theirs = _checkout("mine"), _checkout("theirs")
    # The cache is a module global keyed by repository root, and every test here builds a repo
    # at a fresh path — but a reading taken before a push would still be served after it.
    git._standing_cache.clear()
    monkeypatch.setattr(git, "base_dir", lambda: mine)
    return mine, theirs


def _push_something(where: Path, name: str = "b.txt") -> None:
    (where / name).write_text("theirs\n")
    _run("add", "-A", cwd=where)
    _run("commit", "-q", "-m", "theirs", cwd=where)
    _run("push", "-q", cwd=where)


class TestReadingItLocally:
    def test_a_level_and_recently_fetched_folder_says_nothing(self, clone):
        # Silence is the right answer here, and it has to be: this sits in the prompt of every
        # turn on a project, and a line saying "all is well" would be paid for on all of them.
        mine, _ = clone
        assert git.standing(mine) == ""

    def test_commits_waiting_in_the_remote_are_counted(self, clone):
        mine, theirs = clone
        _push_something(theirs)
        _run("fetch", "-q", cwd=mine)
        git._standing_cache.clear()

        said = git.standing(mine)

        assert "1 commit" in said and "waiting" in said

    def test_it_says_how_to_take_them_in(self, clone):
        # A count with no operation beside it is a fact he can do nothing with.
        mine, theirs = clone
        _push_something(theirs)
        _run("fetch", "-q", cwd=mine)
        git._standing_cache.clear()

        assert "publish" in git.standing(mine)

    def test_unpushed_work_here_is_counted_too(self, clone):
        mine, _ = clone
        (mine / "c.txt").write_text("mine\n")
        _run("add", "-A", cwd=mine)
        _run("commit", "-q", "-m", "mine", cwd=mine)
        git._standing_cache.clear()

        assert "not been pushed" in git.standing(mine)

    def test_a_stale_fetch_is_said_beside_the_count(self, clone):
        """The number and its age mean opposite things apart. Zero behind is agreement if
        somebody looked this morning and no information at all if nobody has looked since."""
        mine, _ = clone
        _run("fetch", "-q", cwd=mine)
        old = time.time() - (30 * 24 * 3600)
        os.utime(mine / ".git" / "FETCH_HEAD", (old, old))
        git._standing_cache.clear()

        said = git.standing(mine)

        assert "nobody has fetched" in said and "30 days" in said

    def test_a_repository_with_no_remote_says_nothing_at_all(self, tmp_path):
        alone = tmp_path / "alone"
        alone.mkdir()
        _run("init", "-q", cwd=alone)
        (alone / "a.txt").write_text("one\n")

        assert git.standing(alone) == ""

    def test_a_folder_that_is_not_a_repository_says_nothing(self, tmp_path):
        assert git.standing(tmp_path) == ""

    def test_a_branch_nobody_has_pushed_is_worth_a_word(self, clone):
        mine, _ = clone
        _run("checkout", "-q", "-b", "side", cwd=mine)
        git._standing_cache.clear()

        assert "has been shared" in git.standing(mine)


class TestAskingTheRemote:
    def test_fetching_makes_the_count_true(self, clone):
        mine, theirs = clone
        _push_something(theirs)
        # Before: nothing has been fetched, so the local reading cannot know.
        assert "waiting" not in git.standing(mine)

        git._standing_cache.clear()
        said = git.fetch(mine)

        assert "1 commit" in said and "waiting" in said

    def test_it_works_with_the_work_half_done(self, clone):
        """`pull` refuses outright on a dirty tree — which is the state anybody mid-job is in,
        so asking "has anything arrived" would mean putting the work down first."""
        mine, theirs = clone
        _push_something(theirs)
        (mine / "a.txt").write_text("half an edit\n")

        assert "Couldn't" not in git.fetch(mine)
        assert (mine / "a.txt").read_text() == "half an edit\n"

    def test_nothing_new_is_said_plainly(self, clone):
        mine, _ = clone
        git._standing_cache.clear()

        assert "level with the remote" in git.fetch(mine)

    def test_no_remote_is_not_an_error(self, tmp_path):
        alone = tmp_path / "alone"
        alone.mkdir()
        _run("init", "-q", cwd=alone)

        assert "nowhere to check" in git.fetch(alone)


class TestTheTwoNumbersTogether:
    """Neither number means anything without the other, and the phrasing has to survive one of
    them being absent."""

    def test_level_but_unfetched_does_not_refer_to_a_count_it_never_printed(self, clone):
        # "nobody has fetched in 30 days, so that count may be out of date" — with nothing
        # behind and nothing ahead there is no count on the line for "that" to point at.
        mine, _ = clone
        _run("fetch", "-q", cwd=mine)
        old = time.time() - (30 * 24 * 3600)
        os.utime(mine / ".git" / "FETCH_HEAD", (old, old))
        git._standing_cache.clear()

        said = git.standing(mine)

        assert "that count" not in said
        assert "nothing has arrived" in said and "30 days" in said

    def test_a_count_and_a_stale_fetch_are_said_in_one_breath(self, clone):
        mine, theirs = clone
        _push_something(theirs)
        _run("fetch", "-q", cwd=mine)
        old = time.time() - (30 * 24 * 3600)
        os.utime(mine / ".git" / "FETCH_HEAD", (old, old))
        git._standing_cache.clear()

        said = git.standing(mine)

        assert "1 commit" in said and "that count may be out of date" in said

    def test_a_reading_is_reused_briefly_rather_than_shelling_out_every_turn(self, clone):
        """Four `git` processes per turn on a path that runs whether or not anybody asked."""
        mine, theirs = clone
        git._standing_cache.clear()
        first = git.standing(mine)
        _push_something(theirs)
        _run("fetch", "-q", cwd=mine)

        assert git.standing(mine) == first  # the clock, not the repository, decides
        git._standing_cache.clear()
        assert git.standing(mine) != first
