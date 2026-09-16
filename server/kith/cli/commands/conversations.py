"""Finding a conversation, and reading one.

The discovery half of the CLI. Almost always unnecessary — ``kith send`` continues this
directory's thread without anyone naming an id — but "almost always" is not a contract another
agent can rely on, so the way to *ask* has to exist and has to be obvious.

Two fields on every row are why this is worth a command rather than a curl: ``working`` and
``waiting``. Neither is a column. Both are computed at the moment of asking — ``working`` from
``live_turns``, ``waiting`` from the questions queue — and together they are the two things a
second agent needs before it opens its mouth: *is Kith mid-turn* (don't talk over him) and *is
Kith blocked on a person* (nothing will happen until someone answers).
"""

from __future__ import annotations

from kith.cli import context, render
from kith.cli.client import Client
from kith.cli.errors import FAILED, Failure


def add(subparsers) -> None:
    listing = subparsers.add_parser(
        "conversations", aliases=["ls"], help="recent conversations, newest first"
    )
    listing.add_argument("-p", "--project", default="", help="only this project (name or id)")
    listing.add_argument("-l", "--limit", type=int, default=20)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(run=_list)

    show = subparsers.add_parser("show", help="the transcript of a conversation")
    show.add_argument("-c", "--conversation", default="", help="id or prefix (default: this directory's)")
    show.add_argument("--turns", type=int, default=20, help="how many turns from the end")
    show.add_argument("--all", action="store_true", help="the whole thing")
    show.add_argument("--json", action="store_true")
    show.set_defaults(run=_show)

    search = subparsers.add_parser("search", help="find where something was said")
    search.add_argument("query", nargs="+")
    search.add_argument("-l", "--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")
    search.set_defaults(run=_search)

    rename = subparsers.add_parser("rename", help="retitle a conversation")
    rename.add_argument("title", nargs="+")
    rename.add_argument("-c", "--conversation", default="")
    rename.set_defaults(run=_rename)


def _rows(client: Client, limit: int = 500) -> list[dict]:
    return list((client.get("/conversations", limit=limit) or {}).get("conversations") or [])


def _project_names(client: Client) -> dict[int, str]:
    projects = (client.get("/projects") or {}).get("projects") or []
    return {int(row.get("id") or 0): str(row.get("name") or "") for row in projects}


def current(client: Client, wanted: str) -> str:
    """The conversation a read command should act on — the named one, else this directory's."""
    if wanted.strip():
        return context.resolve_id(wanted, [str(row.get("id") or "") for row in _rows(client)])
    from_environment = context.from_environment()
    if from_environment:
        return context.resolve_id(from_environment, [str(row.get("id") or "") for row in _rows(client)])
    sticky = str(context.remembered(context.project_root()).get("conversationId") or "")
    if sticky:
        return sticky
    raise Failure(
        "no conversation for this directory yet",
        FAILED,
        "name one with -c, or start one by sending a message",
    )


def _list(client: Client, args) -> int:
    """Newest first, optionally narrowed to one project.

    The narrowing happens here rather than on the server, and that is a deliberate limit worth
    stating: ``/api/conversations`` has no project filter — the history panel groups client
    side — so this fetches a page and filters it. At the scale this runs at (tens to low
    hundreds of conversations) the whole listing is one small request, and adding an endpoint
    to push one ``where`` clause across the wire would be a second way of asking the same
    question, to keep in step forever, for no time anyone could measure.
    """
    rows = _rows(client, max(args.limit, 100))
    names = _project_names(client)

    if args.project:
        wanted = args.project.strip().lower()
        chosen = {
            identifier
            for identifier, name in names.items()
            if name.lower() == wanted or str(identifier) == wanted
        }
        if not chosen:
            raise Failure(f"no project {args.project!r}", FAILED, "list them with: kith projects")
        rows = [row for row in rows if row.get("projectId") in chosen]

    rows = rows[: args.limit]
    if args.json:
        render.emit_json(rows)
        return 0
    if not rows:
        print("no conversations")
        return 0

    table = []
    for row in rows:
        mark = "●" if row.get("working") else ("?" if row.get("waiting") else " ")
        said = " ".join(str(row.get("lastSaid") or "").split())
        table.append(
            [
                str(row.get("id") or ""),
                mark,
                names.get(int(row.get("projectId") or 0), ""),
                str(row.get("messages") or 0),
                said if len(said) <= 54 else said[:53] + "…",
            ]
        )
    render.table(table, ["ID", "", "PROJECT", "MSGS", "LAST"])
    print()
    print("  ● working   ? waiting on you")
    return 0


