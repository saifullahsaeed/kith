"""Getting a file out of his sandbox and into an app you already have.

His files live inside a Docker container, which makes them invisible to everything
else on the machine: a spreadsheet he built can be read in the file viewer and nowhere
else. That is a poor answer for anything the viewer can't render well — a workbook, a
PDF, an image, an archive — and it makes his work feel like it is trapped.

So a file can be *handed off*: copied to a folder on your machine, then opened with
whatever you normally use for that kind of file, or shown in the file manager.

Two decisions worth knowing:

**The server does the opening, not the renderer.** The desktop app deliberately runs
with no preload and no ``contextBridge`` — every security-relevant Electron default is
already the safe one, and the interface only needs ``fetch`` to its own origin. Adding
an IPC bridge to launch files would widen the renderer's reach for something the
server can do directly. It also means this works the same in a browser.

**Only inside the handoff folder.** "Open this path with the default application" is a
capability worth being careful with, so it is not offered for arbitrary paths: a file
is copied into ``~/Kith files`` first and only paths that resolve inside it can be
opened. Nothing here can reach the rest of your disk.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kith.infra import default_app
from kith.infra import workspace as sandbox

#: Where handed-off files land. Visible and obvious on purpose: the point is that they
#: stop being trapped, so they go somewhere you would think to look, not a temp dir
#: that gets swept away.
HANDOFF_DIR = Path.home() / "Kith files"

#: Files whose siblings are part of them. A page loads its stylesheet and scripts from
#: alongside itself, so handing over the page alone hands over something broken.
COMPANION_SUFFIXES = frozenset({".html", ".htm", ".xhtml", ".svg", ".ipynb", ".md"})

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
    """A file now on the machine, and what may be done with it."""

    sandbox_path: str
    host_path: Path
    size: int
    #: False for anything that would execute. Revealing it is still offered.
    openable: bool
    note: str = ""
    #: True when a whole folder came out, not just the one file that was asked for.
    folder_handed_over: bool = False
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
            "folderHandedOver": self.folder_handed_over,
        }


def export(path: str) -> Handoff:
    """Copy a sandbox file — or the folder it needs — out to the handoff folder.

    The sandbox layout is mirrored underneath, so two files with the same name from
    different directories don't collide and you can still tell where something came
    from. Overwrites on purpose: handing off the same file twice should give you the
    current version, not ``report (3).md``.

    A folder is copied whole. So is the parent of a page that loads assets from
    alongside it: copying just ``index.html`` out of a site he built leaves its
    stylesheet and scripts behind, and the page then opens unstyled — which looks like
    he built it badly rather than like a file that arrived incomplete.
    """
    resolved = sandbox.resolve(path)
    kind = sandbox.kind_of(path)
    if not kind:
        raise HandoffError(f"There's no {path} in his files.")

    # A page's assets sit next to it, so the folder is the thing that works.
    widened = kind == "file" and Path(resolved).suffix.lower() in COMPANION_SUFFIXES
    source = str(Path(resolved).parent) if widened else resolved

    relative = source.removeprefix(sandbox.HOME).lstrip("/")
    destination = HANDOFF_DIR / relative if relative else HANDOFF_DIR
    destination.parent.mkdir(parents=True, exist_ok=True)

    # docker cp writes a directory *into* the destination, so a stale copy has to go
    # first or the tree nests one level deeper on every handoff.
    if kind == "dir" or widened:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        sandbox.copy_out(source, destination.parent)
    else:
        sandbox.copy_out(source, destination)

    # What to hand to the OS: the file itself, even when its folder came along.
    target = HANDOFF_DIR / resolved.removeprefix(sandbox.HOME).lstrip("/")
    if not target.exists():
        raise HandoffError(f"{path} didn't come out of the sandbox.")
    destination = target

    suffix = destination.suffix.lower()
    executable = destination.is_file() and (suffix in EXECUTABLE_SUFFIXES or _has_execute_bit(destination))
    return Handoff(
        sandbox_path=path,
        host_path=destination,
        size=_size_of(destination),
        folder_handed_over=widened or kind == "dir",
        openable=not executable,
        note=(
            "This one would run rather than open, so it's shown in the folder instead." if executable else ""
        ),
        opens_with=None if executable else default_app.for_filename(destination.name),
    )


def open_with_default_app(host_path: Path) -> None:
    """Hand a file to whatever the machine uses for that type."""
    target = _inside_handoff(host_path)
    if target.is_file() and (target.suffix.lower() in EXECUTABLE_SUFFIXES or _has_execute_bit(target)):
        raise HandoffError(
            f"{target.name} would be executed rather than opened. Showing it in the "
            "folder instead is safe; opening it is not something to do by accident."
        )
    _launch(target, reveal=False)


def reveal(host_path: Path) -> None:
    """Show a file in the file manager, selected."""
    _launch(_inside_handoff(host_path), reveal=True)


# --------------------------------------------------------------------------- #


def _inside_handoff(host_path: Path) -> Path:
    """Refuse anything outside the handoff folder.

    The check that makes this endpoint narrow rather than "open any file on this
    machine". ``resolve()`` first, so ``../`` and symlinks are settled before the
    comparison rather than after it.
    """
    target = Path(host_path).expanduser().resolve()
    root = HANDOFF_DIR.expanduser().resolve()
    if not target.is_relative_to(root):
        raise HandoffError(f"Only files under {root} can be opened from here.")
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
