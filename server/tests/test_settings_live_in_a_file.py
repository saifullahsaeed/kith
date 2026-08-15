"""Settings are a file, and the database has been released from the duty entirely.

The reason for the move is not tidiness. A file can be fixed when the app will not start, it
can be copied between machines and read in a diff, and Kith can edit it with the file tools he
already has. A row in sqlite can do none of those.

**The rule that makes it worth having: one store.** Two places that both claim to hold a
setting is the shape where somebody edits one, the app reads the other, and there is no way to
tell which is right — which destroys the only thing a settings file is for. So the old rows are
carried across once and then deleted, and nothing on the read or write path touches sqlite
again. The tests below are mostly about that: not "does saving work" but "is there anywhere
left that a second answer could come from".

The migration's *order* is the delicate part and has its own test. The file is written and read
back before a single row is deleted, so a failure anywhere leaves the values where the next
start will find them. Deleting first is the version that loses somebody's configuration.
"""

from __future__ import annotations

import json

import pytest

from kith.domain.tuning import for_key
from kith.services import tuning


@pytest.fixture
def settings_file(tmp_path):
    path = tmp_path / "settings.json"
    tuning.use_file(path)
    yield path
    tuning.use_file(None)


class TestTheFileIsTheStore:
    def test_saving_writes_the_file(self, settings_file):
        tuning.apply({"max_rounds": 12})
        assert json.loads(settings_file.read_text())["max_rounds"] == 12

    def test_only_what_was_changed_is_written(self, settings_file):
        """Not a dump of all thirty-one knobs. What is in the file is what somebody chose,
        which is what makes it readable and what keeps "default" meaning today's number."""
        tuning.apply({"max_rounds": 12})
        stored = json.loads(settings_file.read_text())
        assert [k for k in stored if not k.startswith("/")] == ["max_rounds"]

    def test_the_file_explains_itself(self, settings_file):
        """Somebody opening this in an editor should not have to guess. JSON has no comments,
        so the note is a key — and it is ignored on read like any key that is not a knob."""
        tuning.apply({"max_rounds": 12})
        assert "//" in json.loads(settings_file.read_text())
        assert tuning.value("max_rounds") == 12, "the note does not become a setting"

    def test_resetting_removes_the_key(self, settings_file):
        tuning.apply({"max_rounds": 12})
        tuning.reset(["max_rounds"])
        assert "max_rounds" not in json.loads(settings_file.read_text())
        assert tuning.value("max_rounds") == for_key("max_rounds").default


class TestEditingItByHand:
    def test_an_edit_is_picked_up_without_a_restart(self, settings_file):
        """The cache is keyed on the file's modification time, not merely invalidated by our
        own writes. Editing a config file in another window and watching nothing happen is the
        experience everybody has had once."""
        tuning.apply({"max_rounds": 12})
        assert tuning.value("max_rounds") == 12
        settings_file.write_text(json.dumps({"max_rounds": 7}))
        assert tuning.value("max_rounds") == 7

    def test_a_value_out_of_range_is_clamped(self, settings_file):
        """Hand-editing is the ordinary case now, so a number outside the bounds the code is
        written to expect must not escape into it."""
        settings_file.write_text(json.dumps({"max_rounds": 99_999}))
        assert tuning.value("max_rounds") == for_key("max_rounds").maximum

    def test_a_broken_file_falls_back_to_defaults(self, settings_file, capsys):
        """Refusing to start over a trailing comma is a worse outcome than the documented
        defaults — every value here has one. But it is said out loud, because the symptom
        otherwise is "my setting does nothing"."""
        settings_file.write_text("{ not json,,, }")
        assert tuning.value("max_rounds") == for_key("max_rounds").default
        assert "not readable JSON" in capsys.readouterr().out

    def test_a_file_that_is_not_an_object_falls_back_too(self, settings_file):
        settings_file.write_text("[1, 2, 3]")
        assert tuning.value("max_rounds") == for_key("max_rounds").default

    def test_a_missing_file_is_an_ordinary_condition(self, settings_file):
        """A fresh install has no file, and that is not a problem to report."""
        assert not settings_file.exists()
        assert tuning.value("max_rounds") == for_key("max_rounds").default

    def test_the_environment_still_wins(self, settings_file, monkeypatch):
        tuning.apply({"max_rounds": 12})
        monkeypatch.setenv("KITH_MAX_ROUNDS", "9")
        tuning.reload()
        assert tuning.value("max_rounds") == 9


