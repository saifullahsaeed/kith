"""The CLI may talk to the server over HTTP and may not reach inside it.

A command line that imports `services` would be a second implementation of everything the
server does — a second way to start a conversation, a second place that decides what a project
binding means — and the two would drift, because nothing compares them. The HTTP API is the
contract; this test is what stops the CLI quietly acquiring a private door past it.

``kith.settings`` is the one exception and it is the same exception every layer gets: that
module imports nothing from `kith`, which is precisely what makes it safe to reach for from
anywhere. The CLI needs it to know where the data directory is, and duplicating that logic is
how a frozen binary ends up looking for a token in the directory it was built in.

The constants are here for the other half of the same problem. ``client.HEADER`` and
``client.TOKEN_FILENAME`` must equal the server's, and they are *copied* rather than imported
because importing ``kith.api.auth`` pulls Flask into a process that runs once per message. A
copy with nothing checking it is a copy that goes stale, so this checks it — the same technique
``test_the_client_types_are_not_lying.py`` uses across the TypeScript seam.
"""

from __future__ import annotations

import ast
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "kith" / "cli"

#: The only `kith.*` package the CLI may name.
ALLOWED = {"settings", "cli"}


def _imports(source: Path) -> set[str]:
    """Every `kith.<package>` this file reaches for, function-local imports included.

    The AST rather than the header, for the reason `test_the_layers_point_one_way` walks it:
    a top-level violation fails loudly at import and cannot survive, while one written inside
    a function runs fine forever and is invisible until somebody maps the graph.
    """
    found: set[str] = set()
    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kith"):
            parts = node.module.split(".")
            found.add(parts[1] if len(parts) > 1 else "")
            if len(parts) == 1:
                # `from kith import settings` names the package in the alias, not the module.
                found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("kith."):
                    found.add(alias.name.split(".")[1])
    return {name for name in found if name}


def test_the_cli_reaches_for_nothing_but_settings():
    offenders: dict[str, set[str]] = {}
    for source in sorted(CLI.rglob("*.py")):
        reached = _imports(source) - ALLOWED
        if reached:
            offenders[str(source.relative_to(CLI.parent.parent))] = reached
    assert not offenders, "the CLI is a client and must go through the HTTP API:\n" + "\n".join(
        f"  {where} -> {sorted(what)}" for where, what in offenders.items()
    )


def test_the_token_header_matches_the_server():
    from kith.api import auth
    from kith.cli import client

    assert client.HEADER == auth.HEADER
    assert client.TOKEN_FILENAME == auth.FILENAME


def test_the_config_keys_match_the_route_that_saves_them():
    """`settings set` offers exactly the fields `/api/config` knows how to persist.

    The route keeps a map from wire name to stored column; the CLI keeps a map from wire name
    to a sentence explaining it. Two lists of the same keys, and the failure when they disagree
    is silent in the worst direction: `kith settings set` accepts a key the route drops on the
    floor, prints the new value, and changes nothing.
    """
    from kith.api.routes import config as route
    from kith.cli.commands import settings as command

    assert set(command.CONFIG_KEYS) == set(route._SETTING_KEYS)


def test_importing_the_cli_does_not_import_flask():
    """The import cost is paid once per message, by another agent, in a loop.

    Checked by importing in a subprocess rather than by inspecting `sys.modules` here: the test
    session has already imported half the server, so an in-process check would pass regardless
    of what the CLI actually pulls in.
    """
    import subprocess
    import sys

    probe = (
        "import sys; import kith.cli.main; "
        "heavy = [m for m in sys.modules if m.split('.')[0] in ('flask','apiflask','sqlalchemy','requests')]; "
        "print(','.join(sorted(heavy)))"
    )
    finished = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert finished.returncode == 0, finished.stderr
    assert not finished.stdout.strip(), f"the CLI dragged in: {finished.stdout.strip()}"
