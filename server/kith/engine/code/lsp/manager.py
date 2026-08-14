"""Finding a language server, keeping it warm, and doing without one.

Three problems, and the third is the important one.

**Finding.** Nobody is going to configure this. The server that can answer a question about a
file is almost always already sitting in the project — `typescript-language-server` in its
`node_modules/.bin`, `pyright` in its virtualenv — because the project's own tooling put it
there. So discovery looks in the project first and on `PATH` last, which also means the
answer comes from the version the project actually pins rather than whatever is global.

**Keeping warm.** Starting pyright against a real repository costs seconds, and it is the
same seconds every call. Servers are cached per (project root, language) and reused for the
life of the process, with a ceiling: each one is a hundred-odd megabytes, and a session that
wandered through nine repositories should not be holding nine of them.

**Doing without.** Most machines have none of these installed, and that has to be an ordinary
condition rather than a broken one. Two consequences. An absent server produces a sentence
saying which one and how to get it — not a stack trace, and not silence. And the semantic
tools are *not offered to the model at all* when nothing can serve them: a tool schema costs
tokens on every round of every turn, and paying that to advertise a capability that will
always answer "not installed" is precisely the waste the toolset lists exist to prevent.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from kith.engine.code.lsp.client import LanguageServer, LSPError

#: Files that mean "a project starts here". Ordered by how specific they are: a `package.json`
#: beside a file is a better root than the `.git` twelve levels up, and for a monorepo it is
#: the only one that gives the server the right `tsconfig`.
ROOT_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "tsconfig.json",
    "setup.py",
    "setup.cfg",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
    ".git",
)


@dataclass(frozen=True)
class Candidate:
    """One way a language might be served."""

    #: Executable name to look for.
    binary: str
    #: Arguments that put it in stdio language-server mode.
    args: tuple[str, ...]
    #: What to tell someone who does not have it.
    install: str


#: Language to the servers that can serve it, best first. `language` here is the tree-sitter
#: family name from `code/outline.py`, so one entry covers `.ts`, `.tsx` and `.js`.
CANDIDATES: dict[str, tuple[Candidate, ...]] = {
    "python": (
        Candidate("pyright-langserver", ("--stdio",), "pip install pyright"),
        Candidate("basedpyright-langserver", ("--stdio",), "pip install basedpyright"),
        Candidate("pylsp", (), "pip install python-lsp-server"),
        Candidate("jedi-language-server", (), "pip install jedi-language-server"),
    ),
    "typescript": (
        Candidate(
            "typescript-language-server", ("--stdio",), "npm i -g typescript-language-server typescript"
        ),
    ),
    "go": (Candidate("gopls", ("serve",), "go install golang.org/x/tools/gopls@latest"),),
    "rust": (Candidate("rust-analyzer", (), "rustup component add rust-analyzer"),),
    "ruby": (Candidate("ruby-lsp", (), "gem install ruby-lsp"),),
    "php": (Candidate("intelephense", ("--stdio",), "npm i -g intelephense"),),
    "c": (Candidate("clangd", (), "install clangd (brew install llvm)"),),
}

#: The tree-sitter language names that map onto one server family. `tsx` and `javascript` are
#: all served by typescript-language-server; `cpp` by clangd alongside `c`.
_FAMILY = {
    "python": "python",
    "typescript": "typescript",
    "tsx": "typescript",
    "javascript": "typescript",
    "go": "go",
    "rust": "rust",
    "ruby": "ruby",
    "php": "php",
    "c": "c",
    "cpp": "c",
}

#: What a marker file says the project is written in. Used to decide whether the semantic
#: tools are worth their schema — see `Manager.any_available`.
_MARKER_LANGUAGES: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "requirements.txt": "python",
    "package.json": "typescript",
    "tsconfig.json": "typescript",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "Gemfile": "ruby",
    "composer.json": "php",
}

#: Where a project keeps executables, relative to its root. Checked in this order.
_PROJECT_BINS = (
    "node_modules/.bin",
    ".venv/bin",
    "venv/bin",
    "env/bin",
    ".venv/Scripts",
    "venv/Scripts",
)

#: How long a server that would not start is left alone before it is tried again. Short
#: enough that installing the thing and asking again just works; long enough that a
#: genuinely broken server is not re-launched on every tool call.
BROKEN_FOR = 180.0

#: Most servers to keep running at once. Each is real memory; past a handful, a session has
#: wandered and the oldest is not coming back.
MAX_LIVE = 4


class Unavailable(RuntimeError):
    """No server for this file, with a sentence about what would fix it."""


class Manager:
    """Every language server this process is talking to."""

    def __init__(self) -> None:
        self._servers: dict[tuple[str, str], LanguageServer] = {}
        self._order: list[tuple[str, str]] = []
        self._lock = threading.RLock()
        #: (root, family) that failed to start, and why. Retried on a later call only if the
        #: binary changes — a server that crashes on startup crashes every time, and trying
        #: again on each tool call turns one slow failure into a slow failure per call.
        #: (root, family) that failed to start, with when and why. Time-boxed rather than
        #: permanent — the usual reason a server would not start is that it was not really
        #: installed, and the usual fix is to install it. Remembering the failure forever
        #: would mean that fix needs a restart to take effect, which nobody would guess.
        self._broken: dict[tuple[str, str], tuple[float, str]] = {}

    # -- discovery ---------------------------------------------------------- #

    def project_root(self, path: str | Path) -> Path:
        """The folder a language server should treat as the project.

        Nearest marker wins, walking up. Falls back to the file's own directory, which is a
        perfectly good root for a loose script and stops a stray `.git` in a home directory
        from making someone's whole home the project.
        """
        target = Path(str(path)).resolve()
        here = target if target.is_dir() else target.parent
        for folder in [here, *here.parents]:
            if any((folder / marker).exists() for marker in ROOT_MARKERS):
                return folder
            if folder.parent == folder or folder == Path.home():
                break
        return here

    def family_for(self, path: str | Path) -> str:
        from kith.engine.code import outline

        language = outline.language_for(path)
        return _FAMILY.get(language or "", "")

    def find_binary(self, family: str, root: Path) -> tuple[Candidate, str] | None:
        """The best available server for this family, looked for in the project first."""
        for candidate in CANDIDATES.get(family, ()):
            for folder in _PROJECT_BINS:
                found = root / folder / candidate.binary
                if found.is_file():
                    return candidate, str(found)
            # Kith's own environment. In development that is where a `pip install pyright`
            # lands; in a packaged build it is where a bundled server would sit.
            import sys

            beside = Path(sys.executable).parent / candidate.binary
            if beside.is_file():
                return candidate, str(beside)
            on_path = shutil.which(candidate.binary)
            if on_path:
                return candidate, on_path
        return None

    def available_for(self, path: str | Path) -> bool:
        """Could this file be served, without starting anything?

        Deliberately cheap — a few `stat` calls — because it is asked once per turn to decide
        whether the semantic tools are worth their schema.
        """
        family = self.family_for(path)
        if not family:
            return False
        return self.find_binary(family, self.project_root(path)) is not None

    def any_available(self, root: str | Path) -> bool:
        """Is there a language server for a language this project is actually written in?

        The narrower question, and worth the extra `stat`s. Asking "is *any* server
        installed" gets this wrong in the way that costs tokens: with pyright in Kith's own
        environment the answer is always yes, so a pure-TypeScript project would be offered
        four semantic tools on every round that could only ever answer "no typescript
        language server installed".

        What the project is written in is read off the same marker files that decide the
        project root — free, already being looked for, and right about the common cases. A
        folder with no marker at all falls back to asking about everything, because a
        directory of loose scripts is not evidence of anything either way.
        """
        here = Path(str(root)).expanduser()
        if not here.exists():
            return False
        project = self.project_root(here)
        families = {
            family for marker, family in _MARKER_LANGUAGES.items() if (project / marker).exists()
        } or set(CANDIDATES)
        return any(self.find_binary(family, project) is not None for family in families)

    def how_to_get_one(self, path: str | Path) -> str:
        """What to tell someone who has no server for this file."""
        family = self.family_for(path)
        if not family:
            from kith.engine.code import outline

            language = outline.language_for(path)
            if language is None:
                return f"I don't know how to read {Path(str(path)).suffix or 'this kind of'} files."
            return (
                f"There's no language server I know of for {language}. `outline` still works "
                "on it, and grep still works on it."
            )
        options = CANDIDATES.get(family, ())
        if not options:
            return f"No language server configured for {family}."
        # Phrased as something to do, not something to work around. The previous wording
        # explained the limitation, mentioned the install in passing, and then finished by
        # naming two alternatives — so the sentence that landed was "use grep instead", and
        # over a whole project the install never happened once.
        #
        # There is deliberately no install *tool*. He has a shell and this is a shell command;
        # a tool would be another schema on every round of every turn — and 37 of the 70
        # already offered were never called in 47 ticks — to wrap one line he can already run.
        return (
            f"There's no {family} language server here yet, so I can't answer this one "
            f"semantically. One command fixes it for every {family} project from now on:\n\n"
            f"    {options[0].install}\n\n"
            "Run it with `shell` if that's a reasonable thing to install on this machine, "
            "then ask me again — I'll pick it up. If you'd rather not, `outline` gives the "
            "file's shape and `grep` finds text."
        )

    # -- lifecycle ---------------------------------------------------------- #

    def for_file(self, path: str | Path, start_timeout: float | None = None) -> LanguageServer:
        """A started server that can answer about this file, or `Unavailable`."""
        family = self.family_for(path)
        if not family:
            raise Unavailable(self.how_to_get_one(path))
        root = self.project_root(path)
        key = (str(root), family)

        with self._lock:
            existing = self._servers.get(key)
            if existing is not None:
                if existing.alive:
                    self._touch(key)
                    return existing
                # Died since last time — a crash, or the machine slept. Clear it and start
                # again rather than handing back a corpse.
                self._servers.pop(key, None)
                with contextlib_suppress():
                    existing.stop()

            remembered = self._broken.get(key)
            if remembered:
                when, why = remembered
                if time.monotonic() - when < BROKEN_FOR:
                    raise Unavailable(why)
                # Long enough ago to be worth another try — it may have been installed since.
                self._broken.pop(key, None)

            found = self.find_binary(family, root)
            if found is None:
                raise Unavailable(self.how_to_get_one(path))
            candidate, binary = found

            server = LanguageServer([binary, *candidate.args], root, label=candidate.binary)
            try:
                server.start(**({"timeout": start_timeout} if start_timeout else {}))
            except LSPError as exc:
                reason = (
                    f"{candidate.binary} is installed but would not start: {exc}. "
                    f"Try running it yourself to see why."
                )
                self._broken[key] = (time.monotonic(), reason)
                with contextlib_suppress():
                    server.stop()
                raise Unavailable(reason) from None

            self._servers[key] = server
            self._touch(key)
            self._retire_old()
            return server

    def _touch(self, key: tuple[str, str]) -> None:
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    def _retire_old(self) -> None:
        while len(self._order) > MAX_LIVE:
            oldest = self._order.pop(0)
            server = self._servers.pop(oldest, None)
            if server is not None:
                with contextlib_suppress():
                    server.stop()

    def running(self) -> list[dict]:
        with self._lock:
            return [
                {"root": root, "language": family, "server": server.label, "alive": server.alive}
                for (root, family), server in self._servers.items()
            ]

    def shutdown(self) -> None:
        with self._lock:
            servers = list(self._servers.values())
            self._servers.clear()
            self._order.clear()
        for server in servers:
            with contextlib_suppress():
                server.stop()


class contextlib_suppress:
    """`contextlib.suppress(Exception)` without the import at every call site.

    Shutdown paths must never raise: this is called while cleaning up after something that
    already went wrong, and a failure here would replace a useful error with a useless one.
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True


#: One per process. Language servers are expensive and stateful, and two managers would each
#: start their own copy of pyright against the same project.
manager = Manager()
