"""Projects: what there is, which one you are standing in, and pinning that by hand.

Read-only, on purpose. The CLI talks *to* Kith; it does not edit his mind. Projects, tasks,
memory and the journal are things he maintains by working, and a second writer would mean two
accounts of the same state with no way to tell which one is the record. ``project use`` is the
exception and it does not touch the server at all — it writes a line in this machine's own CLI
state saying which project this directory means.
"""

from __future__ import annotations

from kith.cli import context, render
from kith.cli.client import Client
from kith.cli.errors import FAILED, Failure


def add(subparsers) -> None:
    listing = subparsers.add_parser("projects", help="what projects there are")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(run=_list)

    project = subparsers.add_parser("project", help="one project, or pin this directory to one")
    inner = project.add_subparsers(dest="what", required=True)

    use = inner.add_parser("use", help="pin this directory to a project")
    use.add_argument("name", help="project name or id")
    use.set_defaults(run=_use)

    show = inner.add_parser("show", help="a project and its roadmap")
    show.add_argument("name", nargs="?", default="", help="default: the one this directory belongs to")
    show.add_argument("--json", action="store_true")
    show.set_defaults(run=_show)


def all_projects(client: Client) -> list[dict]:
    return list((client.get("/projects") or {}).get("projects") or [])


def named(client: Client, wanted: str) -> dict:
    projects = all_projects(client)
    wanted = wanted.strip()
    if wanted.isdigit():
        for project in projects:
            if int(project.get("id") or 0) == int(wanted):
                return project
    for project in projects:
        if str(project.get("name") or "").lower() == wanted.lower():
            return project
    known = ", ".join(str(project.get("name")) for project in projects) or "none"
    raise Failure(f"no project {wanted!r}", FAILED, f"there is: {known}")


def here(client: Client) -> dict | None:
    return context.resolve_project(all_projects(client), context.project_root())


def _list(client: Client, args) -> int:
    projects = all_projects(client)
    if args.json:
        render.emit_json(projects)
        return 0
    if not projects:
        print("no projects")
        return 0
    mine = here(client)
    rows = [
        [
            str(project.get("id") or ""),
            "→" if mine and project.get("id") == mine.get("id") else " ",
            str(project.get("name") or ""),
            str(project.get("status") or ""),
            str(project.get("directory") or ""),
        ]
        for project in projects
    ]
    render.table(rows, ["ID", "", "NAME", "STATUS", "FOLDER"])
    return 0


def _use(client: Client, args) -> int:
    project = named(client, args.name)
    root = context.project_root()
    context.pin(root, int(project.get("id") or 0))
    print(f"{root} → {project.get('name')}")
    return 0


def _show(client: Client, args) -> int:
    project = named(client, args.name) if args.name else here(client)
    if project is None:
        raise Failure(
            "this directory is not in a project",
            FAILED,
            "pin it with: kith project use <name>",
        )
    identifier = int(project.get("id") or 0)
    roadmap = client.get(f"/projects/{identifier}/roadmap") or {}
    if args.json:
        render.emit_json({**project, "roadmap": roadmap})
        return 0
    print(f"{project.get('name')}  ({project.get('status')})")
    if project.get("directory"):
        print(f"  {project.get('directory')}")
    if project.get("description"):
        print(f"  {' '.join(str(project.get('description')).split())}")
    milestones = roadmap.get("milestones") or []
    if milestones:
        print()
        for milestone in milestones:
            print(f"  [{str(milestone.get('status') or '')[:4]:4}] {milestone.get('title')}")
    return 0
