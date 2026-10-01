"""The design system a project is drawn in — resolved, parsed and written in one place.

A design references `var(--ds-accent)` and something has to say what that is. Four things now
need the same answer: the renderer that draws a screen, the inspector that offers its swatches,
the checker that says whether a screen obeys the system, and Kith himself when he sits down to
draw one. The one thing worse than no system is four of them disagreeing.

**Why this module reverses an earlier call.** `api/routes/design_system.py` used to resolve the
file and deliberately *not* parse it, on the grounds that the interface already had a reader and
the checker had another, and a third opinion in Python would be one opinion too many. That was
right when a system was only ever read. It stops being right the moment Kith can *write* one:
something has to say what a valid system is, and a validator is a parser. So the Python reader
lives here, once, and both the route and the checker use it — which takes the count from three
to two, and the remaining two are TypeScript and Python because the browser cannot import this.
`ui/src/lib/design/system.ts` is the other half, and `tests/test_a_design_system_reads_the_same_in_both_languages.py`
holds them to the same fixture.

**What a system holds.** Colours, a type scale, radii, fonts and a spacing scale — the values.
And now two things that are not values:

* **`components`** — the project's inventory of parts. Each is a file,
  `components/<name>.html` beside the system, that a screen *references* with
  `<k-use name="…">` rather than copying. This is the thing that makes four screens look like
  one product, and it is the reason a codebase with a component library produces consistent UI
  while a codebase without one does not, regardless of who or what is typing.

  They were snippets inline in the JSON until 2026-10-01, pasted into each screen and labelled
  with `data-k-component`. A label on a copy is not a reference: measured that day, ironline's
  `button.primary` existed in 4 versions across 7 uses, and on Pace (54 screens) he wrote his own
  Python templating to get reuse at all — which then overwrote every hand edit on its next run.
  Inline entries still read, so an older system keeps working; a file of the same name wins.
* **`conventions`** — prose, in a markdown file beside the system. The judgment a JSON file
  cannot hold: this is a dense tool and not a landing page, no shadows, the primary action goes
  bottom-right. Values say what is available; conventions say what this product *does with them*.

**Order, and why.** A project's own folder first, because a design system belongs to a product
and he works on several; then the workspace root, for a person with one; then the installed
skills, which is where a brand authored for something else — a deck, a report — already lives.
Within a folder, a file written *for screens* outranks one adapted *into* them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kith import settings
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo

#: Within a folder, in order. A system authored for screens beats one adapted from slides.
#: Kept identical to `_SYSTEM_FILES` in the checker and `SYSTEM_FILES` in `system.ts`; the
#: cross-language test asserts it.
NAMES: tuple[str, ...] = (
    ".kith/design/system.json",
    "design/system.json",
    "design-system.json",
    "brand/design-system.json",
    "brand/profile.json",
)

#: Where a new system goes when there is not one yet.
#:
#: Inside `.kith/`, which is the folder a project already keeps its Kith state in — its memory
#: and its references. Design work is Kith's output about the product, not the product, and it
#: was landing in the repository root next to the source: a `design/` folder and a `screens/`
#: folder that nobody committed on purpose and everybody had to explain in review. One folder,
#: already gitignored by the people who ignore it, and the source tree stays the source tree.
#:
#: The older locations above still resolve, so a system somebody already wrote by hand keeps
#: working and is edited where it is.
CANONICAL = ".kith/design/system.json"

#: The spacing scale a system that does not name one inherits.
DEFAULT_SPACE: tuple[int, ...] = (4, 8, 12, 16, 24, 32, 48, 64)

#: A token, component or font name. Deliberately narrow: these become CSS custom-property names
#: and `data-k-component` values, and a name with a quote in it is an injection rather than a
#: typo.
NAME = re.compile(r"[\w.-]{1,60}")

#: A component's markup. Big enough for a real card, small enough that nobody pastes a page in.
MAX_COMPONENT = 4_000

#: A component that is a file. Larger, because a file carries its own `<style>` — the reason a
#: part can stop depending on whichever screen's stylesheet it was first drawn in — and because it
#: is no longer pasted into every tool result: `read_design_system` lists a file part by name,
#: props and path, and he reads the markup only when he is changing it.
MAX_COMPONENT_FILE = 16_000

#: Where a system keeps its parts: a folder beside the system file, one part per file.
COMPONENTS_DIR = "components"

#: What a part may not contain. The renderer refuses all of it anyway; refusing here means the
#: refusal happens where somebody can read the reason rather than silently at paint time.
_UNSAFE = re.compile(r"<\s*script|javascript:|\son\w+\s*=|src\s*=\s*['\"]?https?:", re.I)

#: `<!-- use: one line -->`, the first thing in a part's file.
_USE_NOTE = re.compile(r"^\s*<!--\s*use:\s*(.*?)-->", re.S | re.I)

#: A hole in a part: `{{label}}`. Kept identical to `HOLE` in `ui/src/lib/design/compose.ts`.
_HOLE = re.compile(r"\{\{\s*([A-Za-z_][\w.-]{0,60})\s*\}\}")

#: Conventions are meant to be read every time a screen is drawn, so they have to stay short
#: enough that reading them is free. A page of prose is the budget.
MAX_CONVENTIONS = 8_000


@dataclass
class Component:
    """One named part of the inventory."""

    name: str
    html: str
    #: When to reach for it, in one line. The inventory is useless if choosing from it needs
    #: the same thought as writing the markup.
    use: str = ""
    #: The part's file, for a part that is one. None for an inline snippet from an older system.
    path: Path | None = None

    @property
    def props(self) -> list[str]:
        """The holes a screen fills, in the order the part first uses them."""
        out: list[str] = []
        for name in _HOLE.findall(self.html):
            if name.lower() not in out:
                out.append(name.lower())
        return out

    @property
    def slot(self) -> bool:
        """Whether a screen can put content inside it."""
        return bool(re.search(r"<\s*k-slot\b", self.html, re.I))

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"name": self.name, "html": self.html, "use": self.use}
        if self.path:
            out["path"] = str(self.path)
        return out


@dataclass
class System:
    """A resolved design system, with where it came from."""

    name: str = "Kith"
    #: The system file, or None when nothing was found and these are the defaults.
    path: Path | None = None
    #: The markdown file the conventions came from, if there is one.
    conventions_path: Path | None = None
    colors: dict[str, str] = field(default_factory=dict)
    text: dict[str, dict] = field(default_factory=dict)
    radius: dict[str, str] = field(default_factory=dict)
    fonts: dict[str, str] = field(default_factory=dict)
    space: list[int] = field(default_factory=lambda: list(DEFAULT_SPACE))
    components: dict[str, Component] = field(default_factory=dict)
    conventions: str = ""
    #: True when the numbers were converted from another format rather than authored for screens.
    derived: bool = False
    #: True when the file lives inside an installed skill, which makes it a shared brand rather
    #: than this project's own. Writing to one of those would change somebody else's deck.
    shared: bool = False
    #: Which layer answered: "project", "workspace", "skill", or "none".
    #:
    #: The path alone does not say this, and the difference is the whole question. Asked to
    #: report on a project's system, Kith read back `~/Kith/design-system.json` and then had to
    #: say out loud that he could not tell whether it was the project's own or the ambient
    #: default he had fallen through to — which is the one thing somebody deciding whether to
    #: author a system needs to know.
    scope: str = "none"

    @property
    def found(self) -> bool:
        return self.path is not None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path) if self.path else "",
            "conventionsPath": str(self.conventions_path) if self.conventions_path else "",
            "colors": self.colors,
            "text": self.text,
            "radius": self.radius,
            "fonts": self.fonts,
            "space": self.space,
            "components": {k: v.as_dict() for k, v in self.components.items()},
            "conventions": self.conventions,
            "derived": self.derived,
            "shared": self.shared,
            "scope": self.scope,
        }


# ── Reading ─────────────────────────────────────────────────────────────────────────────────


def _colour(raw: Any) -> str | None:
    """A colour as written in a system file: bare hex, `#hex`, or any CSS colour.

    A system file is a file in his workspace rather than a trusted source, so anything that is
    not recognisably a colour is dropped instead of being passed through into a style attribute.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{3,8}", text):
        return f"#{text}"
    if re.fullmatch(r"[\w#(),.%\s/-]{1,80}", text):
        return text
    return None


