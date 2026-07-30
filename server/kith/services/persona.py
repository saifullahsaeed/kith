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
