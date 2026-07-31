"""Opening one of his files in an application you already have.

Some files are better seen somewhere else. A spreadsheet, a Word document, an archive:
the viewer cannot render them and should not try, so the honest answer is to hand the
file to whatever you normally open that kind of thing with, or to show it in the file
manager.

Three decisions worth knowing:

**The server does the opening, not the renderer.** The desktop app deliberately runs
with no preload and no ``contextBridge`` — every security-relevant Electron default is
already the safe one, and the interface only needs ``fetch`` to its own origin. Adding
an IPC bridge to launch files would widen the renderer's reach for something the
server can do directly. It also means this works the same in a browser.

**Only the folders Kith owns.** "Open this path with the default application" is a
capability worth being careful with, so it is not offered for arbitrary paths: see
:func:`_openable_roots`. Nothing here reaches the rest of your disk.

**It opens the real file, in place.** It used to copy first, into ``~/Kith files``, and
that was not caution — it was the sandbox. His files lived in a Docker container and
``docker cp`` was the only way anything else on the machine could see them. The
container is gone; his folder is a real folder in your home directory, and Preview can
open it exactly where it is.

Copying afterwards cost three things. It duplicated every artifact he ever handed over.
It meant "open" could show you a *stale* copy of a file he had since changed. And the
copy of an ``index.html`` arrived without the stylesheet next to it, so a page he built
opened unstyled — which looks like he built it badly. There was a whole mechanism here
for widening a handoff to the parent folder to compensate; opening in place makes the
problem not exist, because the stylesheet was never anywhere else.
"""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kith.infra import default_app
from kith.infra import workspace as sandbox

#: Extensions that would run something rather than open something. Handing one to the
#: default application means executing it, so these are revealed in the file manager
#: instead — he writes scripts, and one of them arriving as a double-click is not a
#: trade worth making.
EXECUTABLE_SUFFIXES = frozenset(
    {
        ".app",
        ".command",
        ".sh",
        ".bash",
        ".zsh",
        ".py",
        ".rb",
        ".pl",
        ".scpt",
        ".applescript",
        ".jar",
        ".exe",
        ".bat",
        ".cmd",
        ".ps1",
        ".msi",
        ".dmg",
        ".pkg",
        ".deb",
        ".run",
        ".bin",
    }
)


class HandoffError(RuntimeError):
    """Something a person can act on: a missing file, an unsupported platform."""


@dataclass(frozen=True)
class Handoff:
    """A file on the machine, and what may be done with it."""

    sandbox_path: str
    host_path: Path
    size: int
    #: False for anything that would execute. Revealing it is still offered.
    openable: bool
    note: str = ""
    #: What the machine would open it with, for the button's label. None when we
    #: can't tell, in which case the generic wording is used.
    opens_with: str | None = None

    def public(self) -> dict:
        return {
            "sandboxPath": self.sandbox_path,
            "hostPath": str(self.host_path),
            "folder": str(self.host_path.parent),
            "size": self.size,
            "openable": self.openable,
            "note": self.note,
            "opensWith": self.opens_with,
        }


def locate(path: str) -> Handoff:
    """Where one of his files actually is, and whether it can be opened.

    No copying: his folder is a real folder, so the answer is the path itself. What is
    left is the part that was never about the sandbox — whether handing this to the
    operating system would *open* something or *run* something.
    """
    if not sandbox.kind_of(path):
        raise HandoffError(f"There's no {path} in his files.")
    target = Path(sandbox.resolve(path))

    suffix = target.suffix.lower()
    executable = target.is_file() and (suffix in EXECUTABLE_SUFFIXES or _has_execute_bit(target))
    return Handoff(
        sandbox_path=path,
        host_path=target,
        size=_size_of(target),
        openable=not executable,
        note=(
            "This one would run rather than open, so it's shown in the folder instead." if executable else ""
        ),
        opens_with=None if executable else default_app.for_filename(target.name),
    )