def _px(value: Any) -> str | None:
    try:
        return f"{round(float(value))}px"
    except (TypeError, ValueError):
        pass
    text = str(value or "").strip()
    return text if re.fullmatch(r"[\d.]+(px|rem|em|%)", text) else None


def _looks_like_a_system(doc: Any) -> bool:
    """Enough to be a system rather than any JSON that happens to sit at the path."""
    if not isinstance(doc, dict):
        return False
    colours = doc.get("colors") or (doc.get("tokens") or {}).get("colors")
    return isinstance(colours, dict) and bool(colours)


def _components(raw: Any) -> dict[str, Component]:
    """The inventory, validated.

    A component is markup that will be rendered into a design, so the same whitelist thinking
    that governs `parse.ts` governs what may be stored: no script, no network. The renderer
    would refuse it anyway; refusing it here means the refusal happens where somebody can read
    the reason rather than silently at paint time.
    """
    out: dict[str, Component] = {}
    if not isinstance(raw, dict):
        return out
    for name, spec in raw.items():
        if not NAME.fullmatch(str(name)):
            continue
        html = spec if isinstance(spec, str) else str((spec or {}).get("html") or "")
        use = "" if isinstance(spec, str) else str((spec or {}).get("use") or "")
        if not html.strip() or len(html) > MAX_COMPONENT:
            continue
        if _UNSAFE.search(html):
            continue
        out[str(name)] = Component(name=str(name), html=html.strip(), use=use.strip()[:200])
    return out


