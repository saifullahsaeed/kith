"""The suite must not write into the user's data folder.

Emptying `server/data/conversations` and running `./check` once put 54 transcripts back,
all stamped within the same second — the suite's own. The folder had 4,491 in it. Nothing
ever failed over this, which is exactly why it lasted: the only symptom is that a folder
belonging to the user slowly stops being theirs.

The database had already been isolated. The transcript folder had not, because
`workspace.internal()` reads `settings.DATA_DIR` and never consults `AGENT_DB_PATH` — so
"database redirected, data folder real" was a perfectly consistent state to be in.
"""

from __future__ import annotations

from pathlib import Path

from kith import settings
from kith.infra import workspace
from kith.services import conversations


def _real_data_dir() -> Path:
    """Where the user's data actually lives, ignoring whatever the fixtures did."""
    return Path(__file__).resolve().parent.parent / "data"


def test_the_data_folder_a_test_sees_is_not_the_real_one():
    assert Path(settings.DATA_DIR).resolve() != _real_data_dir().resolve()


def test_transcripts_written_by_a_test_land_in_the_temp_folder():
    """The specific path that leaked. `internal()` is the one that got missed."""
    assert Path(workspace.internal()).resolve() != _real_data_dir().resolve()

    place = conversations.directory()
    assert _real_data_dir().resolve() not in place.resolve().parents

    # Not just resolved somewhere else — actually writable there, and actually lands there.
    (place / "20260101-000000000-abcdef.jsonl").write_text('{"type": "start"}\n')
    assert (place / "20260101-000000000-abcdef.jsonl").exists()


def test_a_real_conversation_does_not_touch_the_real_folder(db):
    """End to end, through the writer that leaked, not through the path helper.

    `start()` is the exact call that produced the 54 files: it touches the transcript and
    appends a `start` entry, both via `directory()`.
    """
    before = set(_real_data_dir().glob("conversations/*.jsonl"))

    opened = conversations.start(db, "a test, not a person")
    conversations.record(db, opened["id"], "user", "hello")

    assert set(_real_data_dir().glob("conversations/*.jsonl")) == before
    assert conversations.transcript_path(opened["id"]).exists()


def test_skills_are_still_reachable_through_the_redirect():
    """The redirect must not blind the suite to the installed skills.

    They are read, never written, so the temp folder links through to the real ones. If this
    breaks, the fixture is isolating more than it should and skill tests will fail obscurely.
    """
    linked = Path(settings.DATA_DIR) / "skills"
    real = _real_data_dir() / "skills"
    # Whether anything is *installed*, not whether the folder exists. `skills.root()` does
    # `mkdir(parents=True, exist_ok=True)`, so merely asking where skills live conjures an
    # empty directory — and on a fresh clone that is exactly what happens, several tests before
    # this one runs. An existence check therefore passed, found nothing to copy, and failed on
    # `assert copied` with a message about the fixture isolating too much. The fixture was fine;
    # there were no skills.
    installed = {one.name for one in real.iterdir()} if real.is_dir() else set()
    if not installed:
        return  # A checkout with no skills installed; nothing to link.
    assert linked.is_dir()

    copied = {one.name for one in linked.iterdir()}

    # A subset, not an exact match. The copy is made once per session and the real folder is
    # live — installing a skill while the suite runs made this fail on set equality, which is
    # a true statement about the copy and nothing at all about whether skills are reachable.
    # What matters is that the copy is populated and that everything in it came from somewhere
    # legitimate.
    #
    # "Somewhere" is two places, which the first version of this missed. A skill lands in the
    # copy either because it was installed — the case this test is about — or because it ships
    # in `server/skills` and the app seeded it on boot during some earlier test. The second kind
    # is in the copy and *not yet* in the real folder, so comparing against the installed set
    # alone fails the moment anyone adds a bundled skill, with a message about the fixture
    # isolating too much and nothing wrong with the fixture at all.
    bundled = (
        {one.name for one in settings.BUNDLED_SKILLS_DIR.iterdir()}
        if settings.BUNDLED_SKILLS_DIR.is_dir()
        else set()
    )
    assert copied, "the skills were not copied through, so skill tests will fail obscurely"
    assert copied <= installed | bundled
