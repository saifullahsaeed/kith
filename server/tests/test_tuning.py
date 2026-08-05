"""Every knob, and the rules about changing one.

The point of these is that the registry stays honest as it grows: a knob whose help
text doesn't say what goes wrong, or whose default sits outside its own bounds, is
worse than no knob — someone will set it and get a surprise.
"""

from __future__ import annotations

import re

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
        if knob.kind in ("int", "float"):
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
        with pytest.raises(ValueError, match="Tool rounds per message"):
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
        # Was idle_interval, which no longer exists: it was the 600-second backoff for
        # roaming over an empty board, and roaming is gone. min_gap is the same kind of knob
        # — a live-read number the loop consults every second — so it tests the same thing.
        before = tuning.value("min_gap")
        tuning.apply({"min_gap": 7})
        # The whole reason these are read live rather than snapshotted at import.
        assert tuning.value("min_gap") == 7 != before

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


class TestBooleans:
    def test_a_switch_reads_a_real_boolean(self):
        knob = for_key("stop_after_delegating")
        assert knob.coerce(True) is True
        assert knob.coerce(False) is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_the_environment_can_only_send_strings(self, raw):
        assert for_key("stop_after_delegating").coerce(raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
    def test_and_the_off_spellings(self, raw):
        assert for_key("stop_after_delegating").coerce(raw) is False

    def test_it_defaults_to_on(self):
        # The behaviour it prevents is the one that was actually observed.
        assert tuning.value("stop_after_delegating") is True


class TestAKnobWithAFixedSetOfAnswers:
    """A free-text box for a setting with three legal values is a spelling test.

    And the failure was silent: OpenRouter ignores an unknown `sort`, so `prefer_provider_by`
    saved as "pirce", displayed back exactly as typed, and did nothing at all — a setting that
    looks applied and is not. Refusing is right here, unlike the numeric knobs, which clamp: a
    number out of range has a nearest legal value that is obviously what was meant, and a
    misspelt word does not.
    """

    def test_a_legal_value_passes(self):
        assert for_key("prefer_provider_by").coerce("throughput") == "throughput"

    def test_an_illegal_value_is_refused_rather_than_ignored(self):
        with pytest.raises(ValueError) as raised:
            for_key("prefer_provider_by").coerce("pirce")
        # The message has to list what *is* allowed; "invalid" sends someone to the source.
        assert "price" in str(raised.value)
        assert "pirce" in str(raised.value)

    def test_blank_is_legal_where_blank_means_something(self):
        # "Leave it to OpenRouter's own balancing" is a real answer, and omitting it from the
        # list would make it unreachable from the interface.
        assert for_key("prefer_provider_by").coerce("") == ""

    def test_blank_is_refused_where_it_means_nothing(self):
        with pytest.raises(ValueError):
            for_key("search_engine").coerce("")

    def test_surrounding_space_is_forgiven(self):
        assert for_key("search_engine").coerce("  native  ") == "native"

    @pytest.mark.parametrize(
        "knob", [k for k in TUNABLES if k.choices], ids=lambda k: k.key
    )
    def test_choices_only_ever_sit_on_a_text_knob(self, knob):
        # A number with an enum is a number with the wrong `kind`, and the interface would
        # render a select full of digits.
        assert knob.kind == "text"

    @pytest.mark.parametrize(
        "knob", [k for k in TUNABLES if k.choices], ids=lambda k: k.key
    )
    def test_the_default_is_one_of_them(self, knob):
        # Otherwise the first save refuses the value the person was already running with.
        assert knob.default in knob.choices

    def test_they_reach_the_interface(self):
        # The whole point — the client renders a select from this, and falls back to a free-text
        # box when it is absent, which is what an older server sends.
        assert for_key("prefer_provider_by").public()["choices"] == [
            "price",
            "throughput",
            "latency",
            "",
        ]

    def test_a_free_text_knob_says_so_with_an_empty_list(self):
        # Not `None`: the client reads `.length`, and a missing key took the settings page down
        # once already.
        assert for_key("ollama_host").public()["choices"] == []


#: Tunables where a shared, cross-mode "turn" is accurate rather than a labelling drift — see
#: `TestOneWordPerConcept`. Module-level because a class body's decorators run before the class
#: object exists, so `TestOneWordPerConcept._SHARED_TURN_OK` is not yet reachable from inside its
#: own `@pytest.mark.parametrize` calls. Checked by key so adding a new shared tunable requires a
#: deliberate choice here, not a silent pass.
_SHARED_TURN_OK = {"landing_reserve", "live_tool_chars", "mcp_call_timeout"}


class TestOneWordPerConcept:
    """Two concepts, each with one name — checked so a future entry can't drift back apart.

    The settings page used "turn" and "chat message" for the same thing depending which
    tunable you were reading (`max_rounds` said "Tool rounds per **turn**", right next to
    `history_keep_recent` saying "Recent messages kept verbatim" with help text that already
    said "messages" — the label and its own help text disagreed within one entry). And it
    used "tick" and "step" for the unattended unit depending which tunable you were reading —
    `task_tick_cap` said "Most **ticks** one task may take" while `tick_max_rounds` right above
    it said "Tool rounds per unattended **step**". The Work Panel — the surface a person looks
    at every session — only ever says "step", never "tick", so that is the word that won.

    "Turn" survives in a few places on purpose: `landing_reserve`, `live_tool_chars` and
    `mcp_call_timeout` describe something genuinely shared between a chat message and an
    unattended step, and "turn" is the correct umbrella word for that — swapping it for
    "message" or "step" there would make the text specific to one mode when it is not. What
    is checked here is the failure that actually happened: the *same concept* wearing two
    names depending which entry you happened to be reading.
    """

    #: `tick`/`ticks` as a plain-English noun for the autonomous unit. Excludes the checkbox
    #: sense ("ticked off") and the verb sense ("turn into") by requiring the exact plural or
    #: bare noun forms that only ever meant the unit in this file.
    _NOUN_TICK = re.compile(r"\bticks?\b(?!\s+off)")
    #: `turn`/`turns` as a noun for one exchange. Excludes "turn into", "turn on/off", "in turn" —
    #: but only when the next word is literally "into"/"on"/"off"; "turn his loop into a spiral"
    #: still matches, because English lets the object sit between the verb and its particle. That
    #: false positive is a feature, not a gap: it happened once (`min_gap`), and the fix was to
    #: reword the help text away from the phrasal verb rather than chase every shape a sentence
    #: can take. A test that fails loudly on an ambiguous case is doing its job.
    _NOUN_TURN = re.compile(r"\bturns?\b(?!\s+(?:into|on|off))")

    @pytest.mark.parametrize("knob", TUNABLES, ids=lambda k: k.key)
    def test_no_label_says_tick_for_the_unattended_unit(self, knob):
        assert not self._NOUN_TICK.search(knob.label), (
            f"{knob.key}: label {knob.label!r} says 'tick' — the established word is 'step'"
        )

    @pytest.mark.parametrize("knob", TUNABLES, ids=lambda k: k.key)
    def test_no_help_text_says_tick_for_the_unattended_unit(self, knob):
        assert not self._NOUN_TICK.search(knob.help), (
            f"{knob.key}: help text says 'tick(s)' — the established word is 'step'"
        )

    @pytest.mark.parametrize("knob", TUNABLES, ids=lambda k: k.key)
    def test_no_unit_says_ticks(self, knob):
        assert knob.unit != "ticks", f"{knob.key}: unit is 'ticks' — should be 'steps'"

    @pytest.mark.parametrize(
        "knob", [k for k in TUNABLES if k.key not in _SHARED_TURN_OK],
        ids=lambda k: k.key,
    )
    def test_no_label_says_turn_outside_the_shared_cases(self, knob):
        assert not self._NOUN_TURN.search(knob.label), (
            f"{knob.key}: label {knob.label!r} says 'turn' — say 'message' (chat) or 'step' (a "
            "tick), or add this key to _SHARED_TURN_OK if it is genuinely about both"
        )

    @pytest.mark.parametrize(
        "knob", [k for k in TUNABLES if k.key not in _SHARED_TURN_OK],
        ids=lambda k: k.key,
    )
    def test_no_help_text_says_turn_outside_the_shared_cases(self, knob):
        assert not self._NOUN_TURN.search(knob.help), (
            f"{knob.key}: help text says 'turn' — say 'message' (chat) or 'step' (a tick), or "
            "add this key to _SHARED_TURN_OK if it is genuinely about both"
        )

    def test_the_group_blurbs_agree_too(self):
        # The section headings a person actually reads first — same rule, same two exceptions
        # don't apply here since no group is chat-and-tick-shared in name only.
        for group in GROUPS:
            text = f"{group.label} {group.blurb}"
            assert not self._NOUN_TICK.search(text), f"{group.key}: {text!r} says 'tick'"
            assert not self._NOUN_TURN.search(text), f"{group.key}: {text!r} says 'turn'"

    def test_the_shared_exception_list_is_still_accurate(self):
        # If one of these three ever loses its "turn", the exception is stale and should shrink —
        # this fails loudly instead of the set silently protecting a label that no longer needs it.
        for key in _SHARED_TURN_OK:
            knob = for_key(key)
            assert self._NOUN_TURN.search(knob.label) or self._NOUN_TURN.search(knob.help), (
                f"{key} no longer says 'turn' anywhere — remove it from _SHARED_TURN_OK"
            )
