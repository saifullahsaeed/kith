"""What he is holding in mind is scoped to what the conversation is about.

Memories were global: `context_block` injected the `core` set and the eight most-recent on every
turn, whatever the chat was about. Measured on the live database in a Capital Call security chat,
the whole "back of your mind" was four paragraphs of a *different* project's Odoo internals —
1,378 tokens of memory, none of it about the work in hand. It is the same bleed the project
region fixed for the board, one layer over: a chat in one project was handed another's detail.

The rule now: a memory carries a project (or none). A global memory — a fact that holds whatever
the work is — surfaces everywhere. A project's own memories surface in that project's chats.
Another project's are not dumped; `recall`, which is unscoped, reaches them. So the injected set
is small and on-topic and everything else is a search away rather than a tax on every turn.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo
from kith.services import memory_context


@pytest.fixture
def memories(db):
    """One global fact and one apiece for two projects, at both levels."""
    repo.memories.add_memory(db, "the user has two GitHub accounts", level="core", project_id=None)
    repo.memories.add_memory(db, "the portal uses JWT in a cookie", level="recall", project_id=17)
    repo.memories.add_memory(db, "the ERP is 27 Django apps", level="core", project_id=16)
    repo.memories.add_memory(db, "odoo speaks JSON-2 not jsonrpc", level="recall", project_id=6)
    return db


class TestWhatSurfaces:
    def test_a_project_chat_gets_its_own_and_the_global_ones(self, memories):
        block = memory_context.context_block(memories, project_id=17)

        assert "GitHub" in block  # global — everywhere
        assert "JWT in a cookie" in block  # this project

    def test_a_project_chat_does_not_get_another_project_s(self, memories):
        block = memory_context.context_block(memories, project_id=17)

        assert "JSON-2" not in block  # project #6
        assert "27 Django apps" not in block  # project #16

    def test_the_other_project_gets_its_own(self, memories):
        block = memory_context.context_block(memories, project_id=6)

        assert "JSON-2" in block
        assert "GitHub" in block
        assert "JWT in a cookie" not in block

    def test_an_unbound_chat_gets_only_the_global_ones(self, memories):
        # No project to scope to, so nothing project-specific is dumped. It is all still there
        # for `recall` — this is only what arrives without asking.
        block = memory_context.context_block(memories, project_id=None)

        assert "GitHub" in block
        assert "JWT in a cookie" not in block
        assert "JSON-2" not in block
        assert "27 Django apps" not in block


class TestRecallStillReachesEverything:
    """The whole point of not dumping is that fetching still works. A memory scoped to another
    project must be findable — otherwise this is deletion, not scoping."""

    def test_a_foreign_project_s_memory_is_still_searchable(self, memories):
        found = repo.memories.search_memories(memories, "JSON-2")

        assert any("JSON-2" in m["content"] for m in found)


class TestNewMemoriesAreStamped:
    def test_add_memory_records_the_project(self, db):
        made = repo.memories.add_memory(db, "a fact about this project", project_id=17)

        assert made["project_id"] == 17

    def test_a_global_memory_has_no_project(self, db):
        made = repo.memories.add_memory(db, "a fact about nothing in particular")

        assert made["project_id"] is None
