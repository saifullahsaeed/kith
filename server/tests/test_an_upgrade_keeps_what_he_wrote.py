"""What happens to a plugin's skills when the plugin changes, and when it goes.

A plugin's skills are **copied** into the person's own skills folder rather than read in place —
see the note over `_import_skills` for why, and it is a boundary rather than a preference. The
price of copying is that two questions now have to be answered by hand, and neither of them was:

**On an upgrade**, the copy on disk may have the person's edits in it. `_on_upgrade` was an
unfinished stub returning `"replace"` unconditionally, so every version bump `rmtree`'d their
writing — in a codebase whose rule everywhere else is that a folder of someone's writing is
trashed, never destroyed. The whole `_digest_of` mechanism built to answer this was dead code.

**On an uninstall**, the copies do not disappear on their own. `_withdraw_skills` exists to take
them back out, and the comment over the call says it must run "before the row goes, since that is
where the list of what was imported lives" — while the code ran it *after* `write_rows` had
already popped the row. With `delete_state=True` it therefore read an empty dict and withdrew
nothing, leaving instructions in the prompt for tools that had just been uninstalled. That is
the exact failure `_withdraw_skills`'s own docstring is written to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.infra import permissions
from kith.services import skills as skills_service
from kith.services.plugins import install as installer
from kith.services.plugins import registry

MANIFEST = {
    "manifest": 1,
    "id": "circulars",
    "name": "Circular Watch",
    "version": "0.1.0",
    "description": "Watches the regulator and files what it finds.",
}
SKILL = "reading-circulars"


@pytest.fixture(autouse=True)
def somewhere_to_install(tmp_path: Path, config_db: Path, monkeypatch):
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    registry.forget_cache()
    permissions.revoke_all()
    yield
    registry.forget_cache()
    permissions.revoke_all()


def a_plugin(tmp_path: Path, *, version: str = "0.1.0", skill_says: str = "read the PDF") -> Path:
    source = tmp_path / "source" / MANIFEST["id"]
    (source / "skills" / SKILL).mkdir(parents=True, exist_ok=True)
    (source / "kith.plugin.json").write_text(json.dumps({**MANIFEST, "version": version, "skills": [SKILL]}))
    (source / "skills" / SKILL / "SKILL.md").write_text(
        f"---\nname: {SKILL}\ndescription: How to read one.\n---\n\n{skill_says}\n"
    )
    return source


def their_copy() -> Path:
    return skills_service.root() / SKILL / "SKILL.md"


def _a_fake_trash(monkeypatch, tmp_path: Path) -> list[Path]:
    """A Trash that records *and actually moves*, because the caller copies over the gap."""
    import shutil

    trashed: list[Path] = []
    bin_ = tmp_path / "trash"
    bin_.mkdir(exist_ok=True)

    def trash(path):
        path = Path(path)
        trashed.append(path)
        shutil.move(str(path), str(bin_ / f"{path.name}-{len(trashed)}"))

    monkeypatch.setattr("kith.infra.workspace.trash_path", trash)
    return trashed


# --------------------------------------------------------------------------- #
# The decision itself, as a table. No filesystem in it.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "on_disk, incoming, installed, expected, why",
    [
        ("aaa", "bbb", "aaa", "replace", "untouched since install, so nothing of theirs is in it"),
        ("ccc", "aaa", "aaa", "keep", "they edited it and the plugin shipped no change"),
        ("ccc", "bbb", "aaa", "replace", "both moved — the skill has to stay true, and the caller trashes"),
        ("ccc", "bbb", "", "keep", "no baseline, so an edit cannot be ruled out"),
        ("aaa", "aaa", "aaa", "replace", "nothing moved at all; replacing is a no-op"),
    ],
)
def test_the_upgrade_decision(on_disk, incoming, installed, expected, why):
    assert installer._on_upgrade(on_disk, incoming, installed) == expected, why


# --------------------------------------------------------------------------- #
# And what it does on disk
# --------------------------------------------------------------------------- #


def test_an_untouched_skill_is_upgraded(tmp_path: Path, config_db: Path):
    installer.install(config_db, a_plugin(tmp_path))
    installer.install(config_db, a_plugin(tmp_path, version="0.2.0", skill_says="read the XML"))

    assert "read the XML" in their_copy().read_text()


def test_a_skill_he_edited_survives_a_version_that_did_not_touch_it(tmp_path: Path, config_db: Path):
    """The plugin shipped the same skill again. There is nothing to upgrade *to*."""
    installer.install(config_db, a_plugin(tmp_path))
    their_copy().write_text("---\nname: x\ndescription: mine.\n---\n\nmy own notes\n")

    installer.install(config_db, a_plugin(tmp_path, version="0.2.0"))

    assert "my own notes" in their_copy().read_text()


def test_an_edit_is_moved_aside_rather_than_destroyed(tmp_path: Path, config_db: Path, monkeypatch):
    """When both moved, the new skill wins — and their copy goes to the Trash, not to nothing.

    This is the half that makes "replace" defensible. A skill describing tools that no longer
    exist is worse than a lost edit; an edit that is *recoverable* is not a lost edit at all.
    """
    trashed = _a_fake_trash(monkeypatch, tmp_path)

    installer.install(config_db, a_plugin(tmp_path))
    their_copy().write_text("---\nname: x\ndescription: mine.\n---\n\nmy own notes\n")
    installer.install(config_db, a_plugin(tmp_path, version="0.2.0", skill_says="read the XML"))

    assert "read the XML" in their_copy().read_text()
    assert [one.name for one in trashed] == [SKILL]


def test_an_untouched_copy_is_not_worth_trashing(tmp_path: Path, config_db: Path, monkeypatch):
    """The other side of it: a byte-identical copy is a cache, and filling the Trash with caches
    on every version bump is how somebody stops looking in it."""
    trashed = _a_fake_trash(monkeypatch, tmp_path)

    installer.install(config_db, a_plugin(tmp_path))
    installer.install(config_db, a_plugin(tmp_path, version="0.2.0", skill_says="read the XML"))

    assert trashed == []


# --------------------------------------------------------------------------- #
# Uninstall
# --------------------------------------------------------------------------- #


def test_removing_a_plugin_takes_its_skills_out_of_his_prompt(tmp_path: Path, config_db: Path):
    installer.install(config_db, a_plugin(tmp_path))
    assert their_copy().is_dir() or their_copy().is_file()

    installer.uninstall(config_db, MANIFEST["id"])

    assert not (skills_service.root() / SKILL).exists()


def test_removing_it_and_its_data_takes_its_skills_too(tmp_path: Path, config_db: Path):
    """**The bug.** `delete_state=True` popped the row first, so the withdrawal read nothing.

    The one path that destroys the most was the one that left the most behind: every imported
    skill stayed in the person's folder for good, describing tools that were gone.
    """
    installer.install(config_db, a_plugin(tmp_path))

    installer.uninstall(config_db, MANIFEST["id"], delete_state=True)

    assert not (skills_service.root() / SKILL).exists()


# --------------------------------------------------------------------------- #
# The ceiling
# --------------------------------------------------------------------------- #


def a_plugin_with(tmp_path: Path, *, plugin_id: str, commands: int, version="0.1.0") -> Path:
    source = tmp_path / "source" / plugin_id
    source.mkdir(parents=True, exist_ok=True)
    source.joinpath("kith.plugin.json").write_text(
        json.dumps(
            {
                **MANIFEST,
                "id": plugin_id,
                "version": version,
                "skills": [],
                "commands": [
                    {
                        "name": f"do_{n}",
                        "title": f"Do {n}",
                        "delivery": "state",
                        "does": {"set": ["last"]},
                        "description": "x" * 200,
                        "model": True,
                    }
                    for n in range(commands)
                ],
            }
        )
    )
    return source


def test_an_upgrade_is_measured_against_the_ceiling_too(tmp_path: Path, config_db: Path, monkeypatch):
    """**The bug.** The check was guarded by `already is None`, so it bound the first install of
    a plugin and nothing afterwards — a plugin could install with one command and add five in a
    version bump that the only screen quoting a number waved through.

    The ceiling is moved rather than the plugin made enormous, so this asserts the guard rather
    than a byte count that goes stale the next time a schema gains a field.
    """
    from kith.domain.plugins import PluginError

    one = a_plugin_with(tmp_path, plugin_id="cheap", commands=1)
    monkeypatch.setattr(installer, "MAX_INSTALLED_PROMPT_CHARS", 10_000)
    installer.install(config_db, one)

    cost = registry.installed_prompt_chars(config_db)
    monkeypatch.setattr(installer, "MAX_INSTALLED_PROMPT_CHARS", cost + 10)

    with pytest.raises(PluginError, match="over the"):
        installer.install(config_db, a_plugin_with(tmp_path, plugin_id="cheap", commands=5, version="0.2.0"))


def test_an_upgrade_is_not_charged_twice_for_what_it_already_costs(
    tmp_path: Path, config_db: Path, monkeypatch
):
    """Its current contribution comes off the total first. Without that, a plugin sitting at the
    ceiling could never be upgraded at all — not even to a version costing exactly the same."""
    monkeypatch.setattr(installer, "MAX_INSTALLED_PROMPT_CHARS", 10_000)
    installer.install(config_db, a_plugin_with(tmp_path, plugin_id="chunky", commands=4))

    monkeypatch.setattr(installer, "MAX_INSTALLED_PROMPT_CHARS", registry.installed_prompt_chars(config_db))
    installer.install(config_db, a_plugin_with(tmp_path, plugin_id="chunky", commands=4, version="0.2.0"))

    assert (registry.row(config_db, "chunky") or {})["version"] == "0.2.0"