def split_note(text: str) -> tuple[str, str]:
    """A part file's one-line note and its markup."""
    match = _USE_NOTE.match(text)
    if not match:
        return "", text.strip()
    return match.group(1).strip(), text[match.end() :].strip()


def _component_files(system_file: Path) -> dict[str, Component]:
    """The parts that are files, beside this system.

    Beside the system rather than resolved on their own, for the reason conventions are: one
    search decides the palette, the prose and the parts, so a project cannot end up drawing with
    somebody else's button.
    """
    folder = system_file.parent / COMPONENTS_DIR
    out: dict[str, Component] = {}
    try:
        files = sorted(folder.glob("*.html"))
    except OSError:
        return out
    for one in files:
        name = one.name[: -len(".html")]
        if not NAME.fullmatch(name):
            continue
        try:
            text = one.read_text(encoding="utf-8")
        except OSError:
            continue
        use, markup = split_note(text)
        if not markup or len(markup) > MAX_COMPONENT_FILE or _UNSAFE.search(markup):
            continue
        out[name] = Component(name=name, html=markup, use=use[:200], path=one)
    return out


def component_file(system_file: Path, name: str) -> Path:
    """Where the part of this name lives, for this system."""
    return system_file.parent / COMPONENTS_DIR / f"{name}.html"


