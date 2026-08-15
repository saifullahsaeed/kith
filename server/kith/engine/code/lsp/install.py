"""Getting a language server, for the one language this project is actually written in.

The rule the whole module exists to keep: **nothing is installed until something needs it, and
then only that.** A hundred megabytes of pyright is worth it on a Python project and is waste
on every other machine, so the trigger is the code in front of him rather than a setup step.

**Into a folder we own.** `~/.kith/language-servers/`, never the application bundle. The
server is frozen by PyInstaller and codesigned; writing inside the `.app` invalidates the
signature and there is no pip in there anyway. Never globally either — `npm i -g` on someone's
machine to make our feature work is a decision that was not ours to make. A folder we own is
reversible by deleting it.

**npm, for the three families it can serve.** Measured rather than assumed: `npm install
--prefix <dir>` puts binaries in `<dir>/node_modules/.bin`, which is *already* one of the
directories discovery searches, so nothing new has to be taught how to find them. And the npm
registry serves `pyright-langserver` as well as `typescript-language-server` and
`intelephense` — so one mechanism, one toolchain, covers Python, TypeScript/JavaScript/TSX and
PHP. Verified: an npm-installed `pyright-langserver` starts and asks for `--stdio`, which is
the right complaint from a working binary. 34 MB.

**The rest are named, not installed, and that is not a gap to close later.** `gopls` wants a Go
toolchain, `rust-analyzer` is a rustup component, `ruby-lsp` is a gem, `clangd` comes from a
system package manager. Each installs into a location its own ecosystem owns, and redirecting
them into our folder would be fighting four package managers to save the user one command.
They keep the existing behaviour: a sentence saying which server and how to get it.

**The command is never composed from anything a model said.** `_NPM_PACKAGES` is a closed
table of literals. A tool that took a package name would be remote code execution wearing a
helpful hat — the model reads repositories, and a repository can ask it to install something.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

from kith.engine.code import outline, repomap
from kith.settings import DATA_DIR

#: The closed set. Family name (as `outline.LANGUAGES` spells it) to the npm package that
#: serves it. Adding a language means editing this table in a reviewed commit, which is the
#: point — see the note about composed commands above.
_NPM_PACKAGES: dict[str, str] = {
    "python": "pyright",
    "typescript": "typescript-language-server typescript",
    "javascript": "typescript-language-server typescript",
    "tsx": "typescript-language-server typescript",
    "php": "intelephense",
}

#: Roughly what lands on disk, measured by installing each into an empty prefix and running
#: `du -sh`. Shown to whoever is being asked to approve it, because "install a language
#: server" and "install 148 MB" are different questions and only one of them can be answered.
#: Approximate on purpose — it moves with every release of the package, and the point is the
#: order of magnitude rather than a number to hold anyone to.
_SIZES: dict[str, str] = {
    "pyright": "about 34 MB",
    "typescript-language-server typescript": "about 32 MB",
    "intelephense": "about 148 MB",
}

#: How long an install may take. npm fetching pyright is 34 MB over a network someone else
#: owns; a minute is optimistic and five is patient enough not to fail on a hotel connection.
INSTALL_TIMEOUT = 300.0

#: A family needs this many files before it counts as what the project is written in. One
#: stray `setup.py` in a TypeScript repository is not a reason to fetch pyright.
MIN_FILES = 2


def prefix() -> Path:
    """Where servers we installed live. Outside the app bundle, inside the user's data."""
    return DATA_DIR / "language-servers"


def installable(family: str) -> str | None:
    """The npm package serving this family, or None when we do not install it."""
    return _NPM_PACKAGES.get(family)


def download_size(family: str) -> str:
    """Roughly how much this one costs on disk, in words. "" when we do not install it."""
    return _SIZES.get(installable(family) or "", "")


def command_for(family: str) -> str | None:
    """The exact shell command that would install it, or None.

    Returned rather than run so the caller can put it in front of a person before anything
    happens. It is also what the permission gate sees, which means the thing approved and the
    thing executed are the same string.
    """
    package = installable(family)
    if not package:
        return None
    return f"npm install --prefix {prefix()} --no-fund --no-audit --silent {package}"


def languages_in(root: str | Path) -> dict[str, int]:
    """What this project is written in, as family to file count.

    From the files rather than from configuration, because configuration is the thing nobody
    fills in. Uses the same walk as the repo map, so it honours the project's own `.gitignore`
    — a vendored `node_modules` full of JavaScript is not a reason to call this a JS project.
    """
    counted: Counter[str] = Counter()
    for path in repomap.candidates(Path(str(root))):
        family = outline.language_for(path.name)
        if family:
            counted[family] += 1
    return dict(counted)


def wanted_for(root: str | Path, available) -> list[str]:
    """Families this project is written in that have no server and that we could install.

    `available` is asked per family — `Manager.available_for` in practice — and is a parameter
    rather than an import so this stays a function over facts. Ordered by how much of the
    project is in that language, so if only one thing is going to be installed it is the one
    that matters most.
    """
    counted = languages_in(root)
    ranked = sorted(counted.items(), key=lambda pair: -pair[1])
    return [
        family
        for family, count in ranked
        if count >= MIN_FILES and installable(family) and not available(family)
    ]


def run(family: str) -> tuple[bool, str]:
    """Install the server for one family. Returns (worked, what happened).

    Does no permission checking of its own — that is a decision, and decisions belong to the
    adapter above, which has already put `command_for` in front of a person.
    """
    command = command_for(family)
    if not command:
        return False, f"there is no server I install for {family}"
    target = prefix()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create {target}: {exc}"

    from kith.infra import executables

    npm = executables.which("npm")
    if not npm:
        return (
            False,
            "npm is not on this machine, and it is what installs this one. Install Node, or install the server yourself.",
        )
    try:
        done = subprocess.run(
            [
                npm,
                "install",
                "--prefix",
                str(target),
                "--no-fund",
                "--no-audit",
                "--silent",
                *installable(family).split(),
            ],
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"the install took longer than {int(INSTALL_TIMEOUT)}s and was stopped"
    except OSError as exc:
        return False, f"could not run npm: {exc}"
    if done.returncode != 0:
        return False, (done.stderr or done.stdout or "npm failed").strip()[:400]
    return True, f"installed into {target}"
