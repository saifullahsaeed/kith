"""Where am I, and how do I get onto PATH.

``status`` exists because every failure this CLI can have looks the same from outside — a 401,
a refused connection and a stale sticky pointer all read as "it didn't work". It answers the
four questions that separate them: is anything listening, which token file was used, which
project this directory is, and which conversation ``send`` would continue.

``install`` is the packaging half, and it does the same job in two quite different worlds:

* **From a checkout**, a frozen binary would be wrong — you would have to rebuild it to see
  your own edits. So it writes a two-line launcher that execs the repository's virtualenv,
  and the CLI tracks the source the way ``./run dev`` does.
* **Frozen**, there is a real binary in a bundle and the right move is a symlink to it, which
  is what ``code`` does on macOS and what keeps an app update from leaving a stale copy behind.

``~/.local/bin`` rather than ``/usr/local/bin`` in both cases, because it needs no sudo — and
that is a real trade rather than a free win. **On macOS it is not on PATH by default.** The
system PATH comes from ``/etc/paths`` — ``/usr/local/bin``, ``/usr/bin``, ``/bin``,
``/usr/sbin``, ``/sbin`` — and nothing in the default zsh profile adds a home directory to it.
Linux distributions generally do; macOS does not, and macOS is what this runs on.

So the PATH check below is not a courtesy for an unusual setup. It is the expected outcome on
a clean Mac, and the install is only half-finished without acting on what it says. An install
that reports success and leaves nothing runnable is the worst of the available outcomes, which
is why the line to paste is printed rather than assumed.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from kith import settings
from kith.cli import context
from kith.cli.client import Client, base_url, find_token
from kith.cli.errors import FAILED, Failure

#: Where the launcher goes. Overridable for anyone who keeps their own bin elsewhere.
BIN = Path.home() / ".local" / "bin"

#: The block `--claude` maintains in CLAUDE.md, between markers so it can be rewritten in
#: place rather than appended to on every install.
OPEN_MARK = "<!-- kith-cli -->"
CLOSE_MARK = "<!-- /kith-cli -->"

GUIDE = """\
## Kith

Kith is a second agent running on this machine, with his own memory, projects and
conversations. Talk to him with the `kith` command.

- `kith send "..."` — continue **this directory's** conversation with him and stream the reply.
  You almost never need a conversation id: the directory picks the thread.
- `kith send "..." --json` — the same, as an NDJSON event stream, one JSON object per line
  (`delta`, `tool_call`, `tool_result`, `stats`, `error`, `done`).
- `kith conversations` — recent chats. `working` means he is mid-turn; `waiting` means he is
  blocked on a person. Don't send into a conversation that is `working`.
- `kith show` — the transcript of the current conversation. `kith search "..."` — find where
  something was said.
- `kith projects`, `kith settings`, `kith status`.