def _from_design_system(doc: dict) -> System | None:
    """The written form: a system authored for this surface."""
    colours_in = doc.get("colors")
    if not isinstance(colours_in, dict):
        return None

    colors = {
        name: hexed
        for name, value in colours_in.items()
        if NAME.fullmatch(str(name)) and (hexed := _colour(value))
    }
    if not colors:
        return None

    text: dict[str, dict] = {}
    for role, spec in (doc.get("text") or {}).items():
        if not isinstance(spec, dict) or not NAME.fullmatch(str(role)):
            continue
        size = _px(spec.get("size"))
        if not size:
            continue
        entry: dict[str, Any] = {"size": size}
        if str(spec.get("weight") or "").strip().isdigit() or isinstance(spec.get("weight"), (int, float)):
            entry["weight"] = int(spec["weight"])
        if spec.get("case") == "upper":
            entry["transform"] = "uppercase"
        if isinstance(spec.get("tracking"), str):
            entry["tracking"] = spec["tracking"]
        if isinstance(spec.get("font"), str):
            entry["font"] = spec["font"]
        text[str(role)] = entry

    radius = {
        name: size
        for name, value in (doc.get("radius") or doc.get("radii_px") or {}).items()
        if NAME.fullmatch(str(name)) and (size := _px(value))
    }

    fonts: dict[str, str] = {}
    for role, spec in (doc.get("fonts") or (doc.get("tokens") or {}).get("fonts") or {}).items():
        if not NAME.fullmatch(str(role)):
            continue
        if isinstance(spec, str):
            whole = spec
        else:
            whole = ", ".join(
                part
                for part in [str((spec or {}).get("family") or ""), str((spec or {}).get("stack") or "")]
                if part
            )
        if whole and re.fullmatch(r"[\w\s,\"'-]{1,120}", whole):
            fonts[str(role)] = whole

    space = sorted({int(n) for n in (doc.get("space") or []) if isinstance(n, (int, float)) and n > 0})

    return System(
        name=str(doc.get("name") or "Design system"),
        colors=colors,
        text=text,
        radius=radius,
        fonts=fonts,
        space=space or list(DEFAULT_SPACE),
        components=_components(doc.get("components")),
    )


def _from_profile(doc: dict) -> System | None:
    """A deck's brand profile, adapted.

    Its type scale is in points for a 1920-wide slide. Converted by ratio against body rather
    than by the profile's own `px_per_pt`, because a screen is not a slide and honouring the
    print calibration would size a paragraph for a projector. Marked `derived` so the inspector
    can say where the numbers came from rather than implying anybody chose them.
    """
    tokens = doc.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    colours_in = doc.get("colors") or tokens.get("colors")
    if not isinstance(colours_in, dict) or not colours_in:
        return None

    flat = {**doc, "colors": colours_in, "fonts": doc.get("fonts") or tokens.get("fonts") or {}}
    if doc.get("radii_px") and not doc.get("radius"):
        flat["radius"] = doc["radii_px"]

    body_pt = 12.0
    scale_in = doc.get("text") or doc.get("type") or {}
    text: dict[str, Any] = {}
    if isinstance(scale_in, dict):
        for role, spec in scale_in.items():
            if not isinstance(spec, dict):
                continue
            pt = spec.get("size_pt") or spec.get("pt") or spec.get("size")
            if pt is None:
                continue
            try:
                px = round(float(pt) / body_pt * 16)
            except (TypeError, ValueError):
                continue
            text[role] = {**spec, "size": px}
    flat["text"] = text or flat.get("text") or {}

    built = _from_design_system(flat)
    if built:
        built.derived = True
    return built


def parse(text: str) -> System | None:
    """One file's text, as a system — or None when it is not one."""
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not _looks_like_a_system(doc):
        return None
    return _from_design_system(doc) or _from_profile(doc)


def _conventions_beside(system_file: Path) -> tuple[str, Path | None]:
    """The prose beside the values.

    Looked for next to the system rather than resolved separately, so one search decides both and
    a project cannot end up with its conventions attached to somebody else's palette.
    """
    for candidate in (
        system_file.with_suffix(".md"),
        system_file.parent / "conventions.md",
        system_file.parent / "CONVENTIONS.md",
    ):
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")[:MAX_CONVENTIONS], candidate
        except OSError:
            continue
    return "", None


