"""What a memory is scored against, in a chat that is in a project.

`_subject` is the person's last three messages, and for an unbound chat that is the only
signal there is. In a bound chat it throws away the best one: the conversation is *about* a
project by construction, and the project has a name and a description sitting right there.

The cost of leaving it out is asymmetric and quiet. "can you look at that again" scores zero
against every memory in the database, so a turn where the person says something short — which
is most turns in a long session — carries no project memory at all, while the turn where they
happened to type the project's name carries all of it. Memory that flickers with the phrasing
of the last message is worse than memory that is simply absent, because it looks like it works.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo
from kith.services import conversations
from kith.services.turn import prompt as turn_prompt


@pytest.fixture
def bound(db, monkeypatch, tmp_path):
    monkeypatch.setattr(turn_prompt, "AGENT_DB_PATH", db)
    project = repo.projects.add_project(
        db, "Odoo connector", "Syncing invoices from the Odoo tenant", str(tmp_path)
    )
    repo.projects.update_project(db, int(project["id"]), status="active")
    chat = conversations.start(db, "a chat")
    repo.conversations.set_project(db, chat["id"], int(project["id"]))
    repo.memories.add_memory(
        db,
        "The Odoo tenant does not speak jsonrpc; invoice sync is sequential.",
        level="recall",
        project_id=int(project["id"]),
    )
    return db, chat["id"]


class TestAShortMessageStillReachesTheProjectsMemory:
    def test_the_project_is_part_of_what_a_memory_is_scored_against(self, bound):
        _, chat = bound
        state = turn_prompt._present_state(chat, about="can you look at that again")
        assert "invoice sync is sequential" in state

    def test_an_unbound_chat_is_unaffected(self, db, monkeypatch):
        """No project, so nothing is added and the person's words remain the whole signal."""
        monkeypatch.setattr(turn_prompt, "AGENT_DB_PATH", db)
        repo.memories.add_memory(db, "Something about missiles and apogee.", level="recall")
        assert "apogee" not in turn_prompt._present_state("", about="can you look at that again")


class TestItDoesNotBecomeAFreePass:
    def test_a_memory_unrelated_to_the_project_still_stays_out(self, bound):
        db, chat = bound
        repo.memories.add_memory(db, "Missile apogee is 180 kilometres.", level="recall")
        state = turn_prompt._present_state(chat, about="can you look at that again")
        assert "apogee" not in state
