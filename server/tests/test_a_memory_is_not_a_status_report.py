"""What he may keep, and what is a query wearing a memory's clothes.

Measured on the real database: 51 memories, **49 of them `core`**, and 27 lines of the core
block were one fact re-saved on different days —

    Current checkpoint (2026-08-12): Active high-priority Sadeef AI Task #99, "Move Odoo
    behavior into the plugin runtime," is working.

`core` means always present, so that block was 21,728 characters — about 5,432 tokens — in
every prompt of every round. A forty-round turn spent a quarter of a million tokens restating
something the tasks table answers for free and that stopped being true a week earlier.
`remember` was called 17 times; `recall` four.

A refusal rather than a better description, because the description was already right and was
ignored — "Task #99 is active" genuinely *is* a fact that outlives the conversation, so prose
cannot exclude it without excluding the category. And prose has a record here: the persona
paragraph asking him to batch tool calls moved the single-call share from 77% to 88%.

The hard part is not catching the snapshots. It is not catching everything else, so most of
this file is about what must still get through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra.db import repositories as repo
from kith.services import remembering
from kith.tools import memory as memory_tools


class TestAStatusSnapshotIsRefused:
    @pytest.mark.parametrize(
        "content",
        [
            'Current checkpoint (2026-08-12): Active high-priority Task #99, "Move Odoo behavior '
            'into the plugin runtime," is working.',
            "As of today the active workstream is Task #103, connect from the plugin card.",
            "Project 6 has all 4 milestones met and Task #110 is in progress.",
            "Currently working on task 55.",
        ],
    )
    def test_the_shapes_that_filled_the_database(self, db: Path, content: str):
        refusal = remembering.refuse(db, content, "core")

        assert refusal is not None
        assert "list_tasks" in refusal["next"], "a refusal has to name what to do instead"

    def test_it_is_refused_before_anything_is_written(self, db: Path):
        """Cheaper to refuse than to undo: a snapshot goes stale on its own, and a `core` one is
        in every prompt until somebody notices."""
        memory_tools.remember(db, {"content": "Checkpoint: Task #99 is working.", "level": "core"})

        assert repo.memories.list_memories(db) == []


class TestEverythingElseStillGetsThrough:
    @pytest.mark.parametrize(
        "content",
        [
            # The one genuinely good memory in the measured database.
            "The user has two GitHub accounts on this machine: saifullah_sadeefcs for work and "
            "saifullahsaeed personally, and the personal one owns the kith repo.",
            # A board row, but a durable fact about it rather than its status.
            "Task #99 exists because Odoo's JSON-RPC cannot express a batch write, which is why "
            "the runtime does one call per row.",
            # A state word, but nothing the board holds.
            "He prefers the checkpoint to be taken before the deploy, not after.",
            "They like being told what was checked, not just the conclusion.",
        ],
    )
    def test_a_real_memory_is_not_caught(self, db: Path, content: str):
        """Both halves are required — a board row AND a word about where things stand. Either
        alone is an ordinary memory, and a gate that fired on either would be worse than the
        problem: it would teach him to stop saving the things that are actually worth keeping."""
        assert remembering.refuse(db, content, "recall") is None

    def test_nothing_at_all_is_still_refused(self, db: Path):
        assert remembering.refuse(db, "   ", "recall") is not None


class TestTheSameThingTwiceIsRefused:
    def test_a_second_copy_does_not_correct_the_first(self, db: Path):
        """Nine copies of one fact were nine separate calls, each reasonable alone. A duplicate
        is worse than useless: both are in front of him and one of them is wrong."""
        fact = (
            "The release workflow cuts a version when desktop/package.json changes and no tag "
            "for it exists yet, so pushing a bump is what publishes."
        )
        memory_tools.remember(db, {"content": fact})

        refusal = remembering.refuse(db, fact, "recall")

        assert refusal is not None
        assert "forget" in refusal["next"]

    def test_the_date_is_not_what_makes_it_different(self, db: Path):
        """The measured copies differed only in their date and a clause of wording."""
        memory_tools.remember(
            db, {"content": "On 2026-08-12 the deploy target became ai.sadeef.com for every environment."}
        )

        refusal = remembering.refuse(
            db, "On 2026-08-14 the deploy target became ai.sadeef.com for every environment.", "recall"
        )

        assert refusal is not None

    def test_something_genuinely_new_is_kept(self, db: Path):
        memory_tools.remember(db, {"content": "The deploy target is ai.sadeef.com for every environment."})

        assert (
            remembering.refuse(db, "Ollama must be running before the embedding backfill works.", "recall")
            is None
        )


class TestCoreCostsSomething:
    def test_past_the_cap_it_has_to_be_a_trade(self, db: Path):
        """`core` is the one level with a price per round, and 49 of them is not "the front of
        your mind", it is a filing cabinet in the prompt."""
        for index in range(remembering.MAX_CORE):
            repo.memories.add_memory(
                db, f"A durable fact number {index} about how they work.", None, 0, "core"
            )

        refusal = remembering.refuse(db, "One more durable fact worth carrying.", "core")

        assert refusal is not None
        assert "set_memory_level" in refusal["next"]

    def test_recall_is_never_capped(self, db: Path):
        """The cap is about what rides in every prompt, not about how much he may know."""
        for index in range(remembering.MAX_CORE + 5):
            repo.memories.add_memory(
                db, f"A durable fact number {index} about how they work.", None, 0, "core"
            )

        assert remembering.refuse(db, "Something worth keeping but not carrying.", "recall") is None
