"""Who he is has to survive quitting, updating, and being edited from either side.

A packaged Kith unpacks itself into a temporary directory that the operating system deletes
when the app closes, and the persona folder was read from — and written to — inside it. So the
Settings editor worked exactly once: it saved, it said "Saved", the page reloaded with the new
text, and the file was gone by the next launch. Silent and total, every single time, and
invisible from a checkout, where that same path is the repo's own `persona/` and persists fine.
The databases had already been moved out of the bundle for precisely this reason; the persona
was left behind.

These tests are the frozen case, which no developer runs into and every downloaded copy does.
"""

from __future__ import annotations

import shutil
import sys
from importlib import reload
from pathlib import Path

import pytest


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    """A Kith that thinks it is a frozen bundle, with a bundle we can delete under it."""
    bundle = tmp_path / "meipass"
    data = tmp_path / "home" / ".kith"
    (bundle / "persona").mkdir(parents=True)
    (bundle / "persona" / "00-who.md").write_text("<!-- a note -->\nYour name is Kith.\n")
    (bundle / "persona" / "10-tone.md").write_text("Be brief.\n")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("KITH_DATA_DIR", str(data))
    monkeypatch.delenv("KITH_PERSONA_DIR", raising=False)

    from kith import settings

    reload(settings)
    from kith.services import persona

    reload(persona)
    persona._seeded = False

    yield bundle, data, persona

    # Leave the modules describing the real world again, or every test after this one is
    # frozen. `monkeypatch.undo()` first so the reload reads the true environment.
    monkeypatch.undo()
    reload(settings)
    reload(persona)
    persona._seeded = False


def test_the_bundled_persona_is_a_seed_not_the_live_copy(packaged):
    _bundle, data, persona = packaged

    assert not (data / "persona").exists(), "nothing should be written before he is asked"
    assert persona.load_persona() == "Your name is Kith.\n\nBe brief."
    assert (data / "persona" / "00-who.md").exists(), "reading should have seeded the data dir"
    assert persona.persona_dir() == data / "persona"


def test_an_edit_outlives_the_bundle_it_shipped_in(packaged):
    bundle, _data, persona = packaged

    persona.load_persona()
    persona.write_fragment("00-who.md", "Your name is Atlas.\n")
    persona.set_enabled("10-tone.md", False)

    # What the operating system does to _MEIPASS when the app quits.
    shutil.rmtree(bundle)
    persona._seeded = False

    assert persona.load_persona() == "Your name is Atlas."
    assert {one["name"] for one in persona.fragments()} == {"00-who.md", "_10-tone.md"}


def test_an_update_does_not_overwrite_what_someone_wrote(packaged):
    bundle, data, persona = packaged

    persona.load_persona()
    persona.write_fragment("00-who.md", "Your name is Atlas.\n")

    # A new version of the app, carrying different bundled fragments.
    (bundle / "persona" / "00-who.md").write_text("Your name is Kith, version two.\n")
    (bundle / "persona" / "20-new-in-this-release.md").write_text("Something new.\n")
    persona._seeded = False

    assert "Atlas" in persona.load_persona(), "an update must not replace their persona"
    assert not (data / "persona" / "20-new-in-this-release.md").exists()


def test_deleting_every_fragment_is_a_decision_not_a_gap(packaged):
    """Seeding keys on the folder being absent, not on it being empty."""
    _bundle, _data, persona = packaged

    persona.load_persona()
    for fragment in persona.fragments():
        persona.delete_fragment(fragment["name"])
    persona._seeded = False

    assert persona.load_persona() == "", "an empty persona is an answer, not a missing one"


def test_saving_works_when_the_folder_is_behind_a_symlink(tmp_path, monkeypatch):
    """`_resolve` resolves symlinks and `_describe` did not, so every save 500'd.

    Unreachable while the persona could only ever be the repo's own folder. It stopped being
    unreachable the moment the frozen build started keeping it in the data directory, and it
    is reachable today for anyone whose home directory is redirected — or who points
    `KITH_PERSONA_DIR` at anything under `/tmp` on a Mac.
    """
    from kith import settings
    from kith.services import persona

    real = tmp_path / "real"
    real.mkdir()
    (real / "00-who.md").write_text("Your name is Kith.\n")
    link = tmp_path / "through-a-link"
    link.symlink_to(real)

    # Both modules. `PERSONA_DIR` is read from the environment when `settings` is imported,
    # so reloading only the service leaves it pointed at the folder it already had — which is
    # how an earlier version of this test wrote "Your name is Atlas." into the real persona.
    monkeypatch.setenv("KITH_PERSONA_DIR", str(link))
    reload(settings)
    reload(persona)

    written = persona.write_fragment("00-who.md", "Your name is Atlas.\n")
    assert written["name"] == "00-who.md"
    assert (real / "00-who.md").read_text() == "Your name is Atlas.\n"


def test_the_suite_cannot_reach_the_shipped_persona():
    """The guard in `conftest.never_the_real_persona`, asserted rather than assumed.

    Written after this very file wrote "Your name is Atlas." into `server/persona/00-who.md`
    and the suite went green. A guard nothing checks is a guard that quietly stops working.
    """
    from kith.services import persona

    live = persona.persona_dir().resolve()
    shipped = (Path(__file__).parent.parent / "persona").resolve()
    assert live != shipped, "a test is one reload away from editing the product"
    assert persona.fragments(), "but it should still look like a real persona"


