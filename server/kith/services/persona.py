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
import shutil
from pathlib import Path

from kith import settings

_DEFAULT_DIR = settings.DEFAULT_PERSONA_DIR
_FRAGMENT_SUFFIXES = {".md", ".txt"}
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


_seeded = False


def persona_dir() -> Path:
    """The directory persona fragments are read from, seeded from the bundle on first use.

    A packaged Kith unpacks its own files to a temporary directory that is deleted when the
    app closes, so the fragments that ship with it cannot be the ones you edit — see
    ``settings.DEFAULT_PERSONA_DIR``. They are copied into the data directory the first time
    he needs them and read from there ever after, which is also what stops an update from
    replacing a persona someone has spent a month shaping.

    Copied only when the folder is absent, never when it is merely empty: someone who has
    deleted every fragment has said something, and answering by putting them all back would
    be the app arguing with them.
    """
    global _seeded
    override = settings.PERSONA_DIR
    if override:
        return Path(override).expanduser()
    live = _DEFAULT_DIR
    if not _seeded:
        _seeded = True
        if live != settings.BUNDLED_PERSONA_DIR and not live.exists():
            try:
                shutil.copytree(settings.BUNDLED_PERSONA_DIR, live)
            except (OSError, shutil.Error):
                # Nothing to gain from failing here: an unwritable data directory is already
                # fatal for the databases, and starting with no persona is survivable and
                # visible — the Settings screen shows an empty folder rather than a lie.
                pass
    return live


def order(relative: str) -> tuple:
    """Sort key for merge order: numbers compared as numbers.

    The prefix is documented — here, in the routes, and twice on the settings screen — as
    *being* the order. Plain string sorting made that false the moment two prefixes had
    different digit counts: ``9-quick-note.md`` sorts after ``35-how-you-spend-a-round.md``,
    because "9" is greater than "3". Anyone who took the sentence at its word and numbered a
    fragment 9 to put it early got it merged dead last, silently, with nothing on screen
    disagreeing with them.

    So digits are compared as digits, which is also what Finder shows for the same folder — a
    person checking the order by looking at the directory should see the order he reads it in.
    Purely cosmetic for prefixes of equal width, which is every fragment in this repo.
    """
    return tuple(
        (0, int(chunk), "") if chunk.isdigit() else (1, 0, chunk)
        for chunk in re.split(r"(\d+)", relative)
        if chunk
    )


def fragment_paths(root: Path | None = None) -> list[Path]:
    """Active fragment files under the persona directory, in merge order."""
    base = root or persona_dir()
    if not base.is_dir():
        return []
    files = [path for path in base.rglob("*") if path.is_file()]
    files.sort(key=lambda path: order(path.relative_to(base).as_posix()))
    return [path for path in files if _is_fragment(path, base)]


def merged_parts(root: Path | None = None) -> list[dict]:
    """The prompt as the pieces it is made of: ``[{"name", "text"}]``, in merge order.

    The same walk ``load_persona`` does, kept as a list so a reader can be told which file a
    sentence came from. `load_persona` joins this rather than repeating it, which is the point:
    the editor's "what he reads" view and the prompt he actually receives cannot show different
    text, because one is the concatenation of the other.
    """
    base = root or persona_dir()
    out = []
    for path in fragment_paths(root):
        text = prompt_text(path.read_text(encoding="utf-8"))
        if text:
            out.append({"name": path.relative_to(base).as_posix(), "text": text})
    return out


def load_persona(root: Path | None = None) -> str:
    """Merge all active fragments into a single system prompt."""
    return "\n\n".join(part["text"] for part in merged_parts(root))


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

#: Both forms the loader treats as "off" — see `_is_fragment`. Writing uses the first; reading
#: has to accept either, because a person editing the folder by hand may well have used a dot.
_DISABLED_PREFIXES = ("_", ".")


def prompt_text(text: str) -> str:
    """A fragment's text as the model receives it: comments gone, edges trimmed.

    Exactly what ``load_persona`` does to each file before joining, and it now happens here so
    the two cannot drift — the merge and the number reported for a fragment have to agree or the
    number is worse than no number.
    """
    return _COMMENT_RE.sub("", text).strip()


def _fragment(relative: str, text: str) -> dict:
    """One fragment as the editor sees it.

    ``chars`` is the file; ``promptChars`` is the part of it that reaches the model. They are
    both here because they answer different questions and the interface was answering the wrong
    one: it showed file size under a heading about what every request carries. Measured on this
    repo's own persona, the six fragments' file sizes sum to 14,583 against a merged prompt of
    11,144 — and ``35-how-you-spend-a-round.md`` is 2,580 bytes of which 910 reach him, because
    the rest is the note explaining why it exists. Anyone reading the file sizes to decide what
    to trim was reading a list sorted partly by how well-commented each fragment was.
    """
    return {
        "name": relative,
        "title": _title(relative),
        "enabled": not any(part.startswith(("_", ".")) for part in relative.split("/")),
        "chars": len(text),
        "promptChars": len(prompt_text(text)),
        "body": text,
    }


def fragments() -> list[dict]:
    """Every fragment in the folder, active or not, in merge order.

    Includes the disabled ones, which ``fragment_paths`` deliberately hides: the loader
    wants what he is, and an editor wants what there is.
    """
    base = persona_dir()
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.rglob("*"), key=lambda item: order(item.relative_to(base).as_posix())):
        if not path.is_file() or path.suffix.lower() not in _FRAGMENT_SUFFIXES:
            continue
        if path.stem.lower() == "readme":
            continue
        relative = path.relative_to(base).as_posix()
        out.append(_fragment(relative, path.read_text(encoding="utf-8", errors="replace")))
    return out


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
    """Turn a fragment on or off by renaming it, which is what the loader reads.

    Two ways this disagreed with the loader, and both were silent.

    The loader asks whether *any* part of the relative path starts with ``_`` or ``.``; this
    renamed only the file. So ticking "in his prompt" on ``_voice/10-warmth.md`` renamed nothing
    that mattered, returned 200, and the fragment stayed out of the prompt with no error to
    explain it. A fragment its folder has switched off cannot be switched on by itself, so that
    now says so rather than pretending to work.

    And ``lstrip(_DISABLED_PREFIX)`` only ever stripped underscores, while the docstring at the
    top of this module offers ``.`` as an equal alternative — so ``.10-tone.md`` could be turned
    off from a text editor and never turned back on from here.
    """
    path = _resolve(name)
    base = persona_dir().resolve()
    parents = [part for part in path.relative_to(base).parts[:-1] if part.startswith(("_", "."))]
    if enabled and parents:
        raise PersonaError(f"{parents[0]}/ is switched off, so everything in it is too — rename the folder.")
    stem = path.name.lstrip("".join(_DISABLED_PREFIXES))
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
    # `.resolve()` on both sides, because `_resolve` returns a resolved path and this compared
    # it against an unresolved one. Anywhere the persona folder sits behind a symlink — which
    # on macOS is anything under `/tmp` or `/var`, and any home directory a corporate setup has
    # redirected — every save raised ValueError from `relative_to` and came back a 500. It was
    # unreachable while the folder could only be the repo's own; it stopped being unreachable
    # when the frozen build started keeping the persona in the data directory.
    base = persona_dir().resolve()
    relative = path.resolve().relative_to(base).as_posix()
    return _fragment(relative, path.read_text(encoding="utf-8", errors="replace"))


def _title(relative: str) -> str:
    """ "40-how-you-work.md" -> "how you work". The numeric prefix is ordering, not a name."""
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
