"""Parsing the arguments, building the client once, and being the only place that decides how
this process exits.

Two shapes here are deliberate and both are about failure.

**Every command raises; only this function prints.** Commands return an exit code for the
ordinary path and raise `Failure` for anything else. The alternative — each command returning
its own code and each caller propagating it — is the shape where one forgotten ``return`` turns
a failure into a silent success, which for a command another agent is reading the exit code of
is the worst available bug.

**The client is built here, lazily, and shared.** Building it reads the token file, so a
command that does not need a server (``status``, ``install``) must not be made to fail on a
missing one — ``status`` exists precisely to report that it is missing. `needs_client` is how a
command says so.
"""

from __future__ import annotations

import argparse
import sys

from kith import settings as kith_settings
from kith.cli import client as client_module
from kith.cli.commands import chat, conversations, doctor, projects, questions, settings
from kith.cli.errors import INTERNAL, USAGE, Failure, report

#: Printed by `--version` and worth having in a bug report, because the commonest confusion with
#: a CLI installed from a checkout is not knowing which checkout. Read from `kith.settings`
#: rather than kept here: this said 0.1.0 while the app it talks to said 0.5.2, which is a bug
#: report that points at the wrong release.
# Aliased on import: `kith.cli.commands.settings` is the `kith settings` subcommand and would
# otherwise shadow the module this reads.
VERSION = kith_settings.VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kith",
        description=(
            "Talk to a running Kith from the command line. "
            "Most commands act on this directory's conversation, so an id is rarely needed."
        ),
        epilog=(
            "environment:\n"
            "  KITH_URL           where the server is (default: " + client_module.DEFAULT_URL + ")\n"
            "  KITH_TOKEN         the API token, instead of reading it from disk\n"
            "  KITH_CONVERSATION  pin every command in this shell to one conversation\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"kith {VERSION}")
    parser.add_argument(
        "--timeout",
        type=float,
        default=client_module.DEFAULT_TIMEOUT,
        help="seconds to wait between bytes, not for the whole turn",
    )
    subparsers = parser.add_subparsers(dest="command")
    for group in (chat, conversations, questions, projects, settings, doctor):
        group.add(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "run", None):
        parser.print_help()
        return USAGE

    try:
        needed = getattr(args, "needs_client", True)
        connection = client_module.Client(timeout=args.timeout) if needed else None
        return int(args.run(connection, args) or 0)
    except Failure as failure:
        return report(failure)
    except BrokenPipeError:
        # `kith show | head` closes the pipe under us. That is the pipeline working, not a
        # fault, and Python's default handling prints a traceback on exit that makes it look
        # like one. stderr is redirected to devnull so the interpreter's own shutdown message
        # has nowhere to land.
        import os

        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stderr.fileno())
        return 0
    except KeyboardInterrupt:
        # 130 is the shell's own convention for SIGINT. Note this does not stop the turn —
        # Kith keeps going server-side, which is usually what you want; `kith stop` is how you
        # actually stop him, and that distinction is worth a line on screen.
        print("\ndetached — the turn is still running (kith stop to end it)", file=sys.stderr)
        return 130
    except Exception as unexpected:
        print(f"kith: {type(unexpected).__name__}: {unexpected}", file=sys.stderr)
        return INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
