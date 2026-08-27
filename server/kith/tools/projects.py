"""Projects and their roadmap."""

from __future__ import annotations

import itertools
from pathlib import Path

from kith.domain.enums import MILESTONE_STATUSES, PROJECT_STATUSES
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo
from kith.services import project_binding
from kith.services.tasks import _out_of_scope
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "create_project",
    "Start a project — a bigger goal that groups several tasks and a roadmap of "
    "milestones. Use this when work is more than a single task. If it involves code or "
    "files, give it a `directory`: that folder is where the work lives and where the "
    "project keeps what it learns about itself, in `.kith/memory.md`, which you are shown "
    "every time you work there.",
    {
        "name": STR,
        "description": {**STR, "description": "What the project is and what 'done' means."},
        "directory": {
            **STR,
            "description": "The folder the work lives in — relative to your own folder, or "
            "an absolute path your person pointed you at. Leave it out for a project with no "
            "files, like a piece of research.",
        },
    },
    required=("name",),
)
def create_project(path: Path, args: dict):
    """Start a project, and give it a folder if it is the kind that has one.

    A folder is the difference between a project and a list of intentions. It is also where
    the project's own memory lives — `.kith/memory.md`, read to you every time you work here
    — so a code project without one has nowhere to keep what it learns about itself. Beside it,
    `.kith/references.md` holds what the project points *at*: the brief, the spec, the standard
    you are held to. Both are read to you every turn you spend on this project; neither exists
    unless something writes to it.

    Not every project has code, though, and one that does not should not be handed a pretend
    directory: a shortlist or a piece of research is a project with rows and no folder.
    """
    from kith.infra import project_files
    from kith.services import project_memory

    # A conversation gets one project. `_adoption_note` used to be the whole answer to a session
    # starting a second one: the project was created, the binding silently refused, and the reply
    # said so in a note — which left a real row on the board that nothing was working on. Six of
    # the nine projects on the real board point at one folder, two of those are active, and one is
    # called `placeholder`. Refusing is what the note was trying to be.
    bound = project_binding.bound_project(path)
    if bound:
        current = repo.projects.get_project(path, int(bound)) or {}
        return {
            "blocked": (
                f"This conversation is working on {current.get('name') or f'project #{bound}'}, and "
                "a conversation stays with the project it started on."
            ),
            "next": (
                "If this is genuinely separate work, say so and start a new conversation for it. If "
                "it belongs to the project you are already in, file it there instead."
            ),
        }

    directory = str(args.get("directory") or "").strip()
    if directory:
        resolved = Path(sandbox.resolve(directory))
        resolved.mkdir(parents=True, exist_ok=True)
        # A folder has one project. Handing back the existing one rather than refusing, because
        # the intent — work on this codebase — is right and only the "new" part is wrong.
        existing = next(
            (
                one
                for one in repo.projects.list_projects(path)
                if str(one.get("directory") or "") == str(resolved)
            ),
            None,
        )
        if existing:
            project_binding.adopt(path, existing.get("id"), deliberate=True)
            return {
                **existing,
                "note": (
                    f"{resolved} already belongs to {existing.get('name')}, so that is the project "
                    "you are now on — nothing new was created. A second board over one folder is "
                    "how the same work ends up in two places."
                ),
            }
        # Created with its scaffold now rather than on first write, so the headings are there
        # to be filled in instead of the file being invented from scratch later.
        project_memory.ensure(resolved)
        project_files.ensure(resolved)
        made = repo.projects.add_project(path, args["name"], args.get("description") or "", str(resolved))
        project_binding.adopt(path, made.get("id"), deliberate=True)
        out = {
            **made,
            "memory": f"{directory}/.kith/memory.md",
            "references": f"{directory}/.kith/references.md",
        }
        # Said at the one moment it can still be acted on cheaply. `.kith/.gitignore` decides
        # what of his work is safe to commit, and a root-level `.kith/` rule means git never
        # reads it — see `project_files.neutered_by`. Reported, never repaired: their ignore
        # file is theirs.
        blocking = project_files.neutered_by(resolved)
        if blocking:
            out["heads_up"] = (
                f"`.gitignore` here has `{blocking}`, so nothing in `.kith/` is meant to be "
                "committed — which means the second person to work on this project starts from "
                "nothing, and `.kith/.gitignore` (which is what keeps scratch and credentials "
                "out) is never read. Worth telling them: drop that line and the folder shares "
                "properly, keep it and the folder is yours alone. Do not change it yourself."
            )
        return out
    made = repo.projects.add_project(path, args["name"], args.get("description") or "")
    # The conversation that started it is the one working on it. Nothing used to write this
    # down, so `conversations.project_id` existed in the schema, was read on every chat turn
    # to decide which project memory to show, and was never once set — which is why two
    # sessions saw the same everything.
    project_binding.adopt(path, made.get("id"), deliberate=True)
    return {**made}


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
    "Update a project — change its name/description, 'paused' to set it aside, "
    "'archived' to file it away. Marking one 'done' is not yours to do, even once "
    "every task under it is finished — that is a judgement about the whole project, "
    "and it stays your person's call. Tell them it looks finished and let them close it.",
    {
        "id": INT,
        "status": {**STR, "enum": [s for s in PROJECT_STATUSES if s != "done"]},
        "name": STR,
        "description": STR,
    },
    required=("id",),
)
def update_project(path: Path, args: dict):

    foreign = _out_of_scope(path, args.get("id"))
    if foreign is not None:
        return foreign
    status = args.get("status")
    if status == "done":
        # The schema already leaves "done" off the enum; this is the backstop for a
        # provider that does not enforce it strictly, or a raw call to the API. A silent
        # drop here would read as "saved" while doing nothing — say plainly why not.
        return {
            "error": "Only a person can mark a project done. Tell them it looks finished "
            "and let them close it themselves."
        }
    out = repo.projects.update_project(path, args["id"], status, args.get("name"), args.get("description"))
    # Pausing or archiving a project takes back the freedom its folder came with, and that
    # has to land now rather than whenever a cache happens to expire.
    if status:
        from kith.infra import permissions

        permissions.forget_linked_projects()
    # Parking a project is the one update that should *not* claim it: a session whose
    # project has just been set aside has nothing left to do there, and binding it would
    # keep it pointed at work nobody wants touched right now.
    if status in (None, "active"):
        project_binding.adopt(path, args["id"])
    return out


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

    foreign = _out_of_scope(path, args.get("project_id"))
    if foreign is not None:
        return foreign
    project_binding.adopt(path, args["project_id"])
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
    # The milestone's project, not an argument — a milestone id alone says nothing about scope.
    milestone = repo.projects.get_milestone(path, int(args["id"])) or {}
    foreign = _out_of_scope(path, milestone.get("project_id"))
    if foreign is not None:
        return foreign
    return repo.projects.update_milestone(
        path, args["id"], args.get("status"), args.get("title"), args.get("target_at")
    )


