"""The app and the command line have to agree about which Kith this is.

`desktop/package.json` is the number that matters operationally: the release workflow asks
whether `v<version>` is already tagged and builds a dmg when it is not, so that field *is* the
release trigger. The CLI keeps its own copy because a frozen `kith` binary has no package.json
to read, and the copy was hand-bumped — which is how it came to print `kith 0.1.0` against an
app calling itself 0.5.2, for the entire life of the CLI.

Nothing failed. That is the problem with it: the number's only job is to appear in a bug report
and tell somebody which build they are looking at, so a wrong one is not caught by use. It is
caught here or not at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from kith import settings

PACKAGE_JSON = Path(__file__).resolve().parents[2] / "desktop" / "package.json"


def test_the_cli_reports_the_version_the_app_was_released_as():
    if not PACKAGE_JSON.is_file():
        # A server-only checkout is a legitimate way to work on this, and the suite is meant to
        # pass on a tree built from `git ls-files` with no desktop dependencies installed.
        return

    shipped = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]

    assert shipped == settings.VERSION, (
        f"kith.settings.VERSION is {settings.VERSION} and desktop/package.json is {shipped}. "
        "Bump both, or the release is tagged as one version and reports itself as another."
    )


def test_the_command_line_does_not_keep_a_second_copy():
    """`kith --version` has to read the shared constant, not restate it.

    Imported from the module rather than as `from kith.cli import main`, which reaches the
    *function*: `kith/cli/__init__.py` resolves the name `main` through `__getattr__` to
    `main.main`, so the obvious spelling asks a function for an attribute.
    """
    from kith.cli.main import VERSION as reported

    assert reported == settings.VERSION
