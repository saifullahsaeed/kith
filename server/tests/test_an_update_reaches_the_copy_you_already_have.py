"""A correction is only a correction if it arrives.

`infra/seed.py` exists because two true things were in tension and one of them lost silently.
An update must not overwrite a persona somebody has spent a month shaping — that was enforced,
by copying only into a folder that was absent. And an update must be able to carry a fix — that
was not enforced at all, and nothing said so. `running-a-project` named four tools that no
longer existed; the fix shipped to new installs only, which is everybody except the people
holding the broken copy.

These tests are about the file nobody has touched. The ones about the file somebody *has*
touched live in `test_his_persona_outlives_the_app.py`, and both halves have to hold at once or
the mechanism is worse than the rule it replaced.
"""

from __future__ import annotations

import hashlib
import json
import sys
from importlib import reload

import pytest

V_ONE = b"Read the file before you change it.\n"
V_TWO = b"Read the file before you change it, and the diff after.\n"
THEIRS = b"Read whatever you like.\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def upgrading(tmp_path, monkeypatch):
    """A machine that already has Kith, with a data directory a previous release seeded.

    Deliberately built the way a real one is: files on disk and *no manifest*, because the
    release that introduces the manifest is the one that has to reach everybody who installed
    before it existed. A mechanism that only works from its own second release onwards would
    have missed the change it was written for.
    """
    bundle = tmp_path / "meipass"
    data = tmp_path / "home" / ".kith"
    (bundle / "persona").mkdir(parents=True)
    (data / "persona").mkdir(parents=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("KITH_DATA_DIR", str(data))
    monkeypatch.delenv("KITH_PERSONA_DIR", raising=False)

    from kith import settings

    reload(settings)
    from kith.infra import seed

    reload(seed)
    from kith.services import persona

    reload(persona)
    persona._seeded = False

    def release(shipped: bytes, history: dict[str, list[str]]) -> None:
        (bundle / "persona" / "30-how-you-read.md").write_bytes(shipped)
        (bundle / "shipped.json").write_text(json.dumps({"tags": ["v0.5.2"], "paths": history}))
        persona._seeded = False

    yield bundle, data, persona, release

    monkeypatch.undo()
    reload(settings)
    reload(seed)
    reload(persona)
    persona._seeded = False


def test_an_untouched_fragment_is_updated_even_with_no_manifest(upgrading):
    """The bootstrap case, and the only one that mattered on the day this was written.

    Nothing on the machine records where those bytes came from. What identifies them is that
    *we* published them: a file whose contents are byte-for-byte a version Kith has released is
    nobody's work by definition, whoever's disk it is sitting on.
    """
    _bundle, data, persona, release = upgrading
    live = data / "persona" / "30-how-you-read.md"
    live.write_bytes(V_ONE)

    release(V_TWO, {"persona/30-how-you-read.md": [sha(V_ONE)]})
    persona.load_persona()

    assert live.read_bytes() == V_TWO


def test_a_fragment_they_rewrote_is_left_alone_with_no_manifest(upgrading):
    """The same bootstrap, and it has to fail closed.

    Bytes that match nothing Kith has published are somebody's work, and the only safe reading
    of "I do not recognise this" is "do not touch it". Getting this wrong destroys writing that
    exists in one place, on launch, with nothing on screen to say it happened.
    """
    _bundle, data, persona, release = upgrading
    live = data / "persona" / "30-how-you-read.md"
    live.write_bytes(THEIRS)

    release(V_TWO, {"persona/30-how-you-read.md": [sha(V_ONE)]})
    persona.load_persona()

    assert live.read_bytes() == THEIRS


def test_a_fragment_edited_back_to_what_shipped_is_not_treated_as_an_edit(upgrading):
    """Somebody who undoes their own change is holding our bytes again, and should resume
    receiving updates. Recording the edit rather than the file would have made that permanent."""
    _bundle, data, persona, release = upgrading
    live = data / "persona" / "30-how-you-read.md"
    live.write_bytes(THEIRS)

    release(V_ONE, {"persona/30-how-you-read.md": [sha(V_ONE)]})
    persona.load_persona()
    assert live.read_bytes() == THEIRS

    live.write_bytes(V_ONE)
    release(V_TWO, {"persona/30-how-you-read.md": [sha(V_ONE)]})
    persona.load_persona()

    assert live.read_bytes() == V_TWO


def test_provenance_is_earned_by_a_file_that_already_matches(upgrading):
    """Somebody on the current version gets a manifest entry without anything being written,
    which is what lets the *next* release update them once `shipped.json` has moved on."""
    _bundle, data, persona, release = upgrading
    live = data / "persona" / "30-how-you-read.md"
    live.write_bytes(V_ONE)

    release(V_ONE, {})  # no history at all; the match is against what is being shipped now
    persona.load_persona()

    recorded = json.loads((data / "seeded.json").read_text())["paths"]
    assert recorded["persona/30-how-you-read.md"] == sha(V_ONE)

    release(V_TWO, {})
    persona.load_persona()
    assert live.read_bytes() == V_TWO, "the manifest written last time should have carried this"


def test_a_missing_history_file_costs_delivery_and_never_costs_writing(upgrading):
    """How this is allowed to fail. A build that forgets to carry `shipped.json` recognises
    nothing, so it delivers nothing and overwrites nothing — the behaviour it replaced."""
    bundle, data, persona, release = upgrading
    live = data / "persona" / "30-how-you-read.md"
    live.write_bytes(V_ONE)

    release(V_TWO, {"persona/30-how-you-read.md": [sha(V_ONE)]})
    (bundle / "shipped.json").unlink()
    persona.load_persona()

    assert live.read_bytes() == V_ONE, "no history means recognise nothing, not overwrite"


def test_the_shipped_history_we_actually_ship_is_readable():
    """The file itself, in this repository, as the server will read it."""
    from kith import settings

    loaded = json.loads(settings.SHIPPED_HISTORY_PATH.read_text(encoding="utf-8"))
    paths = loaded["paths"]

    assert loaded["tags"], "a history with no releases in it recognises nothing"
    assert paths, "an empty history is the same as a missing one"
    for key, hashes in paths.items():
        assert key.startswith(("persona/", "skills/")), key
        assert hashes, key
        for one in hashes:
            assert len(one) == 64 and set(one) <= set("0123456789abcdef"), (key, one)
