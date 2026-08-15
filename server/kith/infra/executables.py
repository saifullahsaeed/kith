"""Finding a program on the machine, when the environment we were handed is a lie.

`shutil.which` searches `PATH`, and in the packaged application `PATH` is not the one the
person who installed those programs would recognise. A macOS app launched from Finder, the
Dock or Spotlight inherits `launchd`'s environment rather than a shell's — typically
`/usr/bin:/bin:/usr/sbin:/sbin`, four entries, against the thirty-nine on the development
machine this was written on. Electron passes that through to the server unchanged, because
there is nothing else it could pass.

Measured under `env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin`:

    git                          /usr/bin/git      found — the Xcode shim lives there
    clangd                       /usr/bin/clangd   found
    rg                           NOT FOUND
    pyright-langserver           NOT FOUND
    typescript-language-server   NOT FOUND

The language servers going missing is the expected half — they are meant to be optional, and
their absence is reported honestly. **ripgrep is the half that was a bug.** `files.py` falls
back to `grep -E` when `rg` is absent, and the comment above that fallback, written the last
time someone diagnosed it, says: *"this is the bug that made the tool look broken most of the
time — ripgrep takes an extended regex, so `a|b` means 'a or b'. Plain `grep -e` takes a basic
one, where `|` is the literal character."* On the development machine ripgrep is found and the
good path runs; in every shipped copy it never was, so the broken-regex path is the only one
users have ever had.

**Two strategies, because neither alone is enough.** Asking the login shell is the only way to
learn about a `PATH` someone set in their own dotfiles — nvm, pyenv, a custom prefix — and it
is what every Electron app eventually reimplements. It is also the part that can hang, on a
shell that prompts or prints, so it is given a deadline and its output is fenced with a
sentinel to survive banners. The known directories are the floor: they cost a `stat` each and
they cover Homebrew, Go, Rust and the various user-local bins, which is most of what is
actually missing in practice.

Resolved once and remembered. The answer cannot change while the process runs — installing
something new is a restart either way, and the LSP manager already retries a "broken" server
after three minutes for exactly that reason.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

#: How long the login shell gets. It is asked once per process, so this is a deadline for a
#: pathological shell rather than a budget — a shell that has not answered in three seconds is
#: one that is prompting for something, and no answer is better than a hung tool call.
SHELL_TIMEOUT = 3.0

#: Fences the answer so a shell that prints a banner, a motd, or a version-manager notice does
#: not have that mistaken for a `PATH`.
_MARK = ("__kith_path_start__", "__kith_path_end__")

#: Directories worth looking in whatever the environment says, when they exist. Not a
#: substitute for asking the shell — a pyenv or nvm prefix is unguessable — but this is the
#: part that needs no subprocess and cannot hang, and it covers the common installs:
#: Homebrew on both architectures, Go, Rust, and the user-local bins.
KNOWN_BINS: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
    "~/.local/bin",
    "~/bin",
    "~/go/bin",
    "~/.cargo/bin",
    "~/.bun/bin",
    "~/.deno/bin",
    "~/.rye/shims",
    "~/.npm-global/bin",
)

_lock = threading.Lock()
_resolved: str | None = None


def _from_login_shell() -> str:
    """What the user's own shell thinks `PATH` is, or "" if it will not say.

    `-i` as well as `-l` because the split matters on macOS: Homebrew's installer writes to
    `.zprofile` (login) but a great many people put their exports in `.zshrc` (interactive),
    and a resolver that only asked one of them would work on half of machines.
    """
    shell = os.environ.get("SHELL") or "/bin/sh"
    if not Path(shell).exists():
        return ""
    try:
        done = subprocess.run(
            [shell, "-ilc", f'printf "%s%s%s" "{_MARK[0]}" "$PATH" "{_MARK[1]}"'],
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
            # A shell that decides to read from the terminal gets EOF rather than the terminal.
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    found = re.search(rf"{_MARK[0]}(.*?){_MARK[1]}", done.stdout, re.S)
    return found.group(1).strip() if found else ""


def search_path() -> str:
    """The `PATH` to actually search: what we were given, what the shell says, then the floor.

    Order is deliberate. The inherited environment comes first so that anything deliberately
    set for this process still wins; the shell's answer next, because it is the user's real
    configuration; the known directories last, as a backstop. Duplicates are dropped keeping
    the first occurrence, so precedence survives.
    """
    global _resolved
    if _resolved is not None:
        return _resolved
    with _lock:
        if _resolved is not None:  # another thread got there while we waited
            return _resolved
        parts: list[str] = []
        for chunk in (os.environ.get("PATH", ""), _from_login_shell()):
            parts.extend(one for one in chunk.split(os.pathsep) if one)
        parts.extend(str(Path(one).expanduser()) for one in KNOWN_BINS)

        seen: set[str] = set()
        keep: list[str] = []
        for one in parts:
            if one in seen:
                continue
            seen.add(one)
            if Path(one).is_dir():
                keep.append(one)
        _resolved = os.pathsep.join(keep)
        return _resolved


def which(name: str) -> str | None:
    """`shutil.which`, against a `PATH` that reflects the machine rather than the launcher."""
    return shutil.which(name, path=search_path())


def forget() -> None:
    """Drop the remembered answer. For tests, and for anything that changes the environment."""
    global _resolved
    with _lock:
        _resolved = None
