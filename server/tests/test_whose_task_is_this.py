"""Whose task this is — the one fact about a shared board that cannot be backfilled.

Every task on this machine says `created_by = 'kith'`. All 87. That is not a bug while there is
one person: "him or me" is the whole of the question a single-user board has to answer.

It stops being the whole of it the moment `.kith/` is shared through git, which it already is —
102 files committed in one project here, 15 of those commits authored by Kith and 5 by a person.
And it is the one thing no later migration can repair: a task filed with no author never had one,
and writing today's git identity into rows from August would be a guess wearing a record's
clothes.

**Identity is whatever git will put on the commit.** Not a setting, not a profile, not a row in
his database. The work travels by being committed, so the name on the work should be the name on
the commit that carries it — any second answer is a second source of truth for one fact, and it
is the one that would disagree. Measured on this machine while writing it, and the reason the
lookup is per-directory rather than global:

    global      saifullahsaeed.work@gmail.com
    in ai-play  saifullah@sadeef.com
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import identity, project_files


@pytest.fixture(autouse=True)
def no_cached_answer():
    identity.forget()
    yield
    identity.forget()


def _repo(directory: Path, email: str = "", name: str = "") -> Path:
    import subprocess

    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
    if email:
        subprocess.run(["git", "config", "user.email", email], cwd=directory, check=True)
    if name:
        subprocess.run(["git", "config", "user.name", name], cwd=directory, check=True)
    return directory


class TestWhoGitSaysYouAre:
    def test_it_is_the_address_on_the_commit(self, tmp_path: Path):
        _repo(tmp_path, email="saif@example.com")
        assert identity.whoami(tmp_path) == "saif@example.com"

    def test_a_name_will_do_when_there_is_no_address(self, tmp_path: Path):
        """The address is emptied *locally* rather than merely left unset, because git's config
        cascades and an unset local address is answered by the global one. That cascade is not a
        leak — it is precisely what git will sign the commit with, which is the definition this
        module works to. The fallback only fires when there is genuinely no address anywhere."""
        _repo(tmp_path, email="", name="Saifullah")
        import subprocess

        subprocess.run(["git", "config", "user.email", ""], cwd=tmp_path, check=True)
        assert identity.whoami(tmp_path) == "Saifullah"

    def test_a_repository_with_no_address_of_its_own_uses_the_one_git_would(self, tmp_path: Path):
        """The other half of the same fact, asserted so nobody "fixes" the cascade later."""
        _repo(tmp_path, name="Saifullah")
        import subprocess

        globally = subprocess.run(
            ["git", "config", "--get", "user.email"], capture_output=True, text=True
        ).stdout.strip()
        if not globally:
            pytest.skip("no global git identity on this machine to inherit")
        assert identity.whoami(tmp_path) == globally

    def test_the_address_wins_over_the_name(self, tmp_path: Path):
        _repo(tmp_path, email="saif@example.com", name="Saifullah")
        assert identity.whoami(tmp_path) == "saif@example.com"

    def test_each_project_can_answer_differently(self, tmp_path: Path):
        """Not a nicety. A work address and a personal one is a real distinction people already
        keep in `git config`, and the task should land under whichever will sign its commit."""
        work = _repo(tmp_path / "work", email="saif@company.com")
        mine = _repo(tmp_path / "mine", email="saif@home.com")
        assert identity.whoami(work) == "saif@company.com"
        assert identity.whoami(mine) == "saif@home.com"

    def test_nobody_configured_is_blank_and_not_a_guess(self, tmp_path: Path):
        """`unknown` written into 87 rows is worse than a blank — it looks like an answer."""
        import subprocess

        directory = tmp_path / "bare"
        directory.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
        subprocess.run(["git", "config", "user.email", ""], cwd=directory, check=True)
        subprocess.run(["git", "config", "user.name", ""], cwd=directory, check=True)
        assert identity.whoami(directory) == ""

    def test_somewhere_that_is_not_a_repository_does_not_raise(self, tmp_path: Path):
        """This is called on the way to writing a task. A task that could not be written because
        nobody had configured git would be an absurd failure."""
        assert isinstance(identity.whoami(tmp_path / "nothing-here"), str)

    def test_the_answer_is_cached_because_it_is_a_subprocess(self, tmp_path: Path):
        _repo(tmp_path, email="first@example.com")
        assert identity.whoami(tmp_path) == "first@example.com"
        import subprocess

        subprocess.run(["git", "config", "user.email", "second@example.com"], cwd=tmp_path, check=True)
        assert identity.whoami(tmp_path) == "first@example.com", "still the cached one"
        identity.forget()
        assert identity.whoami(tmp_path) == "second@example.com"


class TestTheBriefCarriesIt:
    """The half that actually travels. The database stays on whichever machine is driving, so a
    name not written into the brief is not written anywhere a second person can read."""

    def test_the_name_and_who_acted_are_both_shown(self, tmp_path: Path):
        project_files.write_brief(
            tmp_path,
            {
                "id": 7,
                "goal": "Do the thing",
                "status": "working",
                "account": "saif@example.com",
                "created_by": "kith",
            },
        )
        brief = next((tmp_path / ".kith" / "tasks").glob("*.md")).read_text()
        assert "saif@example.com (Kith)" in brief

    def test_a_task_you_filed_reads_as_yours(self, tmp_path: Path):
        project_files.write_brief(
            tmp_path,
            {
                "id": 8,
                "goal": "Do it",
                "status": "working",
                "account": "saif@example.com",
                "created_by": "user",
            },
        )
        brief = next((tmp_path / ".kith" / "tasks").glob("*.md")).read_text()
        assert "saif@example.com (you)" in brief

    def test_an_older_task_says_nothing_rather_than_guessing(self, tmp_path: Path):
        """The 87 that predate the column. A blank line is obviously a gap; "unknown" is not."""
        project_files.write_brief(tmp_path, {"id": 9, "goal": "Old one", "status": "done"})
        brief = next((tmp_path / ".kith" / "tasks").glob("*.md")).read_text()
        assert "Filed by" not in brief
        assert "unknown" not in brief.lower()
