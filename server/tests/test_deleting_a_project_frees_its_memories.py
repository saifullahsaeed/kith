"""Deleting a project makes its memories global, not stranded.

`memories._scope` admits a project memory only in that project's chats. So a memory left pointing
at a project that no longer exists reaches no prompt at all — "always with you" in name and in
front of nobody in fact. Measured on the live database: two `core` memories were stamped with a
project id (27) that had been deleted, and both had gone silent.

`delete_project` already orphans the project's tasks and unbinds its conversations for the same
reason — the work outlives the grouping. Its memories were the one thing it forgot. Global is the
honest home: the fact holds whatever the work is now, and `recall` still reaches it either way.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo


def test_a_deleted_projects_memory_becomes_global(db: Path) -> None:
    project = repo.projects.add_project(db, "Doomed", "about to be deleted")
    kept = repo.memories.add_memory(db, "A durable fact about the doomed project.", project_id=project["id"])

    repo.projects.delete_project(db, project["id"])

    survivor = next(m for m in repo.memories.list_memories(db) if m["id"] == kept["id"])
    assert survivor["project_id"] is None  # global now, not dangling at a missing project


def test_a_freed_memory_reaches_prompts_again(db: Path) -> None:
    """The point of freeing it: a global core memory surfaces everywhere, which a project memory
    at a deleted id could not."""
    project = repo.projects.add_project(db, "Doomed", "")
    repo.memories.add_memory(db, "The one fact worth carrying.", level="core", project_id=project["id"])

    repo.projects.delete_project(db, project["id"])

    # In a chat bound to some *other* project, the freed core memory is still in the core set.
    core = repo.memories.memories_by_level(db, "core", project_id=999)
    assert any("worth carrying" in m["content"] for m in core)


def test_other_projects_memories_are_untouched(db: Path) -> None:
    doomed = repo.projects.add_project(db, "Doomed", "")
    keeper = repo.projects.add_project(db, "Keeper", "")
    repo.memories.add_memory(db, "Belongs to the doomed one.", project_id=doomed["id"])
    theirs = repo.memories.add_memory(db, "Belongs to the keeper.", project_id=keeper["id"])
    globally = repo.memories.add_memory(db, "Belongs to nobody in particular.", project_id=None)

    repo.projects.delete_project(db, doomed["id"])

    by_id = {m["id"]: m for m in repo.memories.list_memories(db)}
    assert by_id[theirs["id"]]["project_id"] == keeper["id"]  # the surviving project keeps its own
    assert by_id[globally["id"]]["project_id"] is None
