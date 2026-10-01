"""Putting a file from your machine into his folder.

Until this existed the only way to hand him a file was to find his folder in Finder and drag it
there, which works and is exactly the kind of step the Files screen is meant to save. The screen
uploads through here; he never does — his own writes go through `workspace.write_file` and the
permission gate.

Four rules, each of which is a way this has gone wrong somewhere else:

**Never outside his folder.** The folder is relative to the workspace root and resolved before it
is checked, so `..` and a symlink pointing out are both refused for where they *go*. The file's own
name is cut to its last component, because a browser will happily send `../../.zshrc` as a
filename if a page asks it to.

**Never over something without being told to.** A name that is already taken is a 409 carrying the
file that is there, so the interface can ask "replace it, or keep both?" with the size and date of
the one you would lose. `overwrite` is the answer to that question, not a default.

**Never half a file under the real name.** Bytes are streamed into a hidden sibling and moved into
place once complete. A refused or interrupted upload leaves nothing behind, and a reader never sees
a file that is still arriving. Without `overwrite`, the move is a hard link — which fails if the
name was taken in the meantime — so two uploads racing for one name cannot silently swap.

**A ceiling.** 200 MB, checked against the declared length before anything is read and against the
bytes actually written while they are.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from kith.infra import permissions
from kith.infra import workspace as sandbox
from kith.kernel import changes

#: The largest file the Files screen will take. A constant rather than a setting: it exists to
#: stop a mistaken drop of a disk image filling his folder, not as a quota anyone tunes.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

#: How much is copied at a time. A megabyte keeps memory flat whatever the file's size.
CHUNK_BYTES = 1024 * 1024


class UploadRefused(ValueError):
    """An upload that will not be written, and why. `status` is the HTTP answer for it."""

    status = 400


class TooLarge(UploadRefused):
    status = 413


class OutsideWorkspace(UploadRefused):
    status = 403


class AlreadyThere(UploadRefused):
    """The name is taken. Carries what is there, so the question can say what would be replaced."""

    status = 409

    def __init__(self, message: str, existing: dict):
        super().__init__(message)
        self.existing = existing


def describe(root: Path, path: Path) -> dict:
    """The same shape a listing entry has, plus where it is — what the interface shows for it."""
    info = path.stat()
    return {
        "path": path.relative_to(root).as_posix(),
        "name": path.name,
        "type": "dir" if path.is_dir() else "file",
        "size": info.st_size if path.is_file() else 0,
        "modified": int(info.st_mtime),
    }


def clean_name(raw: str | None) -> str:
    """The file's own name, and nothing that could make it a path."""
    text = str(raw or "").replace("\\", "/").replace("\0", "")
    name = PurePosixPath(text).name.strip()
    if not name or name in (".", ".."):
        raise UploadRefused("that file has no name to save it under")
    if name == sandbox.INTERNAL_DIR:
        raise UploadRefused(f"{name} is where he keeps his own records — pick another name")
    return name


def folder_in(root: Path, raw: str | None) -> Path:
    """The folder to put it in: relative to his workspace, existing, and inside it once resolved."""
    text = str(raw or ".").strip() or "."
    if text.startswith(("/", "~")):
        raise OutsideWorkspace("give a folder inside his workspace, not an absolute path")
    parts = [part for part in PurePosixPath(text.replace("\\", "/")).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise OutsideWorkspace("that folder is outside his workspace")
    if parts and parts[0] == sandbox.INTERNAL_DIR:
        raise OutsideWorkspace("that folder is his own bookkeeping, not somewhere to put files")
    target = (root.joinpath(*parts) if parts else root).resolve()
    if not target.is_relative_to(root):
        raise OutsideWorkspace("that folder is outside his workspace")
    if not target.is_dir():
        raise UploadRefused(f"there's no folder {text} to put it in")
    return target


def save(
    stream: BinaryIO,
    filename: str | None,
    folder: str | None = ".",
    *,
    overwrite: bool = False,
    max_bytes: int | None = None,
) -> dict:
    """Write `stream` into `folder` as `filename`, and describe the file that is now there."""
    # Read at call time, not bound as a default, so the ceiling is one constant to change.
    max_bytes = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    root = sandbox.root().resolve()
    name = clean_name(filename)
    target = folder_in(root, folder) / name

    # The same question the editor's save asks, in the form that answers at once — see
    # `routes/workspace.workspace_write` for why `require_path` would park this request instead.
    if not permissions.check_path("write", target, root).allowed:
        raise OutsideWorkspace("that folder is outside the ones this screen can put files into")

    if target.is_symlink() or target.exists():
        if target.is_dir() and not target.is_symlink():
            raise UploadRefused(f"{name} is a folder here — rename the file to put it beside it")
        if not overwrite:
            raise AlreadyThere(f"{name} is already in this folder", describe(root, target))
        if target.is_symlink():
            # Replacing a link would write through it to wherever it points.
            raise UploadRefused(f"{name} here is a link — delete it first to put a file in its place")

    partial = target.with_name(f".{name}.{secrets.token_hex(4)}.uploading")
    written = 0
    try:
        with partial.open("xb") as out:
            while True:
                chunk = stream.read(CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise TooLarge(f"{name} is over the {max_bytes // (1024 * 1024)} MB limit for one file")
                out.write(chunk)
        if overwrite:
            os.replace(partial, target)
        else:
            try:
                os.link(partial, target)
            except FileExistsError:
                raise AlreadyThere(f"{name} is already in this folder", describe(root, target)) from None
            except OSError:
                # A volume without hard links (exFAT, some network shares). Checked once more
                # and moved; the race this loses is a far smaller one than refusing outright.
                if target.exists():
                    raise AlreadyThere(f"{name} is already in this folder", describe(root, target)) from None
                os.replace(partial, target)
            partial.unlink(missing_ok=True)
    except OSError as exc:
        raise UploadRefused(f"couldn't save {name}: {exc.strerror or exc}") from None
    finally:
        if partial.exists():
            partial.unlink(missing_ok=True)

    try:
        changes.publish("workspace")
    except Exception:
        pass  # a note about a write must never fail the write — same rule as `files._wrote`
    return describe(root, target)
