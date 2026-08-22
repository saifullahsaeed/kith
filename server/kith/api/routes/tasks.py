"""One task in full."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from flask import jsonify

from kith.api.blueprint import api
from kith.api.routes.workspace import wanted
from kith.infra.db import repositories as repo
from kith.settings import AGENT_DB_PATH


@api.get("/tasks/<int:task_id>")
@api.doc(summary="A task in full", description="Task with its comment thread, checklist, and deliverables.")
def task_detail(task_id):
    detail = repo.tasks.task_detail(AGENT_DB_PATH, task_id) or {}
    _describe_files(detail)
    return jsonify(detail)


def _describe_files(detail: dict) -> None:
    """Say how big each file deliverable is and when it was last written.

    A deliverable row had a title and a path and nothing else — not even the date it was filed,
    which the database has had all along. For a file the two facts worth more than that are its
    size and when it last changed, and neither is in the database because neither is a fact about
    the row: he can rewrite `docs/V2_CORE_ARCHITECTURE.md` ten times without touching the
    deliverable that points at it.

    So they are read here, from the file, at the moment the page asks. Seven `stat` calls against
    a local disk, and the alternative — storing a size that goes stale the next time he saves —
    is worse than not showing one.

    Anchored to the task's project the same way `/workspace/raw` and the viewer anchor: a
    deliverable's path belongs to its project, not to whichever session happens to be open.
    Missing or unreadable is left absent rather than reported as zero, because a file he has since
    moved should read as "not there" and not as "empty".
    """
    project_id = detail.get("project_id")
    for one in detail.get("deliverables") or []:
        if one.get("kind") != "file":
            continue
        try:
            target = Path(wanted(str(one.get("content") or ""), str(project_id) if project_id else None))
            stat = target.stat()
        except Exception:
            continue
        one["bytes"] = int(stat.st_size)
        one["modified"] = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
