"""Skills — capabilities you can install into him, in a format he did not invent.

An Agent Skill is a folder with a ``SKILL.md`` in it: YAML frontmatter naming the skill and
saying when to use it, then markdown instructions, and optionally scripts, reference files
and assets alongside. It is an open standard (agentskills.io) implemented by Claude Code,
Cursor, Copilot, Gemini CLI and a couple of dozen others, which is the whole point of
following it rather than inventing a Kith-shaped thing: a skill someone wrote for one of
those works here, unmodified, and one written here works there.

**Progressive disclosure is the reason this is affordable, and it is not an optimisation
bolted on afterwards — it is the design.** Three levels:

1. *Metadata* — name and description only, about 100 tokens per skill, always in the prompt
   so he knows what he has. This lands inside the cached prefix (see :mod:`kith.llm.caching`),
   which means after the first request of a session it is billed at read price. Installing ten
   skills costs roughly a thousand cached tokens, not ten thousand live ones.
2. *Instructions* — the SKILL.md body, read through the ``read_skill`` tool only once he has
   decided a skill applies. Under 5k tokens by convention.
3. *Resources* — everything else in the folder. Reference files he reads if the instructions
   send him there; scripts he *runs*, so their code never enters context at all and only
   their output does.

The failure mode this avoids is the obvious implementation: concatenate every skill into the
system prompt. Ten skills of 4k tokens each is 40k tokens on every single request, whether
or not any of them is relevant — which is both ruinous and worse at the task, because a model
handed forty pages of instructions follows the wrong one.

**Unknown frontmatter fields are kept, not rejected.** Claude Code alone defines a dozen
fields beyond the standard (``context``, ``model``, ``effort``, ``hooks``, ``paths``…), and
more will exist. A skill using them must still load here, minus the behaviour Kith has no
concept of. Refusing to parse what we do not implement is how "any skill works" stops being
true a month after it is written.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kith import settings

#: The file that makes a folder a skill. Capitalised exactly this way by the standard.
MANIFEST = "SKILL.md"

#: Where installed skills live. Beside the databases rather than in his workspace: a skill is
#: part of what he can do, not part of what he has made, and the workspace is a folder
#: someone may move — which must not take his capabilities with it.
DIR_KEY = "KITH_SKILLS_DIR"

#: Spec limits. Enforced on install so a broken skill is refused at the door with a reason,
#: rather than silently half-working later.
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500

#: name: 1-64 chars, lowercase alphanumeric and hyphens, no leading/trailing hyphen, no
#: consecutive hyphens. Straight from the specification.
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Conventional subdirectories. Not required and not exhaustive — the standard allows any
#: files at all — but worth naming when we show someone what a skill contains.
CONVENTIONAL_DIRS = ("scripts", "references", "assets")

#: How many bundled file names to name when a skill is activated. Some real skills ship
#: eighty-plus assets, and a list of eighty paths is several hundred tokens spent telling him
#: about template images he will never open. Enough to see the shape of the folder, with a
#: count for the rest and a directory to list properly if he needs to.
MAX_LISTED_RESOURCES = 40

#: How much of a skill body is reasonable to pull into context in one go. The standard
#: recommends under 5k tokens; this is the character equivalent, and going over is a warning
#: rather than a refusal because it is the author's call, not ours.
BODY_WARN_CHARS = 20_000


class SkillError(RuntimeError):
    """A skill could not be read, installed or removed, with a reason worth showing."""


@dataclass(frozen=True)
class Skill:
    """One installed skill, as read from disk."""

    name: str
    description: str
    path: Path
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()
    #: Fields the standard does not define and Kith does not act on. Kept so nothing is
    #: silently lost, and so the interface can show that a skill expects more than we give.
    extra: dict[str, Any] = field(default_factory=dict)
    body_chars: int = 0
    #: Files beside SKILL.md, relative to the skill root. He is told these exist without
    #: their contents being read — that is level 3 of progressive disclosure.
    resources: tuple[str, ...] = ()

    def public(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "license": self.license,
            "compatibility": self.compatibility,
            "metadata": self.metadata,
            "allowedTools": list(self.allowed_tools),
            "unsupportedFields": sorted(self.extra),
            "bodyChars": self.body_chars,
            "resources": list(self.resources),
            "path": str(self.path),
        }


# --------------------------------------------------------------------------- #
# Where they live
# --------------------------------------------------------------------------- #


def root() -> Path:
    """The skills folder, created if it is not there."""
    import os

    configured = str(os.environ.get(DIR_KEY) or "").strip()
    place = Path(configured).expanduser() if configured else settings.DATA_DIR / "skills"
    place.mkdir(parents=True, exist_ok=True)
    return place


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """The YAML frontmatter and the markdown body.

    Real YAML rather than a hand-rolled parser: descriptions in the wild use folded and
    literal scalars, quoted strings with colons in them, and list forms for
    ``allowed-tools``. A parser that handles the easy shapes and mangles the rest would make
    "any skill works" false in exactly the cases that matter.
    """
    if not text.startswith("---"):
        raise SkillError(f"{MANIFEST} must start with YAML frontmatter between --- lines.")
    # Split on the closing fence only, so --- inside the body is left alone.
    parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        raise SkillError(f"{MANIFEST} frontmatter is not closed with a --- line.")
    try:
        loaded = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"the frontmatter is not valid YAML: {exc}") from None
    if not isinstance(loaded, dict):
        raise SkillError("the frontmatter must be a mapping of fields.")
    return loaded, parts[2].lstrip("\n")


def _tools(value: Any) -> tuple[str, ...]:
    """``allowed-tools`` as a tuple. Space-separated string per the spec, or a YAML list,
    which is what Claude Code accepts and therefore what half of the skills in the wild
    actually use."""
    if isinstance(value, str):
        return tuple(part for part in value.replace(",", " ").split() if part)
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def parse(directory: Path) -> Skill:
    """Read one skill folder. Raises :class:`SkillError` with something worth reading."""
    manifest = directory / MANIFEST
    if not manifest.is_file():
        raise SkillError(f"{directory.name} has no {MANIFEST} in it.")
    front, body = split_frontmatter(manifest.read_text(errors="replace"))

    known = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    # The directory name is the fallback for `name`: the spec requires the field to match the
    # folder, and Claude Code makes the field optional and defaults to exactly this. Taking
    # the folder name means a skill missing the field still loads instead of being refused
    # over a formality.
    name = str(front.get("name") or directory.name).strip()
    description = str(front.get("description") or "").strip()
    if not description:
        # Also Claude Code's behaviour, and better than refusing: the first paragraph is
        # nearly always a serviceable description of what the thing is for.
        description = _first_paragraph(body)
    if len(description) > MAX_DESCRIPTION:
        # Trimmed, not refused. Anthropic's own `claude-api` skill overruns the published
        # limit by 44 characters, and an implementation that rejects a working official skill
        # over that is not being correct, it is being useless — "any skill works" has to mean
        # the ones that actually exist. Trimming also serves the reason the limit is there:
        # this text is billed on every request, so the budget is protected either way. The
        # overrun is still reported by validate(), so nobody has to guess why it looks short.
        description = description[: MAX_DESCRIPTION - 1].rstrip() + "…"

    metadata = front.get("metadata")
    return Skill(
        name=name,
        description=description,
        path=directory,
        license=str(front.get("license") or "").strip(),
        compatibility=str(front.get("compatibility") or "").strip(),
        metadata={str(k): str(v) for k, v in metadata.items()} if isinstance(metadata, dict) else {},
        allowed_tools=_tools(front.get("allowed-tools")),
        extra={k: v for k, v in front.items() if k not in known},
        body_chars=len(body),
        resources=_resources(directory),
    )


def _first_paragraph(body: str) -> str:
    """The first real paragraph of the body, for a skill with no description."""
    for block in body.split("\n\n"):
        text = " ".join(line.strip() for line in block.splitlines() if not line.startswith("#"))
        if text.strip():
            return text.strip()[:MAX_DESCRIPTION]
    return ""


def _resources(directory: Path) -> tuple[str, ...]:
    """Everything in the folder except the manifest, as relative paths.

    Listed, never read. Knowing that ``references/FORMS.md`` exists is what lets him decide
    whether to spend context on it; reading all of them to find out would be the opposite of
    the point.
    """
    found: list[str] = []
    for item in sorted(directory.rglob("*")):
        if item.is_dir() or item.name == MANIFEST:
            continue
        if any(part.startswith(".") for part in item.relative_to(directory).parts):
            continue
        found.append(str(item.relative_to(directory)))
        if len(found) >= 200:
            # A skill with two hundred files is unusual; listing all of them in a prompt is
            # not something to do for the one that has two thousand.
            found.append("… and more")
            break
    return tuple(found)


def installed() -> list[Skill]:
    """Every readable skill, by name. Unreadable ones are skipped, not fatal.

    One broken skill must not cost him all the others — it would take the prompt's skill
    index with it and the failure would look like the feature not existing.
    """
    found: list[Skill] = []
    for directory in sorted(root().iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            found.append(parse(directory))
        except SkillError as exc:
            print(f"[kith] skipping skill {directory.name}: {exc}")
    return found


def problems() -> list[dict]:
    """Skill folders that could not be read, and why — for the interface to show."""
    broken: list[dict] = []
    for directory in sorted(root().iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            parse(directory)
        except SkillError as exc:
            broken.append({"name": directory.name, "error": str(exc)})
    return broken


# --------------------------------------------------------------------------- #
# What goes in the prompt
# --------------------------------------------------------------------------- #

#: Prepended to the list. Short on purpose — it is billed on every request, and the protocol
#: it describes is two sentences long.
_PREAMBLE = (
    "## Skills you can use\n\n"
    "Each of these is a set of instructions someone installed for a particular kind of "
    "work. You are seeing only the name and what it is for; the instructions themselves are "
    "not here. When a task matches one, call `read_skill` with its name and follow what it "
    "says — before starting, not after getting stuck. If none of them fits, ignore them "
    "entirely and work as you normally would; reading one speculatively wastes the context "
    "you would need to do the job.\n"
)


def index() -> str:
    """The skills section of the system prompt: names and descriptions, nothing else.

    Empty string when nothing is installed, so a fresh install pays literally nothing —
    including no heading explaining a feature it does not have.

    This text sits inside the cached prefix, which is what makes it cheap to keep many
    skills: it is written once per cache lifetime and read thereafter. It is also why the
    text has to be *stable* — anything varying per request (a count, a timestamp) would move
    the seam and invalidate the persona in front of it. See :mod:`kith.llm.caching`.
    """
    skills = installed()
    if not skills:
        return ""
    lines = [f"- **{skill.name}** — {skill.description}" for skill in skills]
    return "\n\n" + _PREAMBLE + "\n" + "\n".join(lines) + "\n"


def read(name: str) -> dict:
    """Level 2: one skill's instructions, plus the names of what else is in its folder."""
    wanted = str(name or "").strip()
    for skill in installed():
        if skill.name != wanted:
            continue
        _, body = split_frontmatter((skill.path / MANIFEST).read_text(errors="replace"))
        return {
            "name": skill.name,
            "instructions": body,
            # Absolute, so reading a reference file or running a script is one call with no
            # guessing at where the skill lives.
            "directory": str(skill.path),
            "resources": list(skill.resources[:MAX_LISTED_RESOURCES]),
            **(
                {"moreResources": len(skill.resources) - MAX_LISTED_RESOURCES}
                if len(skill.resources) > MAX_LISTED_RESOURCES
                else {}
            ),
            "allowedTools": list(skill.allowed_tools),
            "compatibility": skill.compatibility,
        }
    known = ", ".join(skill.name for skill in installed()) or "none installed"
    raise SkillError(f"no skill called {wanted!r}. Installed: {known}")


