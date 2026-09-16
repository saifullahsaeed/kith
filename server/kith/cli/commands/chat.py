"""Saying something to Kith, and everything that happens around a turn in flight.

``send`` is the command this whole package exists for, and almost all of its complexity is in
deciding *where* the message goes rather than in sending it. See `kith.cli.context` for the
resolution order; what lives here is the consequence of it, which is a single rule:

    a conversation is created and bound in one request, or not bound at all.

There is no second call. ``POST /api/chat`` takes ``conversationId`` and ``projectId``
together, opens the conversation if the first is absent, and binds it before the prompt is
built — which is the only ordering where the opening turn is assembled with the project's
memory in it rather than a guess. Splitting that into "create, then bind" is exactly the bug
the web client shipped for months.
"""

from __future__ import annotations

import sys
from pathlib import Path

from kith.cli import context, render
from kith.cli.client import Client
from kith.cli.errors import FAILED, USAGE, Failure


def add(subparsers) -> None:
    send = subparsers.add_parser(
        "send",
        help="send a message and stream the reply",
        description=(
            "Continues this directory's conversation, starting one if there is none. "
            "Reads the message from stdin when it is not given as an argument."
        ),
    )
    send.add_argument("message", nargs="*", help="what to say (or pipe it in)")
    send.add_argument("-c", "--conversation", default="", help="target a conversation (id or prefix)")
    send.add_argument("-n", "--new", action="store_true", help="start a new conversation instead")
    send.add_argument("-p", "--project", default="", help="project name or id (new conversations only)")
    send.add_argument("--json", action="store_true", help="pass the raw NDJSON event stream through")
    send.add_argument("--quiet", action="store_true", help="answer only — no reasoning, tools or totals")
    send.set_defaults(run=_send)

    new = subparsers.add_parser("new", help="start a conversation and print its id")
    new.add_argument("title", nargs="*", help="what to call it")
    new.add_argument("-p", "--project", default="", help="project name or id")
    new.add_argument("--json", action="store_true")
    new.set_defaults(run=_new)

    attach = subparsers.add_parser(
        "attach",
        help="join a turn already running",
        description=(
            "Streams a turn that is already under way — the same way the desktop window "
            "rejoins one after a reload. Useful for watching what Kith is doing in a "
            "conversation somebody else started."
        ),
    )
    attach.add_argument("-c", "--conversation", default="")
    attach.add_argument("--json", action="store_true")
    attach.add_argument("--quiet", action="store_true")
    attach.set_defaults(run=_attach)

    stop = subparsers.add_parser("stop", help="stop the turn running in a conversation")
    stop.add_argument("-c", "--conversation", default="")
    stop.set_defaults(run=_stop)

    steer = subparsers.add_parser(
        "steer",
        help="correct a turn without stopping it",
        description="Delivered between rounds, so it redirects the work rather than killing it.",
    )
    steer.add_argument("text", nargs="+")
    steer.add_argument("-c", "--conversation", default="")
    steer.set_defaults(run=_steer)


# --------------------------------------------------------------------------- #


def _message_from(args) -> str:
    """The message, from the arguments or from stdin — but never silently from a tty.

    ``kith send`` with no argument and no pipe would otherwise sit waiting for input with no
    prompt and nothing on screen, which reads as a hang. Checking ``isatty`` turns that into a
    usage error, and leaves the piped case working.
    """
    spoken = " ".join(args.message).strip()
    if spoken:
        return spoken
    if sys.stdin.isatty():
        raise Failure("nothing to say", USAGE, "pass a message, or pipe one in")
    piped = sys.stdin.read().strip()
    if not piped:
        raise Failure("nothing to say", USAGE, "stdin was empty")
    return piped


def _projects(client: Client) -> list[dict]:
    return list((client.get("/projects") or {}).get("projects") or [])


def _named_project(client: Client, wanted: str) -> dict:
    """A project by id or by name, case-insensitively, or a failure listing what there is."""
    projects = _projects(client)
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


def _known_ids(client: Client) -> list[str]:
    listing = client.get("/conversations", limit=500) or {}
    return [str(row.get("id") or "") for row in listing.get("conversations") or []]