def project_of_folder(folder: Path) -> int | None:
    """The project whose directory contains this folder, innermost first.

    The design tools used to answer "which project" from the conversation's binding alone, and
    the first real use outside a bound conversation showed why that is not enough. Drawing a
    screen for one product while the conversation is bound to another sent that product's
    palette into the other one's system — the same failure copy-on-write was added to prevent,
    arriving by a different road.

    A design belongs to the folder it is being drawn in. The binding is a good guess about
    which folder that is and nothing more, so where the two disagree the folder wins.
    """
    try:
        here = folder.expanduser().resolve()
    except OSError:
        return None

    best: tuple[int, int] | None = None  # (path depth, project id)
    try:
        rows = repo.projects.list_projects(settings.AGENT_DB_PATH)
    except Exception:
        return None
    for row in rows:
        directory = str((row or {}).get("directory") or "").strip()
        if not directory:
            continue
        try:
            root = Path(directory).expanduser().resolve()
        except OSError:
            continue
        if here == root or root in here.parents:
            depth = len(root.parts)
            # Innermost wins: a trial folder inside a workspace that is itself a project is the
            # trial's, not the workspace's.
            if best is None or depth > best[0]:
                best = (depth, int(row["id"]))
    return best[1] if best else None


def folders(project_id: int | str | None = None) -> list[Path]:
    """Where to look, nearest first."""
    from kith.services import skills as skills_service

    places: list[Path] = []

    if project_id:
        try:
            # Read live rather than bound at import: this module is imported while the app is
            # being created, which is after anything that redirects the database has run, so a
            # captured constant here would be the pre-redirect path.
            project = repo.projects.get_project(settings.AGENT_DB_PATH, int(project_id))
            directory = str((project or {}).get("directory") or "").strip()
            if directory:
                places.append(Path(directory).expanduser())
        except Exception:
            # A project whose folder cannot be read is not a reason to have no system at all.
            pass

    try:
        places.append(sandbox.root())
    except Exception:
        pass

    try:
        for skill in sorted(skills_service.root().iterdir()):
            if skill.is_dir():
                places.append(skill)
    except OSError:
        pass

    return places


def _skill_folders() -> set[Path]:
    from kith.services import skills as skills_service

    try:
        return {p.resolve() for p in skills_service.root().iterdir() if p.is_dir()}
    except OSError:
        return set()


def _layers(project_id: int | str | None) -> list[tuple[str, Path]]:
    """The same folders `folders()` gives, each labelled with the layer it is."""
    found = folders(project_id)
    labelled: list[tuple[str, Path]] = []
    at = 0
    if project_id:
        try:
            project = repo.projects.get_project(settings.AGENT_DB_PATH, int(project_id))
            if str((project or {}).get("directory") or "").strip() and found:
                labelled.append(("project", found[0]))
                at = 1
        except Exception:
            pass
    try:
        workspace = sandbox.root()
        if at < len(found) and found[at] == workspace:
            labelled.append(("workspace", found[at]))
            at += 1
    except Exception:
        pass
    labelled.extend(("skill", one) for one in found[at:])
    return labelled


def find(project_id: int | str | None = None) -> tuple[Path, str] | None:
    """The nearest system file and its text, or None."""
    found = _find(project_id)
    return (found[1], found[2]) if found else None


def _find(project_id: int | str | None = None) -> tuple[str, Path, str] | None:
    """The nearest system, with the layer it came from."""
    for scope, folder in _layers(project_id):
        for name in NAMES:
            candidate = folder / name
            try:
                if not candidate.is_file():
                    continue
                text = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            if parse(text):
                return scope, candidate, text
    return None


def resolve(project_id: int | str | None = None) -> System:
    """The system a screen in this project is drawn in, with its conventions and inventory.

    Always answers. No system anywhere is the ordinary case for a new project rather than an
    error, and it gets Kith's own defaults under the same names, so a screen written before
    anybody authored a system still renders and still has a scale to be measured against.
    """
    found = _find(project_id)
    if not found:
        return System()
    scope, path, text = found
    system = parse(text)
    if not system:
        return System()
    system.path = path
    system.scope = scope
    system.components = {**system.components, **_component_files(path)}
    system.conventions, system.conventions_path = _conventions_beside(path)
    skills = _skill_folders()
    system.shared = any(parent.resolve() in skills for parent in [path, *path.parents] if parent.exists())
    return system


# ── Writing ─────────────────────────────────────────────────────────────────────────────────


class SystemError(Exception):
    """A system that cannot be written, with the reason in the message."""