# --------------------------------------------------------------------------- #
# Installing
# --------------------------------------------------------------------------- #


def validate(directory: Path) -> list[str]:
    """Everything wrong with a candidate skill, worst first. Empty means it is valid.

    Checked against the published spec rather than against what happens to work here, so a
    skill Kith accepts is one other agents will accept too — which is the entire value of
    using the standard.
    """
    faults: list[str] = []
    manifest = directory / MANIFEST
    if not manifest.is_file():
        return [f"no {MANIFEST} — a skill is a folder with a {MANIFEST} in it."]

    try:
        front, body = split_frontmatter(manifest.read_text(errors="replace"))
    except SkillError as exc:
        return [str(exc)]

    name = str(front.get("name") or directory.name).strip()
    if not name:
        faults.append("no name, and the folder has no usable name either.")
    if len(name) > MAX_NAME:
        faults.append(f"name is {len(name)} characters; the limit is {MAX_NAME}.")
    if name and not _NAME.match(name):
        faults.append(
            f"name {name!r} is not valid: lowercase letters, numbers and single hyphens "
            "only, and it cannot start or end with one."
        )
    if front.get("name") and str(front["name"]).strip() != directory.name:
        # The spec requires these to match. A mismatch is how you end up with a skill that
        # one tool finds and another does not.
        faults.append(
            f"name {front['name']!r} does not match the folder name {directory.name!r}; "
            "the specification requires them to be the same."
        )

    description = str(front.get("description") or "").strip()
    if not description:
        faults.append("no description — that is what tells an agent when to use the skill.")
    elif len(description) > MAX_DESCRIPTION:
        faults.append(
            f"description is {len(description)} characters against a limit of {MAX_DESCRIPTION}, "
            "so it is trimmed when loaded. (Warning only.)"
        )

    compatibility = str(front.get("compatibility") or "")
    if len(compatibility) > MAX_COMPATIBILITY:
        faults.append(f"compatibility is {len(compatibility)} characters; the limit is {MAX_COMPATIBILITY}.")
    if "metadata" in front and not isinstance(front["metadata"], dict):
        faults.append("metadata must be a mapping of keys to values.")

    for value in (name, description):
        if "<" in value and ">" in value:
            faults.append("name and description must not contain XML tags.")
            break

    if len(body) > BODY_WARN_CHARS:
        faults.append(
            f"the instructions are {len(body):,} characters, which is a lot to load at once "
            "— consider moving detail into references/ and pointing at it. (Warning only.)"
        )
    return faults