@pytest.fixture
def packaged_with_skills(tmp_path, monkeypatch):
    """A frozen bundle that carries both the persona and the default skills."""
    bundle = tmp_path / "meipass"
    data = tmp_path / "home" / ".kith"
    (bundle / "persona").mkdir(parents=True)
    (bundle / "persona" / "00-who.md").write_text("Your name is Kith.\n")
    canvas = bundle / "skills" / "drawing-a-canvas"
    canvas.mkdir(parents=True)
    (canvas / "SKILL.md").write_text(
        '---\nname: drawing-a-canvas\ndescription: "How to draw one."\n---\n\nInstructions.\n'
    )

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("KITH_DATA_DIR", str(data))
    monkeypatch.delenv("KITH_SKILLS_DIR", raising=False)

    from kith import settings

    reload(settings)
    from kith.services import skills

    reload(skills)
    skills._seeded = False

    yield data, skills

    monkeypatch.undo()
    reload(settings)
    reload(skills)
    skills._seeded = False


def test_the_skill_the_persona_names_is_in_the_box(packaged_with_skills):
    """`25-what-you-draw.md` says "the `drawing-a-canvas` skill has the rest of the syntax".

    That sentence was written on a machine with twenty skills installed, and shipped to people
    with none. Nothing failed — he simply pointed at something that was not there.
    """
    data, skills = packaged_with_skills

    assert [one.name for one in skills.installed()] == ["drawing-a-canvas"]
    assert not skills.problems(), "a skill we ship has to parse"
    assert (data / "skills" / "drawing-a-canvas" / "SKILL.md").exists()


def test_removing_a_bundled_skill_removes_it(packaged_with_skills):
    """Seeding is a first run, not a managed set — an update must not put it back."""
    data, skills = packaged_with_skills

    skills.root()
    shutil.rmtree(data / "skills" / "drawing-a-canvas")
    skills._seeded = False

    assert skills.installed() == [], "it came back after they threw it away"


# --------------------------------------------------------------------------- #
# The box itself
# --------------------------------------------------------------------------- #
#
# The two tests above build a synthetic bundle, which is right for what they check — seeding is
# a first run, not a managed set — but it means nothing at all reads the folder we actually
# ship. `server/skills/` was one skill for as long as that was true by accident; it is a real
# set now, and the failure it can have is the quiet one: a skill goes in the box, does not
# parse, and every downloaded copy silently has one fewer capability than the release notes say.


def _shipped() -> Path:
    return (Path(__file__).parent.parent / "skills").resolve()


@pytest.fixture
def shipped_skills(tmp_path, monkeypatch):
    """The real bundled folder, read through the service, seeded into a throwaway directory."""
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))

    from kith.services import skills

    reload(skills)
    skills._seeded = False
    monkeypatch.setattr(skills.settings, "BUNDLED_SKILLS_DIR", _shipped())

    yield skills

    monkeypatch.undo()
    reload(skills)
    skills._seeded = False


def test_every_skill_we_ship_parses(shipped_skills):
    """A skill in the box that does not load is worse than one that is not in the box.

    It is invisible from a checkout — a developer has all of theirs installed and never reads
    the seeded copy — and there is nothing to see at runtime either, because `installed()`
    skips a broken folder rather than failing loudly.
    """
    skills = shipped_skills
    folders = sorted(one.name for one in _shipped().iterdir() if one.is_dir())
    assert folders, "the bundle is empty; nothing would be seeded on a first run"

    assert sorted(one.name for one in skills.installed()) == folders
    assert not skills.problems()

    for name in folders:
        hard = [f for f in skills.validate(_shipped() / name) if "(Warning only.)" not in f]
        assert not hard, f"{name}: {hard}"


def test_the_persona_only_names_skills_that_are_in_the_box(shipped_skills):
    """The bug the bundle exists to close, checked against the persona rather than a list.

    `25-what-you-draw.md` says "the `drawing-a-canvas` skill has the rest of the syntax". That
    sentence was written on a machine with twenty skills installed and shipped to people with
    none. Hardcoding the one name here would pass forever and check nothing the next time
    someone writes the same sentence about a different skill.
    """
    import re

    from kith.services import persona

    installed = {one.name for one in shipped_skills.installed()}
    text = "\n".join(fragment["body"] for fragment in persona.fragments())
    named = {match.group(1) for match in re.finditer(r"`([a-z0-9]+(?:-[a-z0-9]+){1,})`\s+skill", text)}
    assert named, "the persona names no skill at all — has the sentence moved?"
    assert named <= installed, f"the persona points at {named - installed}, which nobody has"


def test_the_shipped_index_stays_small(shipped_skills):
    """What the bundle costs every request, on every install, forever.

    Each description sits in the cached prefix (see `llm/caching`), so this is read-price after
    the first request of a session — but it is paid by everyone, including someone who never
    uses either skill. The ceiling is here so that adding a third is a decision with a number
    attached rather than a folder someone dropped in.
    """
    assert shipped_skills.snapshot()["indexTokens"] < 600
