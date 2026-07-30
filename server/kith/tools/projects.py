"""Projects and their roadmap."""

from __future__ import annotations

from pathlib import Path

from kith.domain.enums import MILESTONE_STATUSES, PROJECT_STATUSES
from kith.infra.db import repositories as repo
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "create_project",
    "Start a project — a bigger goal that groups several tasks and a roadmap of "
    "milestones. Use this when work is more than a single task.",
    {"name": STR, "description": {**STR, "description": "What the project is and what 'done' means."}},
    required=("name",),
)
def create_project(path: Path, args: dict):
    return repo.projects.add_project(path, args["name"], args.get("description") or "")


@tool(
    "list_projects",
    "See your projects with their roadmap (milestones) and how much is done.",
    {},
    required=(),
)
def list_projects(path: Path, args: dict):
    return repo.projects.project_overview(path)


@tool(
    "update_project",
    "Update a project — change its name/description, or set its status. Set 'done' "
    "when the whole project is finished (then you can rest and stop working its "
    "tasks), 'paused' to set it aside, 'archived' to file it away.",
    {
        "id": INT,
        "status": {**STR, "enum": list(PROJECT_STATUSES)},
        "name": STR,
        "description": STR,
    },
    required=("id",),
)
def update_project(path: Path, args: dict):
    return repo.projects.update_project(
        path, args["id"], args.get("status"), args.get("name"), args.get("description")
    )


@tool(
    "add_milestone",
    "Add a milestone to a project's roadmap — a step to reach, optionally by a date. "
    "Milestones are the timeline you follow.",
    {
        "project_id": INT,
        "title": STR,
        "target_at": {**STR, "description": "Optional target date, ISO 8601 (local zone)."},
    },
    required=("project_id", "title"),
)
def add_milestone(path: Path, args: dict):
    return repo.projects.add_milestone(path, args["project_id"], args["title"], args.get("target_at"))


@tool(
    "update_milestone",
    "Update a milestone — mark it 'done' when reached, or change its title/target.",
    {
        "id": INT,
        "status": {**STR, "enum": list(MILESTONE_STATUSES)},
        "title": STR,
        "target_at": STR,
    },
    required=("id",),
)
def update_milestone(path: Path, args: dict):
    return repo.projects.update_milestone(
        path, args["id"], args.get("status"), args.get("title"), args.get("target_at")
    )
