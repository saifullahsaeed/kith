"""One task in full."""

from __future__ import annotations

from flask import jsonify

from kith.api.blueprint import api
from kith.config import AGENT_DB_PATH
from kith.infra.db import repositories as repo


@api.get("/tasks/<int:task_id>")
@api.doc(summary="A task in full", description="Task with its comment thread, checklist, and deliverables.")
def task_detail(task_id):
    return jsonify(repo.tasks.task_detail(AGENT_DB_PATH, task_id) or {})
