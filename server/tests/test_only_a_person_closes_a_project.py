"""Marking a project done is a person's call, never his.

Two mechanisms used to let a project reach "done" without anyone deciding it: `_roll_up`
closed it the moment its last milestone did (see test_task_rollup.py — that cascade is gone
now, on purpose), and the `update_project` tool would forward whatever status the model
asked for, "done" included. This file covers the second one: the tool itself refuses the
status outright, both in what it offers the model and in what it does if asked anyway.

The person's own path is untouched — the UI's status dropdown goes through `brain.update`,
not this tool, so closing a project by hand still works exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools import registry, tool_schemas


def test_the_schema_does_not_offer_done_at_all(db: Path) -> None:
    """Checked first because it is the cheaper guard — a model that never sees "done" as an
    option cannot ask for it by picking from the enum, only by typing outside it."""
    [schema] = [s for s in tool_schemas(db) if s["function"]["name"] == "update_project"]
    enum = schema["function"]["parameters"]["properties"]["status"]["enum"]
    assert "done" not in enum
    assert set(enum) == {"active", "paused", "archived"}


def test_asking_anyway_is_refused_not_silently_dropped(db: Path) -> None:
    """The backstop for a provider that does not enforce the enum strictly. Silently ignoring
    the field would read as "saved" while doing nothing — this has to say why not."""
    project = repo.projects.add_project(db, "p")

    out = registry.require("update_project").run(db, {"id": project["id"], "status": "done"})

    assert "error" in out
    assert "person" in out["error"].lower()
    assert repo.projects.get_project(db, project["id"])["status"] == "active"


def test_an_allowed_status_still_goes_through(db: Path) -> None:
    """The refusal must not have taken the rest of the tool down with it."""
    project = repo.projects.add_project(db, "p")

    registry.require("update_project").run(db, {"id": project["id"], "status": "paused"})

    assert repo.projects.get_project(db, project["id"])["status"] == "paused"