def _show(client: Client, args) -> int:
    """A transcript, as turns.

    ``--turns 0`` is the endpoint's own way of saying "all of it", and it exists because the
    default is a page: the largest real conversation here is 22.83 MB and shipping it whole was
    once the default. ``--all`` is spelled out rather than making the user know that 0 means
    everything.
    """
    conversation = current(client, args.conversation)
    detail = client.get(f"/conversations/{conversation}", turns=0 if args.all else args.turns)
    if args.json:
        render.emit_json(detail)
        return 0

    names = _project_names(client)
    project = names.get(int(detail.get("projectId") or 0), "")
    print(f"{detail.get('title') or 'Untitled'}")
    print(
        f"  {conversation}"
        + (f" · {project}" if project else "")
        + f" · {detail.get('messageCount') or 0} messages"
        + f" · turns {detail.get('windowStart') or 0}–{detail.get('turnCount') or 0}"
    )
    print()
    for turn in detail.get("timeline") or []:
        _print_turn(turn)
    if detail.get("hasMore"):
        print(f"  … earlier turns not shown — kith show -c {conversation} --all")
    return 0


def _print_turn(turn: dict) -> None:
    """One turn, as `timeline()` builds it: a role and a list of parts.

    A turn is not a message. `services.conversations.timeline` keeps the shape the interface
    renders — reasoning blocks, the prose between tool rounds, and each call with the result
    it got, in arrival order — because a turn's shape cannot be reconstructed from a paragraph
    once you have thrown away which sentence went with which call.

    Reasoning is dropped here and tool arguments are never expanded. Both are in `--json` for
    anything that wants them. A transcript is read to remember what was decided, and a single
    `write_file` call carries a whole file: printing it buries the decision under the thing
    the decision produced.
    """
    role = str(turn.get("role") or "assistant")
    who = {"user": "you ", "system": "sys "}.get(role, "kith")
    body: list[str] = []
    tools: list[str] = []
    for part in turn.get("parts") or []:
        kind = str(part.get("kind") or "")
        if kind == "text":
            said = str(part.get("text") or "").strip()
            if said:
                body.append(said)
        elif kind == "tool":
            tools.append(str(part.get("name") or "?"))

    if not body and not tools:
        return
    first = True
    for block in body:
        for line in block.splitlines():
            print(f"{who if first else '    '} {line}")
            first = False
    if tools:
        # Collapsed to names and counts: eleven `read_file` calls in one turn is one useful
        # fact, not eleven lines of it.
        counted: dict[str, int] = {}
        for name in tools:
            counted[name] = counted.get(name, 0) + 1
        summary = ", ".join(name + (f" ×{n}" if n > 1 else "") for name, n in counted.items())
        print(f"{'     ' if not first else who + ' '}· {summary}")
    print()


def _search(client: Client, args) -> int:
    query = " ".join(args.query)
    hits = (client.get("/conversations/search", q=query, limit=args.limit) or {}).get("hits") or []
    if args.json:
        render.emit_json(hits)
        return 0
    if not hits:
        print(f"nothing said about {query!r}")
        return 0
    for hit in hits:
        print(f"{hit.get('conversationId')}  {hit.get('title')}")
        print(f"  {hit.get('role')}: {' '.join(str(hit.get('snippet') or '').split())}")
        print()
    return 0


def _rename(client: Client, args) -> int:
    conversation = current(client, args.conversation)
    client.patch(f"/conversations/{conversation}", {"title": " ".join(args.title)})
    print("renamed")
    return 0
