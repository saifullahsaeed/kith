"""The worst loss in the whole handoff: he could not read what he had written down.

A work tick was told about its own past like this:

    last = comments[-1]
    lines.append(f"Latest note ({last['author']}): {last['body'][:160]}")

One note, cut at a hundred and sixty characters. Measured across the board: **170,736
characters** of findings he had written on his own tasks, of which a tick was ever shown
**4.1%**.

It is the worst possible channel to lose, because commenting progress is what the persona tells
him to do, so that is where his best notes go. Task #43 — ten ticks — had fourteen comments
holding 5,849 characters against a working file of 2,659 bytes: he wrote more than twice as much
into the channel he could not read back as into the one he could. Then he re-derived it from the
codebase, which is the measured 54% of file reads that were of a file already read that turn.

The first note he now sees on #43 reads "Existing `AppSetting` is a generic **Postgres**-backed
key/value table" — the exact fact he later got wrong in a document, having recorded it correctly
and been unable to see it.

Second bug in the same function: the working file was addressed as `work/task-N.md` while the
persona and `project_files.ensure` both say `.kith/work/`. The person moved their `work/` folder
into `.kith/` and every tick went on instructing him to write outside it, so the notes split down
the middle — tasks 34–41 under `.kith/work/`, 42–50 under `work/` — and the prompt could only
ever see one side.
"""

from __future__ import annotations

from kith.autonomy import prompts


def _mine(body: str) -> dict:
    return {"author": "kith", "body": body}


def _theirs(body: str) -> dict:
    return {"author": "user", "body": body}


class TestHeSeesWhatHeWrote:
    def test_more_than_one_note_survives(self):
        notes = prompts._task_notes([_mine("first thing"), _mine("second thing"), _mine("third")])
        joined = "\n".join(notes)
        assert "first thing" in joined
        assert "second thing" in joined
        assert "third" in joined

    def test_a_long_note_is_not_cut_at_160_characters(self):
        body = "The catalog lives in app/db/model_catalog.py. " * 12  # ~540 chars
        notes = prompts._task_notes([_mine(body)])
        assert len(notes[0]) > 400

    def test_it_reads_oldest_first(self):
        """These are a narrative. Reading one backwards costs him a round working out the order."""
        notes = prompts._task_notes([_mine("step one"), _mine("step two"), _mine("step three")])
        joined = "\n".join(notes)
        assert joined.index("step one") < joined.index("step two") < joined.index("step three")

    def test_nothing_is_claimed_when_there_is_nothing(self):
        assert prompts._task_notes([]) == []

    def test_his_notes_are_labelled_as_his(self):
        assert "[you wrote]" in prompts._task_notes([_mine("a finding")])[0]

    def test_newlines_are_flattened_so_one_note_is_one_line(self):
        """A note with its own headings and bullets would otherwise be indistinguishable from the
        surrounding prompt structure."""
        notes = prompts._task_notes([_mine("### Findings\n- one\n- two\n\n### Next\n- three")])
        assert len(notes) == 1
        assert "\n" not in notes[0]


class TestTheirCorrectionsAreNeverDropped:
    def test_a_note_from_the_person_survives_a_full_budget(self):
        """"sqlite is not being used any more, we moved to postgres" is exactly the sentence that
        must not fall off the end of a budget. Theirs are rare and usually a correction."""
        flood = [_mine("x" * 900) for _ in range(20)]  # far past the budget
        correction = _theirs("stop using sqlite, this project is on postgres now")
        notes = prompts._task_notes([correction, *flood])
        assert any("postgres now" in line for line in notes)

    def test_theirs_is_marked_differently_from_his(self):
        notes = prompts._task_notes([_theirs("do it this way instead")])
        assert "[YOU ASKED]" in notes[0]

    def test_an_early_correction_keeps_its_place_in_the_story(self):
        notes = prompts._task_notes([_theirs("use the API"), _mine("done"), _mine("verified")])
        joined = "\n".join(notes)
        assert joined.index("use the API") < joined.index("done")


class TestTheBudget:
    def test_a_long_history_is_trimmed(self):
        notes = prompts._task_notes([_mine("y" * 1_000) for _ in range(20)])
        assert sum(len(line) for line in notes) < prompts._NOTES_CHARS + 1_500

    def test_the_most_recent_note_always_gets_in_however_long(self):
        """A budget that can return nothing would put a tick back to knowing nothing about its own
        last attempt, which is the entire bug."""
        notes = prompts._task_notes([_mine("z" * (prompts._NOTES_CHARS * 3))])
        assert len(notes) == 1
        assert len(notes[0]) > prompts._NOTES_CHARS

    def test_it_keeps_the_recent_end_not_the_old_one(self):
        history = [_mine(f"note {i} " + "w" * 800) for i in range(20)]
        joined = "\n".join(prompts._task_notes(history))
        assert "note 19" in joined
        assert "note 0" not in joined

    def test_it_says_when_something_was_left_out(self):
        """Silence would read as "that is all there is", and he would stop looking."""
        notes = prompts._task_notes([_mine("q" * 1_000) for _ in range(20)])
        assert any("earlier note" in line and "view_task" in line for line in notes)

    def test_it_says_nothing_when_nothing_was_left_out(self):
        notes = prompts._task_notes([_mine("short"), _mine("also short")])
        assert not any("earlier note" in line for line in notes)


class TestTheWorkingFileIsWhereThePersonaSaysItIs:
    def test_the_prompt_asks_for_the_kith_folder(self):
        """`project_files.ensure` creates `.kith/work/` and the persona sends notes there. This
        line said `work/`, so following either one put the file where the other could not see it."""
        source = prompts.__loader__.get_source("kith.autonomy.prompts")
        assert 'f".kith/work/task-{detail[\'id\']}.md"' in source

    def test_the_pre_move_location_is_still_read(self):
        """Nine real files sit at `work/task-4X.md`. Moving the path must not orphan them."""
        source = prompts.__loader__.get_source("kith.autonomy.prompts")
        assert 'f"work/task-{detail[\'id\']}.md"' in source
        # And only ever read — the instruction he is given names the new location.
        instruct = source.split("You have no working file")[1][:200]
        assert "work_path" in instruct
