"""Every knob, and the rules about changing one.

The point of these is that the registry stays honest as it grows: a knob whose help
text doesn't say what goes wrong, or whose default sits outside its own bounds, is
worse than no knob — someone will set it and get a surprise.
"""

from __future__ import annotations

import pytest

from kith.domain.tuning import GROUPS, TUNABLES, for_key
from kith.services import tuning


class TestRegistry:
    """Properties every entry has to hold, checked across all of them at once."""

    def test_keys_and_env_names_are_unique(self):
        keys = [knob.key for knob in TUNABLES]
        envs = [knob.env for knob in TUNABLES]
        assert len(keys) == len(set(keys))
        assert len(envs) == len(set(envs))

    @pytest.mark.parametrize("knob", TUNABLES, ids=lambda k: k.key)
    def test_every_default_is_within_its_own_bounds(self, knob):
        # A default outside its bounds means the first save silently changes the value
        # someone was already running with.
        assert knob.coerce(knob.default) == knob.default

    @pytest.mark.parametrize("knob", TUNABLES, ids=lambda k: k.key)
    def test_every_knob_belongs_to_a_real_group(self, knob):
        assert knob.group in {group.key for group in GROUPS}

    @pytest.mark.parametrize("knob", TUNABLES, ids=lambda k: k.key)
    def test_help_is_an_explanation_not_a_label(self, knob):
        """Long enough to have said something, which is all a test can honestly check.

        Two earlier versions of this tried to verify that the text names a consequence
        — first by looking for words like "too", then by counting sentences. Both
        failed on help that explained itself perfectly well in a single sentence with
        a clause, so both were grading form. Whether the prose is any good is a review
        question, not an assertion.
        """
        assert len(knob.help) > 60, f"{knob.key}: help has to explain, not label"

    @pytest.mark.parametrize("knob", TUNABLES, ids=lambda k: k.key)
    def test_numbers_are_bounded_at_both_ends(self, knob):
        if knob.kind != "text":
            assert knob.minimum is not None and knob.maximum is not None
            assert knob.minimum < knob.maximum

    def test_every_group_has_at_least_one_knob(self):
        used = {knob.group for knob in TUNABLES}
        assert {group.key for group in GROUPS} == used


class TestCoercion:
    def test_out_of_range_clamps_rather_than_refusing(self):
        rounds = for_key("max_rounds")
        assert rounds.coerce(0) == rounds.minimum
        assert rounds.coerce(10_000) == rounds.maximum

    def test_a_float_knob_keeps_its_fraction(self):
        assert for_key("prose_match").coerce("0.45") == 0.45

    def test_an_int_knob_takes_a_numeric_string(self):
        assert for_key("max_rounds").coerce("12") == 12
        assert for_key("max_rounds").coerce(12.7) == 12

    def test_nonsense_is_refused_with_the_label(self):
        with pytest.raises(ValueError, match="Tool rounds per turn"):
            for_key("max_rounds").coerce("soon")

    def test_text_is_trimmed(self):
        assert for_key("timezone").coerce("  Asia/Riyadh ") == "Asia/Riyadh"

    def test_an_unknown_key_names_itself(self):
        with pytest.raises(ValueError, match="mystery"):
            for_key("mystery")


class TestResolution:
    def test_an_untouched_knob_reads_its_default(self):
        assert tuning.value("max_rounds") == for_key("max_rounds").default

    def test_a_saved_value_wins_over_the_default(self):
        tuning.apply({"max_rounds": 12})
        assert tuning.value("max_rounds") == 12

    def test_the_environment_wins_over_a_saved_value(self, monkeypatch):
        tuning.apply({"max_rounds": 12})
        monkeypatch.setenv("KITH_MAX_ROUNDS", "7")
        tuning.reload()
        # An operator who exported this expects it to hold, and expects the UI to say
        # so rather than appearing to accept an edit that never applies.
        assert tuning.value("max_rounds") == 7
        assert tuning.overridden_by_env(for_key("max_rounds"))

    def test_a_typo_in_the_environment_falls_back_rather_than_crashing(self, monkeypatch):
        monkeypatch.setenv("KITH_MAX_ROUNDS", "quite a few")
        tuning.reload()
        assert tuning.value("max_rounds") == for_key("max_rounds").default

    def test_a_saved_value_is_clamped_on_the_way_out_too(self, config_db):
        # Written before a bound was tightened, or by hand. It must not escape the
        # range the code is written to expect.
        from kith.infra.db import config_store

        config_store.update_settings(config_db, {tuning.PREFIX + "max_rounds": 99_999})
        tuning.use_database(config_db)
        assert tuning.value("max_rounds") == for_key("max_rounds").maximum


class TestWriting:
    def test_saving_takes_effect_without_a_restart(self):
        before = tuning.value("idle_interval")
        tuning.apply({"idle_interval": 30})
        # The whole reason these are read live rather than snapshotted at import.
        assert tuning.value("idle_interval") == 30 != before

    def test_a_batch_saves_together(self):
        tuning.apply({"max_rounds": 60, "landing_reserve": 8})
        assert (tuning.value("max_rounds"), tuning.value("landing_reserve")) == (60, 8)

    def test_an_unknown_key_is_refused_and_nothing_is_written(self):
        with pytest.raises(ValueError, match="mystery"):
            tuning.apply({"max_rounds": 33, "mystery": 1})
        assert tuning.value("max_rounds") == for_key("max_rounds").default

    def test_reset_returns_to_the_declared_default(self):
        tuning.apply({"max_rounds": 12})
        tuning.reset(["max_rounds"])
        assert tuning.value("max_rounds") == for_key("max_rounds").default

    def test_reset_removes_the_row_rather_than_storing_the_default(self, isolated_tuning):
        """So "default" keeps meaning whatever the code says today.

        A stored copy would freeze this version's number and quietly diverge the next
        time a default is reconsidered.
        """
        from kith.infra.db import config_store

        tuning.apply({"max_rounds": 12})
        tuning.reset(["max_rounds"])
        assert tuning.PREFIX + "max_rounds" not in config_store.load_settings(isolated_tuning)

    def test_reset_with_no_argument_clears_everything(self):
        tuning.apply({"max_rounds": 12, "min_gap": 9.0})
        tuning.reset()
        assert tuning.value("max_rounds") == for_key("max_rounds").default
        assert tuning.value("min_gap") == for_key("min_gap").default


class TestSnapshot:
    def test_it_carries_the_declarations_so_the_ui_needs_no_copy(self):
        groups = tuning.snapshot()["groups"]
        assert [g["key"] for g in groups] == [g.key for g in GROUPS]
        knob = next(s for g in groups for s in g["settings"] if s["key"] == "max_rounds")
        for field in ("label", "help", "default", "min", "max", "unit", "value", "isDefault"):
            assert field in knob

    def test_it_marks_what_has_been_changed(self):
        tuning.apply({"max_rounds": 12})
        knob = next(s for g in tuning.snapshot()["groups"] for s in g["settings"] if s["key"] == "max_rounds")
        assert knob["value"] == 12
        assert knob["isDefault"] is False

    def test_paths_are_reported_but_not_offered_as_settings(self):
        snapshot = tuning.snapshot()
        assert snapshot["paths"], "someone has to be able to find their data"
        editable = {s["key"] for g in snapshot["groups"] for s in g["settings"]}
        # The databases are already open by the time anyone could change this, so an
        # editable field would be a lie.
        assert "data_dir" not in editable