def open_workspace_file(path: str, *, reveal: bool = False) -> Handoff:
    """Open one of his files, or show it in the file manager.

    The whole action in one call. It was two — export, then open the path that came back
    — because the first step used to copy the file somewhere the second step could reach.
    With nothing to copy, a round trip that returns a path so the caller can immediately
    send it back is just a round trip.
    """
    found = locate(path)
    if reveal or not found.openable:
        _launch(_openable(found.host_path), reveal=True)
    else:
        open_with_default_app(found.host_path)
    return found


def open_with_default_app(host_path: Path) -> None:
    """Hand a file to whatever the machine uses for that type."""
    target = _openable(host_path)
    if target.is_file() and (target.suffix.lower() in EXECUTABLE_SUFFIXES or _has_execute_bit(target)):
        raise HandoffError(
            f"{target.name} would be executed rather than opened. Showing it in the "
            "folder instead is safe; opening it is not something to do by accident."
        )
    _launch(target, reveal=False)


def reveal(host_path: Path) -> None:
    """Show a file in the file manager, selected."""
    _launch(_openable(host_path), reveal=True)


# --------------------------------------------------------------------------- #


def _openable_roots() -> list[Path]:
    """The folders this endpoint will open something from.

    Narrow on purpose. "Hand a path to the operating system" is a capability worth being
    careful with, and the list is the folders Kith itself owns: where he works, where his
    databases are, and where his persona is. Everything else is refused by name.

    It has to be a list rather than one root because his files are real folders on your
    machine, and the settings page reveals each of them.
    """
    from kith import settings
    from kith.infra import workspace

    roots = [workspace.root(), settings.DATA_DIR]
    persona = settings.PERSONA_DIR or settings.DEFAULT_PERSONA_DIR
    if persona:
        roots.append(Path(persona))
    resolved = []
    for root in roots:
        try:
            resolved.append(Path(root).expanduser().resolve())
        except OSError:
            continue
    return resolved


def _openable(host_path: Path) -> Path:
    """Refuse anything outside the folders Kith owns.

    ``resolve()`` first, so ``../`` and symlinks are settled before the comparison rather
    than after it — the version of this check that stripped and then tested let
    ``/etc/passwd`` through.
    """
    target = Path(host_path).expanduser().resolve()
    roots = _openable_roots()
    if not any(target.is_relative_to(root) for root in roots):
        listed = ", ".join(str(root) for root in roots)
        raise HandoffError(f"Only things under {listed} can be opened from here.")
    if not target.exists():
        raise HandoffError(f"{target.name} isn't there any more.")
    return target


def _has_execute_bit(path: Path) -> bool:
    return os.access(path, os.X_OK) and path.is_file()


def _size_of(path: Path) -> int:
    """Bytes, summed for a directory so the number means the same thing either way."""
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _launch(target: Path, *, reveal: bool) -> None:
    """Ask the desktop environment to open or reveal a path.

    Never through a shell: the path is passed as an argument, so a filename with a
    space or a quote in it is a filename, not syntax.
    """
    system = platform.system()
    if system == "Darwin":
        command = ["open", "-R", str(target)] if reveal else ["open", str(target)]
    elif system == "Windows":
        command = ["explorer", f"/select,{target}"] if reveal else ["cmd", "/c", "start", "", str(target)]
    elif system == "Linux":
        # No portable "reveal"; opening the containing folder is the closest thing.
        command = ["xdg-open", str(target.parent if reveal else target)]
    else:
        raise HandoffError(f"Don't know how to open files on {system}.")

    try:
        subprocess.run(command, check=True, capture_output=True, timeout=20)
    except FileNotFoundError:
        raise HandoffError(f"`{command[0]}` isn't available on this machine.") from None
    except subprocess.CalledProcessError as failed:
        detail = failed.stderr.decode(errors="replace").strip()
        raise HandoffError(detail or f"{shlex.join(command)} failed.") from None
    except subprocess.TimeoutExpired:
        raise HandoffError("The application took too long to start.") from None
