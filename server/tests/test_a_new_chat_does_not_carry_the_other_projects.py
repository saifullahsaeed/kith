"""What a conversation about nothing arrives carrying.

Measured on the real database before this existed: **13,362 characters**, of which roughly
10,500 was another project's memories — a missile simulator's physics notes, a Flutter
toolchain listing, a domain-registry finding — in a brand-new chat that had been asked
nothing. 104 of 141 conversations on that machine were unbound, so this was not an edge case;
it was the common case, and the wide path was the default one.

Two causes, and neither is a cap set too high:

1. **The menu.** `project_context.block` returns "" for an unbound chat, and `memory_context`
   filled the gap with every active project and every active task across all of them. The
   comment called that "the right context for a chat that has not chosen yet", which assumed
   an unbound chat is pre-choice. Most are not. They are questions.
2. **The level contract.** `recent_memories` selected `level != "core"`, and with
   `MEMORY_LEVELS = ("core", "recall")` that is every recall memory — precisely the ones
   `set_memory_level` promises are "retrieved when you reach for it".
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo
from kith.services.turn import prompt as turn_prompt


@pytest.fixture
def seeded(db, monkeypatch):
    """A machine with two projects, work on both, and memories from both."""
    monkeypatch.setattr(turn_prompt, "AGENT_DB_PATH", db)

    sim = repo.projects.add_project(db, "Ballistic Sim", "Missile defence simulator", "/tmp/sim")
    shop = repo.projects.add_project(db, "Storefront", "The customer-facing shop", "/tmp/shop")
    for project in (sim, shop):
        repo.projects.update_project(db, int(project["id"]), status="active")

    repo.tasks.add_task(db, "Decompose the boost phase burn", status="working", project_id=int(sim["id"]))
    repo.tasks.add_task(db, "Fix the checkout button colour", status="working", project_id=int(shop["id"]))

    repo.memories.add_memory(
        db, "The missile lands 38 km east of target and that is correct physics.", level="recall"
    )
    repo.memories.add_memory(db, "Checkout button colour comes from the accent design token.", level="recall")
    repo.memories.add_memory(db, "Always verify before claiming something works.", level="core")
    return db


def _state(conversation_id: str = "", about: str = "") -> str:
    return turn_prompt._present_state(conversation_id, about=about)


class TestAnUnboundChatIsNotHandedTheWholeMachine:
    def test_the_other_projects_are_named_and_not_described(self, seeded):
        """Named, so he can recognise that something belongs elsewhere. No further, because a
        task list is an invitation to go and work on it."""
        state = _state(about="what is the capital of France")
        assert "Ballistic Sim" in state
        assert "Decompose the boost phase burn" not in state
        assert "Fix the checkout button colour" not in state

    def test_a_recall_memory_about_another_subject_stays_in_recall(self, seeded):
        state = _state(about="what is the capital of France")
        assert "38 km east" not in state
        assert "accent design token" not in state

    def test_core_memories_still_arrive(self, seeded):
        """Core is the one level that means 'always'. Narrowing must not quietly take it."""
        assert "verify before claiming" in _state(about="what is the capital of France")

    def test_the_clock_is_never_a_casualty(self, seeded):
        assert "[Right now]" in _state(about="anything")


class TestRelevanceLetsTheRightMemoryThrough:
    def test_a_recall_memory_about_the_subject_does_arrive(self, seeded):
        state = _state(about="the checkout button colour is wrong, is it the accent token")
        assert "accent design token" in state

    def test_and_the_unrelated_one_still_does_not(self, seeded):
        state = _state(about="the checkout button colour is wrong, is it the accent token")
        assert "38 km east" not in state


class TestTheWholeBlockHasACeiling:
    def test_a_machine_full_of_projects_cannot_push_the_block_past_its_budget(self, db, monkeypatch):
        monkeypatch.setattr(turn_prompt, "AGENT_DB_PATH", db)
        for n in range(40):
            made = repo.projects.add_project(db, f"Project {n}", "x" * 500, f"/tmp/p{n}")
            repo.projects.update_project(db, int(made["id"]), status="active")
            for t in range(5):
                repo.tasks.add_task(
                    db, f"Task {t} of project {n}", status="working", project_id=int(made["id"])
                )
        for n in range(60):
            repo.memories.add_memory(db, f"Memory number {n} about project {n}.", level="core")

        assert len(_state(about="hello")) <= turn_prompt.PRESENT_BUDGET