def _target(project_id: int | str | None, current: System) -> Path:
    """Where an edit lands.

    **A system is edited in place only when it is the caller's own.** Everything else is
    copy-on-write, and all of it is the same thought: do not write to a file that is somebody
    else's.

    The case that makes this rule rather than a special case is the ordinary one, and the first
    real use found it. A project with no system of its own *resolves* one — his workspace's —
    because resolution falls through, and that is correct for reading. Editing in place then
    meant a project adding its first colour renamed the workspace system and merged its palette
    into it: one product's brand written over the default every other product inherits. So
    `scope` decides. With a project in hand, only that project's own file is written; with no
    project, the workspace's own file is.

    The other two are narrower and were right already. **A brand inside an installed skill**
    belongs to whatever it was authored for — a deck, a report — and editing it there would
    change that too. **A brand profile, wherever it lives:** `brand/profile.json` is a format
    this surface reads and does not author, its type scale is in points for a 1920-wide slide,
    and writing the conversion back would reshape the original into something the deck tooling
    did not write and return its type scale in pixels.

    In every one of those the caller gets its own copy, seeded from what it resolved. That is
    the only point in this module where a file is duplicated, and it is duplicated because the
    alternative is overwriting somebody else's.
    """
    own = current.scope == ("project" if project_id else "workspace")
    if current.found and own and not current.shared and not current.derived:
        return current.path  # type: ignore[return-value]

    if project_id:
        try:
            project = repo.projects.get_project(settings.AGENT_DB_PATH, int(project_id))
            directory = str((project or {}).get("directory") or "").strip()
            if directory:
                return Path(directory).expanduser() / CANONICAL
        except Exception:
            pass

    return sandbox.root() / CANONICAL


