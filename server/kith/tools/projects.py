"""Projects and their roadmap."""

from __future__ import annotations

import itertools
from pathlib import Path

from kith.domain.enums import MILESTONE_STATUSES, PROJECT_STATUSES
from kith.infra.db import repositories as repo
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
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
    # A project carries its milestones, so a handful of them is already a large payload.
    {**PAGE_PARAMS},
    required=(),
)
def list_projects(path: Path, args: dict):
    """Projects with their roadmap, and which milestones are actually available.

    ``ready`` and ``blocked_by`` are included per milestone because the order is not
    advisory: work under a waiting milestone is not offered to him at all, so a listing
    that showed only titles and statuses would leave him unable to see why something he
    can see is not something he can do.
    """
    return paging.page(repo.projects.project_overview(path), args, default=5)


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
        "after": {
            "type": "array",
            "items": INT,
            "description": "Ids of milestones this one waits for — the numeric id a "
            "milestone already has, NOT its position in the list you are creating. Its tasks "
            "stay out of your way until they are all done. If you are laying out a fresh "
            "roadmap and do not have the ids yet, add them all first and then call "
            "order_milestones with the ids in order.",
        },
    },
    required=("project_id", "title"),
)
def add_milestone(path: Path, args: dict):
    """Find or create the milestone, then apply its order either way.

    Same title twice used to mean a second row: the roadmap came out with "Workout data model
    and local persistence are defined" listed twice, the copy dangling with no order on it,
    permanently available and looking like real work.

    The obvious fix — return the existing one and stop — was worse, and it is worth writing
    down why. Laying out a fresh roadmap he has no ids yet, so he adds the milestones first
    with no `after`, and comes back to wire the order once he knows them. That second call is
    the *same title* with an `after`, which the early return threw on the floor. He tried
    three edges, got three milestones back with no order, tried the identical three again,
    and stopped: ten calls, zero edges, and a roadmap where every milestone was available at
    once. Silently discarding the meaningful half of a call is how you get a caller that
    retries forever and a person who concludes the feature does not work.

    So: idempotent on the milestone, and `after` is honoured whether the milestone was just
    made or already there.
    """
    title = str(args["title"]).strip()
    target = next(
        (
            m
            for m in repo.projects.list_milestones(path)
            if m["project_id"] == args["project_id"] and m["title"].strip() == title
        ),
        None,
    )
    reused = target is not None
    if target is None:
        target = repo.projects.add_milestone(path, args["project_id"], title, args.get("target_at"))

    problems = []
    added = 0
    for earlier in args.get("after") or []:
        try:
            repo.projects.add_dependency(path, target["id"], int(earlier))
            added += 1
        except (TypeError, ValueError) as exc:
            problems.append(str(exc))

    out = {**target}
    if reused:
        out["note"] = "That milestone already existed, so this is the one you have" + (
            f" — and it now waits for {', '.join(str(a) for a in args['after'])}." if added else "."
        )
    if problems:
        out["warnings"] = problems
    return out


@tool(
    "order_milestones",
    "Put a project's milestones in order, so each waits for the one before it. Pass the ids "
    "in the order they should happen. This is what makes a roadmap real: you will only be "
    "offered work from milestones whose predecessors are finished, so you build in the order "
    "you laid out instead of picking whatever looks urgent.",
    {
        "ids": {
            "type": "array",
            "items": INT,
            "description": "Milestone ids, earliest first.",
        }
    },
    required=("ids",),
)
def order_milestones(path: Path, args: dict):
    """One call for the common case, which is a straight line.

    Chaining a five-step roadmap by hand is four separate calls and four chances to get a
    direction backwards — and a backwards edge is not a visible mistake, it is work that
    quietly never becomes available.
    """
    ids = [int(one) for one in (args.get("ids") or [])]
    if len(ids) < 2:
        return {"ordered": ids, "note": "nothing to order — pass two or more ids"}
    problems = []
    for earlier, later in itertools.pairwise(ids):
        try:
            repo.projects.add_dependency(path, later, earlier)
        except (TypeError, ValueError) as exc:
            problems.append(f"{earlier} -> {later}: {exc}")
    return {
        "ordered": ids,
        "roadmap": repo.projects.roadmap(path, _project_of(path, ids[0])),
        **({"warnings": problems} if problems else {}),
    }


@tool(
    "unlink_milestones",
    "Stop one milestone waiting for another, when the order you set turns out to be wrong.",
    {"milestone_id": INT, "no_longer_waits_for": INT},
    required=("milestone_id", "no_longer_waits_for"),
)
def unlink_milestones(path: Path, args: dict):
    repo.projects.remove_dependency(path, args["milestone_id"], args["no_longer_waits_for"])
    return {"ok": True}


def _project_of(path: Path, milestone_id: int) -> int:
    for milestone in repo.projects.list_milestones(path):
        if milestone["id"] == milestone_id:
            return int(milestone["project_id"])
    return 0


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