@tool(
    "link_folder",
    "Point a project at a folder on your person's machine — an existing codebase they want "
    "you working in, or a folder you are about to fill. While the project is active that "
    "folder is yours to work in freely, the same as your own, and its `.kith/memory.md` is "
    "read to you every time. Use the exact path they gave you. Pass no folder to unlink.",
    {
        "id": INT,
        "folder": {
            **STR,
            "description": "Absolute path, or relative to your own folder. Leave it out to "
            "unlink the project from its folder.",
        },
    },
    required=("id",),
)
def link_folder(path: Path, args: dict):
    """Attach a folder to a project, and treat it as his for as long as that project runs.

    The link is the grant. Working in a folder outside his own otherwise prompts on every
    single file — a hundred clicks to do the thing that was just asked for, which is not
    protection, it is a reason to switch the gate off and lose it everywhere. So linking says
    "this folder is yours", and closing the project takes that back.

    Narrower than what already happens, not wider: auto mode allows any non-sensitive write
    anywhere outside the workspace with no record of why. This is one named folder, named by
    them, revoked when the work ends.
    """
    from kith.infra import permissions, project_files
    from kith.services import project_memory

    folder = str(args.get("folder") or "").strip()
    if not folder:
        updated = repo.projects.set_directory(path, args["id"], None)
        permissions.forget_linked_projects()
        if not updated:
            raise ValueError(f"there is no project #{args['id']}")
        return {**updated, "note": "Unlinked. That folder is no longer yours to write in."}

    resolved = Path(sandbox.resolve(folder))
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"{folder} is a file, not a folder")
    existed = resolved.is_dir()
    # A relative path that does not exist is the one shape worth refusing, and it is how this
    # went wrong: `job/the-app` resolves against *his* folder, so pointing at a codebase on
    # the Desktop quietly created an empty `~/Kith/job/the-app` and linked that instead. The
    # note said "created the folder", truthfully, and it still looked like success — the
    # project was linked, it just was not linked to the code. Everything after that happened
    # in the wrong place.
    #
    # Creating is still allowed, because "a folder you are about to fill" is a real use. It
    # just has to be asked for unambiguously, with a path that says where.
    if not existed and not Path(folder).expanduser().is_absolute():
        raise ValueError(
            f"`{folder}` is a relative path and there is no folder there — it would resolve "
            f"to {resolved}, inside your own folder, and I would create an empty one. If you "
            "meant an existing codebase, give the full path. If you really do want a new "
            "folder there, pass the full path and I will make it."
        )
    resolved.mkdir(parents=True, exist_ok=True)

    updated = repo.projects.set_directory(path, args["id"], str(resolved))
    if not updated:
        raise ValueError(f"there is no project #{args['id']}")
    # Invalidated rather than waited out: he will write a file in the next breath, and a
    # five-second stale cache would refuse the first thing he does in a folder he was just
    # given.
    permissions.forget_linked_projects()
    project_memory.ensure(resolved)
    project_files.ensure(resolved)
    project_binding.adopt(path, args["id"], deliberate=True)
    return {
        **updated,
        "memory": str(project_memory.path_for(resolved)),
        "references": str(project_memory.references_path_for(resolved)),
        "note": (
            "Linked to an existing folder. Read what is there before changing it."
            if existed
            else "Created the folder and linked it."
        ),
    }