class TestNothingLeftInTheDatabase:
    def test_reading_a_value_never_opens_the_database(self, settings_file, monkeypatch):
        """The point of the whole change. If a read can still reach sqlite then there are two
        stores, and two stores is the thing this was done to remove."""
        import sqlite3

        def refuse(*_args, **_kwargs):
            raise AssertionError("settings must not touch the database")

        monkeypatch.setattr(sqlite3, "connect", refuse)
        tuning.apply({"max_rounds": 12})
        assert tuning.value("max_rounds") == 12
        tuning.reset(["max_rounds"])
        assert tuning.snapshot()["groups"]

    def test_the_module_names_no_database_outside_the_one_time_move(self):
        """Read as text on purpose. An import added back later would pass every behavioural
        test above and still reintroduce the second store."""
        import inspect

        source = inspect.getsource(tuning)
        body = source.split("def _migrate_from_database")[0]
        for forbidden in ("config_store", "sqlite3", "CONFIG_DB_PATH"):
            assert forbidden not in body, f"{forbidden} is back on the live path"


class TestCarryingAnOldInstallAcross:
    @pytest.fixture
    def old_database(self, tmp_path, monkeypatch):
        from kith import settings as module
        from kith.infra.db import config_store

        db = tmp_path / "config.db"
        config_store.init(db)
        config_store.update_settings(db, {"tune.max_rounds": 12, "tune.max_parallel": 3, "model": "keep-me"})
        monkeypatch.setattr(module, "CONFIG_DB_PATH", db, raising=False)
        return db

    def test_old_values_are_moved_into_the_file(self, old_database, settings_file):
        assert tuning._migrate_from_database() == 2
        assert json.loads(settings_file.read_text())["max_rounds"] == 12
        assert tuning.value("max_parallel") == 3

    def test_the_old_rows_are_deleted(self, old_database, settings_file):
        """Left behind they would be a second answer waiting to be read by something."""
        from kith.infra.db import config_store

        tuning._migrate_from_database()
        assert not [k for k in config_store.load_settings(old_database) if k.startswith("tune.")]

    def test_what_was_never_ours_is_left_alone(self, old_database, settings_file):
        """The database keeps chat config. It lost the settings duty, not every duty."""
        from kith.infra.db import config_store

        tuning._migrate_from_database()
        assert config_store.load_settings(old_database)["model"] == "keep-me"

    def test_it_runs_once(self, old_database, settings_file):
        assert tuning._migrate_from_database() == 2
        assert tuning._migrate_from_database() == 0

    def test_an_existing_file_is_never_overwritten(self, old_database, settings_file):
        """Whatever is in the file now is the truth, and a stale row must not replace it."""
        settings_file.write_text(json.dumps({"max_rounds": 33}))
        assert tuning._migrate_from_database() == 0
        assert tuning.value("max_rounds") == 33

    def test_nothing_is_deleted_if_the_file_cannot_be_written(self, old_database, settings_file, monkeypatch):
        """The order is the whole care here. A failure must leave the values where the next
        start will find them again."""
        from kith.infra.db import config_store

        monkeypatch.setattr(tuning, "_write", lambda _values: None)  # writes nothing
        assert tuning._migrate_from_database() == 0
        remaining = config_store.load_settings(old_database)
        assert remaining["tune.max_rounds"] == 12, "still recoverable"

    def test_a_fresh_install_has_nothing_to_move(self, settings_file, tmp_path, monkeypatch):
        from kith import settings as module

        monkeypatch.setattr(module, "CONFIG_DB_PATH", tmp_path / "nope.db", raising=False)
        assert tuning._migrate_from_database() == 0


class TestTheUiCanFindIt:
    def test_the_file_is_listed_among_the_paths(self, settings_file):
        """Being able to find it is most of the point — it is the copy to reach for when the
        app will not start."""
        listed = tuning.snapshot()["paths"]
        assert any(str(settings_file) == one["value"] for one in listed), listed

    def test_it_exists_from_the_first_launch(self, settings_file):
        """A file nobody has created is a file nobody can edit — and the Reveal button on the
        settings page refuses a path that is not there, so on a fresh install the button would
        have failed until somebody happened to change a setting."""
        assert not settings_file.exists()
        assert tuning.ensure_exists() == settings_file
        assert settings_file.exists()

    def test_creating_it_does_not_freeze_todays_defaults(self, settings_file):
        """Writing all thirty-one knobs out would pin this version's numbers into somebody's
        file and diverge the next time one is reconsidered."""
        tuning.ensure_exists()
        stored = json.loads(settings_file.read_text())
        assert [k for k in stored if not k.startswith("/")] == []
        assert tuning.value("max_rounds") == for_key("max_rounds").default

    def test_creating_it_never_disturbs_one_that_exists(self, settings_file):
        tuning.apply({"max_rounds": 12})
        tuning.ensure_exists()
        assert tuning.value("max_rounds") == 12