def _target(client: Client, args, *, allow_new: bool) -> tuple[str, Path, dict | None]:
    """Which conversation, which directory, and which project if one is being opened.

    Returns an empty conversation id when a new one is to be opened — the server allocates it,
    so inventing one here would mean guessing at a format the server owns.
    """
    directory = context.project_root()

    explicit = (args.conversation or "").strip()
    if explicit:
        return context.resolve_id(explicit, _known_ids(client)), directory, None

    if getattr(args, "new", False):
        project = _named_project(client, args.project) if args.project else None
        if project is None:
            project = context.resolve_project(_projects(client), directory)
        return "", directory, project

    from_environment = context.from_environment()
    if from_environment:
        return context.resolve_id(from_environment, _known_ids(client)), directory, None

    sticky = str(context.remembered(directory).get("conversationId") or "")
    if sticky:
        # Verified against the server rather than trusted. A remembered conversation can have
        # been deleted from the app since, and sending to a dead id would open a *new*
        # conversation server-side under a stale local pointer — the message would land
        # somewhere real and nothing on screen would say where.
        if sticky in _known_ids(client):
            return sticky, directory, None
        context.forget(directory)

    project = _named_project(client, args.project) if args.project else None
    if project is None:
        project = context.resolve_project(_projects(client), directory)
    return "", directory, project


def _stream(client: Client, args, body: dict, directory: Path | None) -> int:
    events = client.stream("/chat", body)
    if args.json:
        code, conversation = render.passthrough(events)
    else:
        code, conversation = render.human(events, quiet=getattr(args, "quiet", False))
    if conversation and directory is not None:
        context.remember(directory, conversation, body.get("projectId"))
    return code


def _send(client: Client, args) -> int:
    message = _message_from(args)
    conversation, directory, project = _target(client, args, allow_new=True)
    # Where this was typed. The CLI resolves which conversation to continue from the working
    # directory and used to send nothing about it, so a question about "this repo" reached
    # someone with no way to know which folder that was — measured once at a whole turn spent
    # searching the disk for a path that was sitting in a local variable here.
    body: dict = {"messages": [{"role": "user", "content": message}], "cwd": str(Path.cwd())}
    if conversation:
        body["conversationId"] = conversation
    elif project:
        # Only on the first message of a new conversation. `set_project` refuses to move an
        # existing binding, so sending this on a resumed chat is at best ignored and at worst
        # misleading to read in a request log.
        body["projectId"] = int(project.get("id") or 0)
    return _stream(client, args, body, directory)


def _new(client: Client, args) -> int:
    title = " ".join(args.title).strip()
    opened = client.post("/conversations", {"firstMessage": title})
    conversation = str(opened.get("id") or "")
    if args.project:
        project = _named_project(client, args.project)
        client.put(f"/conversations/{conversation}/project", {"projectId": int(project.get("id") or 0)})
        opened = client.get(f"/conversations/{conversation}")
    context.remember(context.project_root(), conversation, opened.get("projectId"))
    if args.json:
        render.emit_json(opened)
    else:
        # The bare id and nothing else, so `ID=$(kith new)` is the obvious thing it looks like.
        print(conversation)
    return 0


def _attach(client: Client, args) -> int:
    conversation, _directory, _project = _target(client, args, allow_new=False)
    if not conversation:
        raise Failure("no conversation to attach to", FAILED, "name one with -c, or start one with: kith new")
    events = client.stream(f"/chat/{conversation}/attach")
    code, _ = render.passthrough(events) if args.json else render.human(events, quiet=args.quiet)
    return code


def _stop(client: Client, args) -> int:
    conversation, _directory, _project = _target(client, args, allow_new=False)
    if not conversation:
        raise Failure("no conversation to stop", FAILED)
    answer = client.post(f"/chat/{conversation}/stop")
    print("stopping" if (answer or {}).get("stopping") else "nothing was running")
    return 0


def _steer(client: Client, args) -> int:
    """Redirect a running turn — or, if none is running, say it as an ordinary message.

    The fallback is the endpoint's own instruction rather than an invention here: it answers
    ``{"steering": false}`` when there is no turn and says the caller should send the text as a
    message instead, which is what the window does. Refusing here would make ``kith steer`` a
    command whose success depends on losing a race with a turn that may have finished a second
    ago — and the thing the person wanted said would simply not be said.
    """
    conversation, directory, _project = _target(client, args, allow_new=False)
    if not conversation:
        raise Failure("no conversation to steer", FAILED)
    said = " ".join(args.text).strip()
    answer = client.post(f"/chat/{conversation}/steer", {"message": said}) or {}
    if answer.get("steering"):
        waiting = int(answer.get("waiting") or 0)
        print(f"steered ({waiting} waiting)" if waiting > 1 else "steered")
        return 0
    print("no turn was running — sending it as a message instead", file=sys.stderr)
    body = {"conversationId": conversation, "messages": [{"role": "user", "content": said}]}
    return _stream(client, _Plain(), body, directory)


class _Plain:
    """The rendering options `steer`'s fallback turn runs with: human output, nothing hidden."""

    json = False
    quiet = False