def _merge(base: dict, incoming: dict | None, *, replace: bool) -> dict:
    if not isinstance(incoming, dict):
        return base
    if replace:
        return dict(incoming)
    merged = dict(base)
    for key, value in incoming.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def save(
    project_id: int | str | None = None,
    *,
    name: str | None = None,
    colors: dict | None = None,
    text: dict | None = None,
    radius: dict | None = None,
    fonts: dict | None = None,
    space: list | None = None,
    components: dict | None = None,
    conventions: str | None = None,
    replace: bool = False,
) -> System:
    """Create or update the project's system, and answer with what it now is.

    Merging rather than replacing is the default because the common edit is one addition — a
    colour the screens kept needing, a card that turned out to be a part. A write that silently
    dropped everything it was not told about would make adding a component the most dangerous
    call in the tool.

    Writing goes through the workspace, which is what applies the permission gate. A design
    system is a file in somebody's project and it is not exempt from that.
    """
    current = resolve(project_id)
    target = _target(project_id, current)

    doc: dict[str, Any] = {}
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                doc = existing
        except (OSError, ValueError):
            doc = {}
    elif current.found and not name:
        # Seeding from whatever was resolved rather than starting empty — but only when the
        # caller is *adding* to it.
        #
        # Naming the system is the signal that this is a different one. Without that
        # distinction the first `update_design_system` for a new product inherited the whole
        # workspace palette: a finance tool for a back office was handed twenty-one sage, sand
        # and aqua tokens and a display serif, because they were what the folder above it
        # happened to use. Kith noticed and overwrote the sections by hand, which is the right
        # call and not one the tool should force.
        #
        # So: no name means "add to what I have", and a name means "this product is its own
        # thing". Inheriting the house brand on purpose is then `read_design_system` followed
        # by passing the colours you want to keep.
        try:
            seeded = json.loads(current.path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            if isinstance(seeded, dict):
                doc = seeded
        except (OSError, ValueError):
            doc = {}

    if name:
        doc["name"] = name
    # The written form is flat, so a profile being edited is normalised on the way in. Its
    # `tokens.colors` nesting is a shape this surface reads and does not author.
    if "tokens" in doc and isinstance(doc.get("tokens"), dict):
        nested = doc.pop("tokens")
        doc.setdefault("colors", nested.get("colors") or {})
        doc.setdefault("fonts", nested.get("fonts") or {})

    doc["colors"] = _merge(doc.get("colors") or {}, colors, replace=replace)
    doc["text"] = _merge(doc.get("text") or {}, text, replace=replace)
    doc["radius"] = _merge(doc.get("radius") or doc.get("radii_px") or {}, radius, replace=replace)
    doc.pop("radii_px", None)
    doc["fonts"] = _merge(doc.get("fonts") or {}, fonts, replace=replace)
    # Parts are files now, so the JSON keeps only the inline ones an older system already had.
    # A part named here goes to its file and its inline entry, if any, is retired — two copies
    # of one part is the exact disagreement files exist to end.
    parts_in = components if isinstance(components, dict) else {}
    inline = dict(doc.get("components") or {})
    if replace and components is not None:
        inline = {}
    for part_name in parts_in:
        inline.pop(part_name, None)
    doc["components"] = inline
    if space:
        doc["space"] = sorted({int(n) for n in space if isinstance(n, (int, float)) and n > 0})

    if not doc.get("colors"):
        raise SystemError(
            "A design system needs at least one colour — that is what makes it a system rather "
            'than a file. Give `colors` a name and a value, for instance {"ink": "#1a1a1a"}.'
        )

    checked = _from_design_system(doc)
    if not checked:
        raise SystemError("None of those colours parsed. A colour is a hex value or a CSS colour name.")

    refused: list[str] = []
    writes: dict[str, str] = {}
    removals: list[str] = []
    for part_name, spec in parts_in.items():
        if spec is None:
            removals.append(str(part_name))
            continue
        html = spec if isinstance(spec, str) else str((spec or {}).get("html") or "")
        use = "" if isinstance(spec, str) else str((spec or {}).get("use") or "")
        _, html = split_note(html)
        if (
            not NAME.fullmatch(str(part_name))
            or not html
            or len(html) > MAX_COMPONENT_FILE
            or _UNSAFE.search(html)
        ):
            refused.append(str(part_name))
            continue
        # A hole named after one of the use's own attributes could never be filled: `name`
        # says which part, `style` and `class` dress the instance. Said now, with the fix,
        # rather than discovered as a blank on every screen that uses the part.
        clashing = sorted({hole.lower() for hole in _HOLE.findall(html)} & {"name", "style", "class"})
        if clashing:
            raise SystemError(
                f"{part_name}: {{{{{clashing[0]}}}}} cannot be a hole — `{clashing[0]}` is the "
                "<k-use> tag's own attribute. Call it something else, like {{title}} or {{label}}."
            )
        note = " ".join(use.split())[:200].replace("--", "-")
        writes[str(part_name)] = (f"<!-- use: {note} -->\n" if note else "") + html.rstrip() + "\n"
    if refused:
        raise SystemError(
            f"These components were refused: {', '.join(sorted(refused))}. A component is markup "
            "in the dialect — no <script>, no inline handlers, no http(s) sources, under "
            f"{MAX_COMPONENT_FILE:,} characters, and named with word characters, dots or dashes."
        )

    if replace and components is not None:
        # Replacing the section means the parts not named are gone, and the parts are files.
        removals.extend(name for name in _component_files(target) if name not in writes)

    sandbox.write_file(str(target), json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    for part_name, text_out in writes.items():
        sandbox.write_file(str(component_file(target, part_name)), text_out)
    for part_name in removals:
        gone = component_file(target, part_name)
        if gone.is_file():
            sandbox.remove(str(gone))

    if conventions is not None:
        if len(conventions) > MAX_CONVENTIONS:
            raise SystemError(
                f"The conventions are {len(conventions):,} characters and the budget is "
                f"{MAX_CONVENTIONS:,}. They are read every time a screen is drawn, so they have "
                "to stay short enough that reading them is free. Keep the decisions, cut the prose."
            )
        sandbox.write_file(str(target.parent / "conventions.md"), conventions.rstrip() + "\n")

    return resolve(project_id)
