"""Persona loading & merging.

The system prompt is assembled from the fragment files under ``persona/`` (a
sibling of ``app.py``). Each ``.md``/``.txt`` file is one instruction; they are
merged in path order — use numeric prefixes (``00-``, ``10-``, …) to control the
sequence, and subfolders to group related instructions.

Conventions
-----------
- Disable a fragment by prefixing its name (or a parent folder) with ``_`` or
  ``.`` — e.g. ``10-tone.md`` -> ``_10-tone.md``.
- ``README`` files are ignored, so a folder can document itself.
- HTML comments (``<!-- ... -->``) are stripped, so a fragment can be annotated
  without the note leaking into the prompt.

Set ``KITH_PERSONA_DIR`` to load from a different directory, or ``KITH_SYSTEM``
to bypass the folder entirely with a single inline prompt.
"""

from __future__ import annotations

import re
from pathlib import Path

from kith import settings

_DEFAULT_DIR = settings.DEFAULT_PERSONA_DIR
_FRAGMENT_SUFFIXES = {".md", ".txt"}
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def persona_dir() -> Path:
    """The directory persona fragments are read from."""
    override = settings.PERSONA_DIR
    return Path(override).expanduser() if override else _DEFAULT_DIR


def fragment_paths(root: Path | None = None) -> list[Path]:
    """Active fragment files under the persona directory, in merge order."""
    base = root or persona_dir()
    if not base.is_dir():
        return []
    files = [path for path in base.rglob("*") if path.is_file()]
    files.sort(key=lambda path: path.relative_to(base).as_posix())
    return [path for path in files if _is_fragment(path, base)]


def load_persona(root: Path | None = None) -> str:
    """Merge all active fragments into a single system prompt."""
    parts = []
    for path in fragment_paths(root):
        text = _COMMENT_RE.sub("", path.read_text(encoding="utf-8")).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _is_fragment(path: Path, base: Path) -> bool:
    if path.suffix.lower() not in _FRAGMENT_SUFFIXES:
        return False
    if path.stem.lower() == "readme":
        return False
    return not any(part.startswith(("_", ".")) for part in path.relative_to(base).parts)


# --------------------------------------------------------------------------- #
# Editing. The folder is the source of truth, so these are file operations with
# guardrails rather than a database.
# --------------------------------------------------------------------------- #


class PersonaError(RuntimeError):
    """Something a person can act on: a bad name, a fragment that isn't there."""


#: Enabling and disabling is a rename, which is the convention the loader already reads.
#: Kept as a prefix rather than a database flag so the folder stays the whole truth —
#: someone editing these files in a text editor should see the same state the app does.
_DISABLED_PREFIX = "_"


def fragments() -> list[dict]:
    """Every fragment in the folder, active or not, in merge order.

    Includes the disabled ones, which ``fragment_paths`` deliberately hides: the loader
    wants what he is, and an editor wants what there is.
    """
    base = persona_dir()
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        if not path.is_file() or path.suffix.lower() not in _FRAGMENT_SUFFIXES:
            continue
        if path.stem.lower() == "readme":
            continue
        relative = path.relative_to(base).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        out.append(
            {
                "name": relative,
                "title": _title(relative),
                "enabled": not any(part.startswith(("_", ".")) for part in relative.split("/")),
                "chars": len(text),
                "body": text,
            }
        )
    return out


def read_fragment(name: str) -> str:
    return _resolve(name).read_text(encoding="utf-8", errors="replace")


def write_fragment(name: str, body: str) -> dict:
    """Save a fragment. Creates it if it isn't there yet."""
    target = _resolve(name, must_exist=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return _describe(target)


def create_fragment(name: str, body: str = "") -> dict:
    """Add a fragment. Refuses to overwrite, because "create" that silently replaces
    something is how a persona loses a paragraph nobody notices is gone."""
    target = _resolve(name, must_exist=False)
    if target.exists():
        raise PersonaError(f"{name} already exists.")
    return write_fragment(name, body)


def set_enabled(name: str, enabled: bool) -> dict:
    """Turn a fragment on or off by renaming it, which is what the loader reads."""
    path = _resolve(name)
    stem = path.name.lstrip(_DISABLED_PREFIX)
    target = path.with_name(stem if enabled else _DISABLED_PREFIX + stem)
    if target != path:
        if target.exists():
            raise PersonaError(f"{target.name} already exists.")
        path.rename(target)
    return _describe(target)


def delete_fragment(name: str) -> None:
    """Remove one. There is no undo here, which is why the interface asks first."""
    _resolve(name).unlink()


def rename_fragment(name: str, new_name: str) -> dict:
    """Rename, which is also how the merge order is changed — the numeric prefix IS the
    order, so "move this earlier" and "rename this" are the same operation."""
    path = _resolve(name)
    target = _resolve(new_name, must_exist=False)
    if target.exists():
        raise PersonaError(f"{new_name} already exists.")
    target.parent.mkdir(parents=True, exist_ok=True)
    path.rename(target)
    return _describe(target)


def _describe(path: Path) -> dict:
    base = persona_dir()
    relative = path.relative_to(base).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "name": relative,
        "title": _title(relative),
        "enabled": not any(part.startswith(("_", ".")) for part in relative.split("/")),
        "chars": len(text),
        "body": text,
    }


def _title(relative: str) -> str:
    """"40-how-you-work.md" -> "how you work". The numeric prefix is ordering, not a name."""
    stem = Path(relative).stem.lstrip("_.")
    without_number = re.sub(r"^\d+[-_]?", "", stem)
    return (without_number or stem).replace("-", " ").replace("_", " ").strip()


def _resolve(name: str, must_exist: bool = True) -> Path:
    """Turn a fragment name into a path inside the persona folder, or refuse.

    The guard is the point. This is a write endpoint pointed at a directory of files, so
    ``..`` and absolute paths are settled before the containment check rather than after —
    the same mistake made once in the workspace routes, where stripping slashes first let
    ``/etc/passwd`` through as ``etc/passwd``.
    """
    text = (name or "").strip()
    if not text:
        raise PersonaError("a name is required")
    base = persona_dir().resolve()
    target = (base / text).resolve()
    if not target.is_relative_to(base):
        raise PersonaError("that name points outside the persona folder")
    if target.suffix.lower() not in _FRAGMENT_SUFFIXES:
        raise PersonaError("a fragment has to be a .md or .txt file")
    if must_exist and not target.is_file():
        raise PersonaError(f"there is no {text}")
    return target
