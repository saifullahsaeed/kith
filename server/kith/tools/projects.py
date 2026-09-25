"""Projects: a name, a folder, and the tasks filed under it."""

from __future__ import annotations

from pathlib import Path

from kith.domain.enums import PROJECT_STATUSES
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
    "Start a project — a bigger goal that groups several tasks. Use this when work is more "
    "than a single task. If it involves code or files, give it a `directory`: that folder is "
    "where the work lives and where the project keeps what it learns about itself, in "
    "`.kith/memory.md`, which you are shown every time you work there. Then plan_work its first "
    "few tasks — a project with no tasks has nothing to hand you when you come back to it.",
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
    "See your projects and how many of their tasks are planning, approved, working and done.",
    {**PAGE_PARAMS},
    required=(),
)
def list_projects(path: Path, args: dict):
    """Projects with a count of their tasks in each column — the board, summarised."""
    return paging.page(repo.projects.project_overview(path), args, default=5)


@tool(
    "update_project",
    "Update a project — change its name/description, 'paused' to set it aside, "
    "'archived' to file it away. Marking one 'done' is not yours to do, even once "
    "every task under it is finished — that is a judgement about the whole project, "
    "and it stays your person's call. Tell them it looks finished and let them close it. "
    "`directory` points the project at a folder on your person's machine — an existing "
    "codebase they want you working in, or one you are about to fill. While the project is "
    "active that folder is yours to work in freely, the same as your own, and its "
    "`.kith/memory.md` is read to you every time. Use the exact path they gave you; pass an "
    "empty string to unlink.",
    {
        "id": INT,
        "status": {**STR, "enum": [s for s in PROJECT_STATUSES if s != "done"]},
        "name": STR,
        "description": STR,
        "directory": {
            **STR,
            "description": "The folder this project's work lives in — absolute, or relative "
            "to your own folder. An empty string unlinks it.",
        },
    },
    required=("id",),
)
def update_project(path: Path, args: dict):
    """A project's folder is a field on the project, so setting it is an update.

    It was `link_folder`, a whole schema for one column, and `create_project` had taken a
    `directory` from the beginning — so the same fact was a parameter on the way in and a tool
    of its own thereafter. The grant that comes with it is unchanged; see `_link_folder`, which
    is where all of that still lives.

    `directory` is not an ordinary column write — it creates folders, seeds `.kith/`,
    invalidates the permission cache and claims the conversation — so it is done by
    `_link_folder`. It used to *return* from there, which silently threw away any `name`,
    `description` or `status` sent in the same call, and took two guards with it: a project
    could be marked `done` (which only a person may do) and a project could be `paused` while
    still being claimed by this conversation, the one update the code below says must not claim
    it. The schema advertises all five fields with only `id` required and says nothing about
    exclusivity, so a caller setting two of them was doing what it was told.
    """
    foreign = _out_of_scope(path, args.get("id"))
    if foreign is not None:
        return foreign
    linked: dict | None = None
    if "directory" in args:
        linked = _link_folder(path, args)
        # An error from linking is the whole answer: the folder is what the rest of the call is
        # about, and half-applying the other fields onto a project whose link failed is worse.
        if isinstance(linked, dict) and linked.get("error"):
            return linked
        if not any(args.get(key) is not None for key in ("status", "name", "description")):
            return linked
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
    # Both halves, when both ran: the caller asked for the link *and* the column changes, and a
    # result carrying only one of them reads as the other having been ignored. `out` can be
    # falsy — `update_project` returns nothing for a project that is not there — in which case
    # the link is the whole answer.
    if linked and isinstance(out, dict):
        return {**linked, **out}
    return out or linked


def _link_folder(path: Path, args: dict):
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

    folder = str(args.get("directory") or "").strip()
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