He can read and edit files, run commands and do long jobs on his own. Ask him for the parts of
the work you are not doing — he keeps context across sessions that you do not.
"""


def add(subparsers) -> None:
    status = subparsers.add_parser("status", help="is Kith up, and where am I")
    status.add_argument("--json", action="store_true")
    status.set_defaults(run=_status, needs_client=False)

    install = subparsers.add_parser("install", help="put `kith` on PATH")
    install.add_argument("--prefix", default=str(BIN), help=f"where to put it (default: {BIN})")
    install.add_argument("--claude", action="store_true", help="also tell Claude Code that Kith exists")
    install.add_argument(
        "--claude-md",
        default=str(Path.home() / ".claude" / "CLAUDE.md"),
        help="which CLAUDE.md to write the block into",
    )
    install.set_defaults(run=_install, needs_client=False)


# --------------------------------------------------------------------------- #


def _status(client: Client | None, args) -> int:
    """Report, never fail. A diagnostic that exits non-zero on the thing it was run to
    diagnose tells you nothing you did not already know."""
    from kith.cli import render

    url = base_url()
    token_source = ""
    try:
        _value, token_source = find_token()
    except Failure:
        token_source = ""

    # Built here rather than by `main`, because this is the one command that must run when
    # there is no token to build a client with — that is a thing it reports, not a thing that
    # stops it.
    probe: Client | None = None
    listening = False
    authorised = False
    if token_source:
        try:
            probe = Client(timeout=5.0)
            # Two probes, not one, because they fail differently and the difference is the
            # whole point of this command. `/api/health` is in the server's unauthenticated
            # allow-list — the launcher polls it before a window exists — so it answers
            # cheerfully whatever token you hold. Reporting "up" on the strength of it is how
            # this command told a frozen CLI holding `~/.kith/api.token` that a checkout's
            # server on the same port was fine, when every real call would 401. So liveness
            # is asked of `/health` and permission is asked of something that checks.
            listening = probe.alive()
            if listening:
                probe.get("/config")
                authorised = True
        except Failure:
            authorised = False
    reachable = listening and authorised

    root = context.project_root()
    project = None
    conversation = str(context.remembered(root).get("conversationId") or "")
    live = ""
    if reachable and probe is not None:
        try:
            projects = (probe.get("/projects") or {}).get("projects") or []
            project = context.resolve_project(projects, root)
            if conversation:
                rows = (probe.get("/conversations", limit=500) or {}).get("conversations") or []
                match = next((row for row in rows if row.get("id") == conversation), None)
                if match is None:
                    live = "gone — will start a new one"
                elif match.get("working"):
                    live = f"{match.get('messages') or 0} messages · working"
                elif match.get("waiting"):
                    live = f"{match.get('messages') or 0} messages · waiting on you"
                else:
                    live = f"{match.get('messages') or 0} messages · idle"
        except Failure:
            pass

    if args.json:
        render.emit_json(
            {
                "url": url,
                "reachable": reachable,
                "listening": listening,
                "authorised": authorised,
                "tokenSource": token_source,
                "directory": str(root),
                "project": project,
                "conversationId": conversation,
                "conversation": live,
                "installed": str(_installed_at() or ""),
            }
        )
        return 0

    rows = [
        ["server", _server_line(listening, authorised, url)],
        ["token", token_source or "not found — is Kith installed?"],
        ["project", f"{project.get('name')}  ({project.get('directory')})" if project else f"none · {root}"],
        ["conversation", f"{conversation} · {live}" if conversation else "none yet for this directory"],
    ]
    installed = _installed_at()
    if installed:
        rows.append(["installed", str(installed)])
    render.table(rows)
    if not listening:
        print()
        print("  start it with: ./run server")
    elif not authorised:
        print()
        print("  something is listening but rejected the token — a different Kith is on this port,")
        print("  or this CLI read the token of a different install. Both paths are printed above.")
    return 0


def _server_line(listening: bool, authorised: bool, url: str) -> str:
    where = url.removeprefix("http://")
    if not listening:
        return f"down · {where}"
    if not authorised:
        return f"listening but rejecting this token · {where}"
    return f"up · {where}"


def _installed_at() -> Path | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / "kith"
        if candidate.exists():
            return candidate
    return None


def _launcher_text() -> str:
    """The shell script that runs this CLI from a checkout.

    ``PYTHONPATH`` rather than an editable install: the package is not installed into the
    virtualenv (nothing here is — ``make venv`` only installs requirements), so the import has
    to be told where ``server/`` is. Prepended rather than replacing an existing value, so
    running ``kith`` from inside another project's shell does not break that project's imports.
    """
    server_root = settings.SERVER_ROOT
    python = server_root / ".venv" / "bin" / "python"
    if not python.exists():
        raise Failure(
            f"no virtualenv at {python}",
            FAILED,
            "build one first with: make venv",
        )
    return (
        "#!/bin/sh\n"
        "# Generated by `kith install` — runs the CLI from the checkout, so it tracks edits.\n"
        f'PYTHONPATH="{server_root}${{PYTHONPATH:+:$PYTHONPATH}}" exec "{python}" -m kith.cli "$@"\n'
    )


def _install(client: Client | None, args) -> int:
    target = Path(args.prefix).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    link = target / "kith"

    if settings.FROZEN:
        # A symlink, not a copy: an app update replaces the binary in the bundle and a copy
        # here would keep running the old one, silently, until someone noticed the version.
        binary = Path(sys.executable).resolve()
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(binary)
        how = f"symlink → {binary}"
    else:
        link.write_text(_launcher_text())
        link.chmod(link.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        how = f"launcher → {settings.SERVER_ROOT}"

    print(f"installed  {link}")
    print(f"           {how}")

    on_path = str(target) in os.environ.get("PATH", "").split(os.pathsep)
    if not on_path:
        print()
        print(f"  {target} is not on your PATH. Add this to your shell profile:")
        print(f'    export PATH="{target}:$PATH"')

    if args.claude:
        written = _write_guide(Path(args.claude_md).expanduser())
        print()
        print(f"{'updated' if written else 'already current'}  {args.claude_md}")
        print("           Claude Code will now know `kith` exists and how to use it")
    return 0


def _write_guide(path: Path) -> bool:
    """Put the usage block into a CLAUDE.md, replacing an older copy rather than stacking.

    A binary on PATH is invisible to a model — nothing in a fresh session says it is there, so
    it goes unused however good it is. That failure has a precedent in this codebase worth
    naming: ``send_builder`` shipped, worked, and was called zero times across two full runs,
    because the decision to use it is lost at framing rather than at the call. A tool nothing
    mentions is a tool nobody reaches for.
    """
    block = f"{OPEN_MARK}\n{GUIDE}{CLOSE_MARK}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    if OPEN_MARK in existing and CLOSE_MARK in existing:
        head = existing[: existing.index(OPEN_MARK)]
        tail = existing[existing.index(CLOSE_MARK) + len(CLOSE_MARK) :].lstrip("\n")
        updated = head + block + tail
    else:
        updated = (existing.rstrip("\n") + "\n\n" if existing.strip() else "") + block
    if updated == existing:
        return False
    path.write_text(updated)
    return True