def install(source: Path, *, overwrite: bool = False) -> Skill:
    """Copy a skill folder in, refusing anything that is not one.

    Validated before it is copied, so a rejected skill leaves nothing behind. Nothing is
    fetched from the network here on purpose: a skill is executable instructions, and
    downloading one is a decision for the person, not something that should be one function
    call away from anything that can reach this module.
    """
    source = Path(source).expanduser()
    if not source.is_dir():
        # A single SKILL.md is a common shape to be handed. Take it and make the folder.
        if source.is_file() and source.name == MANIFEST:
            raise SkillError(
                f"that is a bare {MANIFEST}. Put it in a folder named after the skill and "
                "install the folder — the folder name is part of the format."
            )
        raise SkillError(f"{source} is not a folder.")

    faults = [f for f in validate(source) if "(Warning only.)" not in f]
    if faults:
        raise SkillError(f"{source.name} is not a valid skill: " + " ".join(faults))

    candidate = parse(source)
    destination = root() / candidate.name
    if destination.exists():
        if not overwrite:
            raise SkillError(
                f"a skill called {candidate.name} is already installed. Remove it first, or "
                "install with overwrite to replace it."
            )
        shutil.rmtree(destination)
    # Metadata is not copied: source mtimes and modes from a downloaded archive are not
    # information anyone wants, and copytree's default would carry them in.
    shutil.copytree(source, destination, copy_function=shutil.copy)
    return parse(destination)


def remove(name: str) -> None:
    """Uninstall a skill. The folder goes to the Trash rather than being destroyed.

    Someone who edited a skill for a fortnight and then clicks Remove should be able to get
    it back, and this is a folder of their writing, not a cache.
    """
    wanted = str(name or "").strip()
    if not wanted or "/" in wanted or wanted.startswith("."):
        raise SkillError(f"{wanted!r} is not a skill name.")
    directory = root() / wanted
    if not directory.is_dir():
        raise SkillError(f"no skill called {wanted}.")

    from kith.infra import workspace

    try:
        workspace.trash_path(directory)
    except Exception as exc:
        raise SkillError(f"couldn't remove {wanted}: {exc}") from None


def snapshot() -> dict:
    """Everything the interface needs: what is installed, what is broken, what it costs."""
    skills = installed()
    text = index()
    return {
        "root": str(root()),
        "skills": [skill.public() for skill in skills],
        "problems": problems(),
        # The honest number, in the same units the rest of the app reports. This is what
        # sits in the cached prefix on every request, so it is the one figure that decides
        # whether installing another skill is free or not.
        "indexChars": len(text),
        "indexTokens": round(len(text) / 3.7),
    }
