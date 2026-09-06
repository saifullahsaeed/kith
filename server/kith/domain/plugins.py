"""A plugin as a thing you have on disk, before anything is run.

Structure only — no process, no network, no filesystem beyond the paths handed in. The same
split `domain/mcp.py` makes between "this is missing a field" and "this is unreachable",
because conflating them is how a typo in a command reads as an outage.

**A plugin is one folder that may contribute four things**: skills (the format
`services/skills.py` already parses), one MCP server, UI surfaces, and commands. It contributes
them by *declaring* them, and core does the work — generates the tool schemas, draws the
buttons, validates the arguments, gates the permission, dispatches, validates the reply. Plugin
code never runs in this process and never in the renderer. That is the whole design: the
manifest is data, and data is the only thing safe to accept from a stranger.

**Why the manifest is JSON and not a `SKILL.md`.** The frontmatter/body split is what earns
that format for a skill — frontmatter is level 1 of progressive disclosure and the body is
level 2. A plugin manifest has no level 2; the body would be a README with good placement. It
would also force a command's parameter shapes into YAML, which `skills.parse()` never has to
handle.

**Unknown *top-level* keys are kept, not rejected** — the rule `services/skills.py` states and
for the same reason: refusing to parse what we do not implement is how "any plugin works" stops
being true a month after it ships. **Unknown keys inside `server`, `surfaces[]`, `commands[]`
and `state` are faults**, because a misspelled `comand` is a plugin that silently never starts,
and a silent never-starts is the failure mode this whole subsystem is worst at surfacing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

#: The file that makes a folder a plugin.
MANIFEST = "kith.plugin.json"

#: Manifest versions this build understands.
#:
#: A *range*, not a number, and that is the point. The forward case is easy — a plugin
#: declaring 2 is told it needs a newer Kith. The backward case is the one that breaks real
#: installs: Kith updates, starts writing 2, and someone has six plugins declaring 1. Retiring
#: a version silently removes six tabs, their skills and their subprocesses from a running
#: system with no explanation. So a version is only retired after at least one release in which
#: both are accepted, and a plugin outside the range keeps its row, its state and its grants and
#: contributes nothing — see `registry.health`.
SUPPORTED = (1,)

#: A plugin id. Narrower than `MCPServer._LABEL`'s 32 characters on purpose.
#:
#: It has to survive being embedded in two different tool names. Provider function names are
#: `^[a-zA-Z0-9_-]{1,64}$` — no dots — and both `mcp__<id>__<tool>` and `plugin__<id>__<command>`
#: must fit inside one. At 24 characters the server's own tool name still has 33 to work with;
#: at 32 it drops to 25, which is shorter than `create_pull_request`.
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")

#: A surface or command id.
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SURFACE_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

#: The prefix every surface-driving tool carries.
#:
#: Deliberately **not** `mcp__`. `mcp.manager.owns()` claims every well-formed `mcp__…__…` name
#: regardless of what is running — correct there, and fatal here: a plugin command spelled that
#: way is swallowed by the MCP arm of `run_tool` and answered "the 'work' server is not
#: connected" before any routing can happen. Built-ins still win by construction, because
#: nothing built-in is spelled `plugin__…__…`.
PREFIX = "plugin__"
SEPARATOR = "__"

MAX_NAME = 48
MAX_TITLE = 40
#: Chars. A command's description is in the prompt on every round of every turn.
MAX_DESCRIPTION = 240
#: Per plugin. `model` defaults to false, so a command opts in to costing prompt tokens —
#: the skills-index economics one layer out. Buttons are free; tools are not.
MAX_MODEL_COMMANDS = 8
MAX_COMMANDS = 32
MAX_SURFACES = 4
MAX_PARAM_KEYS = 8
MAX_RETURN_KEYS = 12
#: Ceiling on any declared `maxLength`.
STRING_MAX = 1_000
TIMEOUT_MS_RANGE = (250, 10_000)

#: What one plugin may add to every request, forever, if fully enabled. Refused at install
#: rather than trimmed at assembly, because a cap applied during prompt building is a cap
#: nobody was told about.
MAX_PROMPT_CHARS = 4_000

#: The repo's own characters-per-token divisor. There is no tokeniser installed and
#: `llm/budget.py` is explicit that installing one is the wrong answer.
CHARS_PER_TOKEN = 3.7

#: Runner arguments that resolve differently on every start.
#:
#: `npx -y pkg` fetches whatever is newest at the moment it runs, so the grant is against code
#: nobody reviewed and no digest notices the change. Refusing an unpinned runner at install is
#: the cheapest thing that makes "the subprocess is the code that was reviewed" true at all.
_RUNNERS = ("npx", "uvx", "pipx", "bunx", "dlx", "pnpx")

Delivery = Literal["host", "state", "surface"]

#: Effects a command may ask core to perform. A closed list, in the renderer, and the invariant
#: that bounds it: *a host effect may only do something a person can already do with one click
#: in Kith's own chrome*. That is checkable by a reviewer reading a manifest, which is why the
#: list is five long and a sixth entry is a core commit.
HOST_EFFECTS = (
    "open_surface",
    "close_surface",
    "navigate",
    "fold_conversation",
    "focus_conversation",
)

#: The projections a surface may ask to be pushed. Each is something core owns and caps — never
#: a raw endpoint payload.
#:
#: `chat_usage` is why this mechanism exists rather than "let the plugin call an endpoint": it
#: is renderer-only state published from inside a chat's runtime, so no endpoint and no MCP
#: server could ever supply it. `phrases` is the `describeCall` table; without it a plugin
#: rendering tool activity shows raw tool names, which is the "mcp probe add" failure this
#: codebase already fixed once.
WANTS = ("activity", "focus", "chat_usage", "processes", "schedules", "working_on", "phrases")


class PluginError(RuntimeError):
    """A plugin could not be read, installed or removed, with a reason worth showing."""


def tool_name(plugin: str, command: str) -> str:
    return f"{PREFIX}{plugin}{SEPARATOR}{command}"


def split_tool_name(name: str) -> tuple[str, str]:
    """`plugin__work__show_errand` -> ("work", "show_errand"), or ("", "") if it is not ours.

    Splits on the *first* separator after the prefix, because a command name may contain
    underscores — the same rule `mcp.split_tool_name` follows and for the same reason.
    """
    if not name.startswith(PREFIX):
        return "", ""
    rest = name[len(PREFIX) :]
    plugin, found, command = rest.partition(SEPARATOR)
    return (plugin, command) if found and plugin and command else ("", "")


# --------------------------------------------------------------------------- #
# The declared shapes
# --------------------------------------------------------------------------- #


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def _mapping(value: Any) -> dict:
    """A dict or an empty one. Written as a function because the inline
    `x.get(k) if isinstance(x.get(k), dict) else {}` form calls `.get` twice and, more to the
    point, does not narrow — the type checker still sees `dict | None` at the call site."""
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any, limit: int) -> str:
    """One line of someone else's text, bounded and stripped of anything that can lie.

    Control characters and bidi overrides go, because every one of these strings is eventually
    rendered on a screen where a person decides something. A plugin called
    ``"Notes\\u202e…"`` that can reorder the sentence around it has beaten the review screen
    without needing an exploit.
    """
    text = str(value or "")
    text = "".join(ch for ch in text if ch.isprintable() and ch not in "‪‫‬‭‮")
    return text.strip()[:limit]


@dataclass(frozen=True)
class Reach:
    """The boundary a plugin's subprocess declares, which Kith builds rather than checks.

    Kith does not verify the server against this. Kith compiles it into a sandbox profile, a
    constructed environment and an explicit working directory — so if the manifest lies, the
    lie is inert and the kernel refuses. That is the only claim here that survives a hostile
    plugin, and it is why there is no `why` field anywhere near it: a field nobody can check
    only ever appears on the screen where a person decides, which is the worst possible place
    for an unverifiable claim.
    """

    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ("plugin:state",)
    network: bool = True

    def canonical(self) -> str:
        """The bytes the grant hashes. Sorted, so an unstable order cannot make one boundary
        look like a different one and re-ask for nothing."""
        return json.dumps(
            {"read": sorted(self.read), "write": sorted(self.write), "network": self.network},
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class ServerSpec:
    """The one subprocess a plugin may bundle."""

    command: str
    args: tuple[str, ...] = ()
    #: NAMES ONLY. `domain/mcp.py` already establishes that env values never leave the process;
    #: a manifest is a file that gets pasted into READMEs and committed to git, so a manifest
    #: carrying a value is a manifest that has already leaked a credential. The person supplies
    #: values at install and they land in the row, never in the folder.
    env_keys: tuple[str, ...] = ()
    reach: Reach = field(default_factory=Reach)

    def problems(self) -> list[str]:
        found: list[str] = []
        if not self.command.strip():
            found.append("The plugin declares a server with no command to run.")
        for name in self.env_keys:
            if not re.match(r"^[A-Z][A-Z0-9_]{0,63}$", name):
                found.append(f"{name!r} is not an environment variable name.")
        base = Path(self.command).name
        if base in _RUNNERS:
            pinned = any("@" in arg and not arg.startswith("-") for arg in self.args)
            if not pinned:
                found.append(
                    f"{base} downloads its package every time it starts, so this plugin would "
                    f"run whatever is newest rather than what you approved. Pin a version "
                    f"(for example `pkg@1.2.3`)."
                )
            if not self.reach.network:
                # Both cannot be true, and the failure would otherwise present as a server that
                # hangs on first start with nothing to diagnose it by.
                found.append(
                    f"This plugin says it needs no network, but {base} installs it by "
                    f"downloading it. One of those is wrong."
                )
        return found


@dataclass(frozen=True)
class SurfaceDecl:
    """A tab this plugin contributes."""

    id: str
    title: str
    icon: str = "puzzle"
    min_width: int = 320
    min_height: int = 140
    entry: str = ""
    instances: Literal["single", "many"] = "single"
    #: Which conversation this surface answers for. Named `answers` rather than `scope` because
    #: `state.scope` already means something else on a different axis, and one word meaning two
    #: things across one manifest is a bug waiting for its first author.
    answers: Literal["conversation", "any"] = "conversation"
    wants: tuple[str, ...] = ()

    def problems(self) -> list[str]:
        found: list[str] = []
        if not _SURFACE_ID.match(self.id or ""):
            found.append(f"{self.id!r} is not a surface id (lower-case letters, digits, hyphens).")
        if not self.title:
            found.append(f"The {self.id!r} surface has no title, so its tab would have no label.")
        if not self.entry:
            found.append(f"The {self.id!r} surface names no entry page.")
        for want in self.wants:
            if want not in WANTS:
                found.append(f"The {self.id!r} surface asks for {want!r}, which Kith does not offer.")
        return found


@dataclass(frozen=True)
class CommandDecl:
    """One thing a plugin can be asked to do."""

    name: str
    title: str
    description: str = ""
    #: A validated subset of JSON Schema — primitives only, in both directions. Objects and
    #: arrays are how you smuggle structure into a turn, which is the rule `readCanvasMessage`
    #: already applies to a canvas reading.
    params: dict[str, dict] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    delivery: Delivery = "state"
    #: Required for `delivery: "surface"` — which of this plugin's surfaces answers.
    surface: str = ""
    #: `{"host": "<effect>"}` for host delivery, `{"set": [keys]}` for state delivery.
    does: dict = field(default_factory=dict)
    returns: dict[str, dict] = field(default_factory=dict)
    timeout_ms: int = 3_000
    #: Safe to deliver twice? Defaults false, so a reload that cannot prove a command ran
    #: reports uncertainty rather than repeating a side effect.
    repeatable: bool = False
    present: dict = field(default_factory=lambda: {"in": "none"})
    model: bool = False
    extra: dict = field(default_factory=dict)

    def problems(self, surfaces: tuple[str, ...]) -> list[str]:
        found: list[str] = []
        if not _NAME.match(self.name or ""):
            found.append(f"{self.name!r} is not a command name (lower-case, digits, underscores).")
        if not self.title:
            found.append(f"The {self.name!r} command has no title, so nothing could label it.")
        if self.delivery not in ("host", "state", "surface"):
            found.append(f"{self.name!r} has an unknown delivery {self.delivery!r}.")
        if self.delivery == "surface" and self.surface not in surfaces:
            found.append(
                f"{self.name!r} is delivered by the {self.surface!r} surface, which this "
                f"plugin does not have."
            )
        if self.delivery == "host":
            effect = str(self.does.get("host") or "")
            if effect not in HOST_EFFECTS:
                found.append(
                    f"{self.name!r} asks Kith to do {effect!r}, which is not something a plugin may ask for."
                )
        if self.delivery == "state":
            keys = self.does.get("set")
            if not isinstance(keys, list) or not keys:
                found.append(f"{self.name!r} writes to the plugin's store but names no keys.")
        if len(self.params) > MAX_PARAM_KEYS:
            found.append(f"{self.name!r} declares more than {MAX_PARAM_KEYS} parameters.")
        if len(self.returns) > MAX_RETURN_KEYS:
            found.append(f"{self.name!r} declares more than {MAX_RETURN_KEYS} return fields.")
        for where, shapes in (("parameter", self.params), ("return field", self.returns)):
            for key, shape in shapes.items():
                found += [f"{self.name!r}'s {where} {key!r}: {why}" for why in _shape_problems(shape)]
        for name in self.required:
            if name not in self.params:
                found.append(f"{self.name!r} requires {name!r}, which it does not declare.")
        return found

    def schema(self, plugin: str, plugin_name: str) -> dict:
        """The function declaration the model is handed.

        The second sentence is appended by core for `surface` delivery only, so he can act on
        the failure he is about to get rather than discovering it. `host` and `state` commands
        do not need the tab, so telling him they might would be false.
        """
        description = f"[{plugin_name}] {self.description or self.title}"
        if self.delivery == "surface":
            description += " Needs that surface open; if it is not, this says so and nothing happens."
        return {
            "type": "function",
            "function": {
                "name": tool_name(plugin, self.name),
                "description": description[: MAX_DESCRIPTION + MAX_NAME + 80],
                "parameters": {
                    "type": "object",
                    "properties": dict(self.params),
                    "required": list(self.required),
                },
            },
        }


#: The four primitive shapes a parameter or return field may take.
_TYPES = ("string", "number", "integer", "boolean")


def _shape_problems(shape: Any) -> list[str]:
    if not isinstance(shape, dict):
        return ["must be an object describing one value."]
    kind = shape.get("type")
    if kind not in _TYPES:
        return [f"has type {kind!r}; only {', '.join(_TYPES)} are allowed. Objects and arrays are not."]
    found: list[str] = []
    if kind == "string":
        if "maxLength" in shape and not isinstance(shape["maxLength"], int):
            found.append("has a maxLength that is not a whole number.")
        elif int(shape.get("maxLength") or 0) > STRING_MAX:
            found.append(f"declares a maxLength above {STRING_MAX}.")
        choices = shape.get("enum")
        if choices is not None and (not isinstance(choices, list) or len(choices) > 24):
            found.append("has an enum that is not a list of at most 24 values.")
    return found


@dataclass(frozen=True)
class Plugin:
    """One plugin, as read from disk. What it *offers*; never what anyone decided about it."""

    id: str
    name: str
    version: str
    path: Path
    description: str = ""
    manifest_version: int = 1
    publisher: str = ""
    homepage: str = ""
    license: str = ""
    server: ServerSpec | None = None
    surfaces: tuple[SurfaceDecl, ...] = ()
    commands: tuple[CommandDecl, ...] = ()
    state: dict = field(default_factory=dict)
    #: Top-level keys this build has no concept of. Kept so nothing is silently lost and so the
    #: Plugins screen can say a plugin expects more than we give it.
    extra: dict[str, Any] = field(default_factory=dict)
    #: Skill folder names found under `<path>/skills`. Discovered, never declared — a second
    #: declaration is a second source of truth that can disagree with the first.
    skills: tuple[str, ...] = ()

    def problems(self) -> list[str]:
        found: list[str] = []
        if not _ID.match(self.id or ""):
            found.append(
                "A plugin id must be lower-case letters, digits or hyphens, up to 24 characters. "
                "It becomes the folder name, the tool namespace and its server's label."
            )
        if SEPARATOR in (self.id or ""):
            found.append(f"A plugin id cannot contain {SEPARATOR!r}.")
        if not self.name:
            found.append("The plugin has no name, so nothing could label it.")
        if not self.version:
            found.append("The plugin declares no version, so an upgrade could not be told from a reinstall.")
        if self.manifest_version not in SUPPORTED:
            found.append(
                f"This plugin is written for manifest version {self.manifest_version}; this "
                f"Kith understands {', '.join(str(v) for v in SUPPORTED)}."
            )
        if len(self.surfaces) > MAX_SURFACES:
            found.append(f"A plugin may contribute at most {MAX_SURFACES} surfaces.")
        if len(self.commands) > MAX_COMMANDS:
            found.append(f"A plugin may declare at most {MAX_COMMANDS} commands.")
        offered = sum(1 for command in self.commands if command.model)
        if offered > MAX_MODEL_COMMANDS:
            found.append(
                f"{offered} commands are offered to him and the limit is {MAX_MODEL_COMMANDS}. "
                f"Each one costs prompt tokens on every round of every turn."
            )
        if self.server is not None:
            found += self.server.problems()
        ids = tuple(surface.id for surface in self.surfaces)
        if len(set(ids)) != len(ids):
            found.append("Two surfaces share an id.")
        names = tuple(command.name for command in self.commands)
        if len(set(names)) != len(names):
            found.append("Two commands share a name.")
        for surface in self.surfaces:
            found += surface.problems()
        for command in self.commands:
            found += command.problems(ids)
        cost = self.prompt_chars()
        if cost > MAX_PROMPT_CHARS:
            found.append(
                f"This plugin would add about {cost:,} characters (~{self.prompt_tokens():,} "
                f"tokens) to every request. The limit is {MAX_PROMPT_CHARS:,}."
            )
        return found

    @property
    def is_usable(self) -> bool:
        return not self.problems()

    def prompt_chars(self) -> int:
        """What this costs in every request, forever, if fully enabled.

        Command schemas plus the digest allowance. Deliberately *not* including the skills it
        ships: those are counted by the skills index, which already has a screen that prices
        itself, and counting them twice would refuse a plugin for a cost that is reported
        somewhere else as well.
        """
        schemas = sum(len(json.dumps(c.schema(self.id, self.name))) for c in self.commands if c.model)
        digest = 240 if (self.state or {}).get("digest") else 0
        return schemas + digest

    def prompt_tokens(self) -> int:
        return int(self.prompt_chars() / CHARS_PER_TOKEN)

    def surface(self, view: str) -> SurfaceDecl | None:
        return next((s for s in self.surfaces if s.id == view), None)

    def command(self, name: str) -> CommandDecl | None:
        return next((c for c in self.commands if c.name == name), None)

    def public(self) -> dict:
        """Safe to send to a client. Env *names* go out; there are no values here to leak."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "publisher": self.publisher,
            "homepage": self.homepage,
            "license": self.license,
            "path": str(self.path),
            "server": None
            if self.server is None
            else {
                "command": self.server.command,
                "args": list(self.server.args),
                "envKeys": list(self.server.env_keys),
                "reach": {
                    "read": list(self.server.reach.read),
                    "write": list(self.server.reach.write),
                    "network": self.server.reach.network,
                },
            },
            "surfaces": [
                {
                    "id": s.id,
                    "title": s.title,
                    "icon": s.icon,
                    "minWidth": s.min_width,
                    "minHeight": s.min_height,
                    "instances": s.instances,
                    "answers": s.answers,
                    "wants": list(s.wants),
                }
                for s in self.surfaces
            ],
            "commands": [
                {
                    "name": c.name,
                    "title": c.title,
                    "description": c.description,
                    "delivery": c.delivery,
                    "surface": c.surface,
                    "present": c.present,
                    "model": c.model,
                }
                for c in self.commands
            ],
            "skills": list(self.skills),
            "state": dict(self.state),
            "unsupportedFields": sorted(self.extra),
            "promptChars": self.prompt_chars(),
            "promptTokens": self.prompt_tokens(),
            "problems": self.problems(),
        }


# --------------------------------------------------------------------------- #
# Reading one
# --------------------------------------------------------------------------- #

_SERVER_KEYS = {"command", "args", "env", "reach"}
_SURFACE_KEYS = {"id", "title", "icon", "minWidth", "minHeight", "entry", "instances", "answers", "wants"}
_COMMAND_KEYS = {
    "name", "title", "description", "params", "required", "delivery", "surface",
    "does", "returns", "timeout_ms", "repeatable", "present", "model",
}  # fmt: skip
_STATE_KEYS = {"scope", "digest"}
_TOP_KEYS = {
    "manifest", "id", "name", "version", "description", "publisher", "homepage",
    "license", "server", "surfaces", "commands", "state",
}  # fmt: skip


def _strict(block: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(block) - allowed)
    if unknown:
        raise PluginError(
            f"{where} has {'a field' if len(unknown) == 1 else 'fields'} Kith does not know: "
            f"{', '.join(unknown)}. A misspelled field is a plugin that silently never starts, "
            f"so this is refused rather than ignored."
        )


def parse(directory: Path) -> Plugin:
    """Read one plugin folder. Raises `PluginError` with something worth reading."""
    manifest = directory / MANIFEST
    if not manifest.is_file():
        raise PluginError(f"{directory.name} has no {MANIFEST} in it.")
    try:
        raw = json.loads(manifest.read_text(errors="replace"))
    except ValueError as exc:
        raise PluginError(f"{MANIFEST} is not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise PluginError(f"{MANIFEST} must be an object.")

    server = None
    if isinstance(raw.get("server"), dict):
        block = raw["server"]
        _strict(block, _SERVER_KEYS, "The `server` block")
        reach_raw = _mapping(block.get("reach"))
        _strict(reach_raw, {"read", "write", "network"}, "The `server.reach` block")
        if isinstance(block.get("env"), dict):
            raise PluginError(
                "`server.env` lists environment variable NAMES, not values. A manifest is a file "
                "that gets committed to git, so a value here has already leaked."
            )
        server = ServerSpec(
            command=_text(block.get("command"), 200),
            args=tuple(_text(a, 512) for a in (block.get("args") or [])),
            env_keys=tuple(_text(k, 64) for k in (block.get("env") or [])),
            reach=Reach(
                read=tuple(_text(p, 512) for p in (reach_raw.get("read") or [])),
                write=tuple(_text(p, 512) for p in (reach_raw.get("write") or ["plugin:state"])),
                network=str(reach_raw.get("network", "any")).lower() != "none",
            ),
        )

    surfaces: list[SurfaceDecl] = []
    for block in raw.get("surfaces") or []:
        if not isinstance(block, dict):
            raise PluginError("Every entry in `surfaces` must be an object.")
        _strict(block, _SURFACE_KEYS, "A `surfaces` entry")
        surfaces.append(
            SurfaceDecl(
                id=_text(block.get("id"), 32),
                title=_text(block.get("title"), MAX_TITLE),
                icon=_text(block.get("icon"), 40) or "puzzle",
                min_width=_clamp(block.get("minWidth", 320), 240, 720, 320),
                min_height=_clamp(block.get("minHeight", 140), 80, 480, 140),
                entry=_text(block.get("entry"), 512),
                instances="many" if block.get("instances") == "many" else "single",
                answers="any" if block.get("answers") == "any" else "conversation",
                wants=tuple(_text(w, 32) for w in (block.get("wants") or [])),
            )
        )

    commands: list[CommandDecl] = []
    for block in raw.get("commands") or []:
        if not isinstance(block, dict):
            raise PluginError("Every entry in `commands` must be an object.")
        _strict(block, _COMMAND_KEYS, "A `commands` entry")
        commands.append(
            CommandDecl(
                name=_text(block.get("name"), 40),
                title=_text(block.get("title"), MAX_TITLE),
                description=_text(block.get("description"), MAX_DESCRIPTION),
                params=_mapping(block.get("params")),
                required=tuple(_text(r, 40) for r in (block.get("required") or [])),
                delivery=block.get("delivery") or "state",
                surface=_text(block.get("surface"), 32),
                does=_mapping(block.get("does")),
                returns=_mapping(block.get("returns")),
                timeout_ms=_clamp(block.get("timeout_ms", 3000), *TIMEOUT_MS_RANGE, 3000),
                repeatable=bool(block.get("repeatable", False)),
                present=_mapping(block.get("present")) or {"in": "none"},
                model=bool(block.get("model", False)),
            )
        )

    state = _mapping(raw.get("state"))
    if state:
        _strict(state, _STATE_KEYS, "The `state` block")

    return Plugin(
        id=_text(raw.get("id"), 64) or directory.name,
        name=_text(raw.get("name"), MAX_NAME),
        version=_text(raw.get("version"), 40),
        path=directory,
        description=_text(raw.get("description"), 200),
        manifest_version=_clamp(raw.get("manifest", 1), 0, 999, 0),
        publisher=_text(raw.get("publisher"), 64),
        homepage=_text(raw.get("homepage"), 200),
        license=_text(raw.get("license"), 64),
        server=server,
        surfaces=tuple(surfaces),
        commands=tuple(commands),
        state=state,
        extra={k: v for k, v in raw.items() if k not in _TOP_KEYS},
        skills=_skills_in(directory),
    )


def _skills_in(directory: Path) -> tuple[str, ...]:
    """Skill folder names under `<plugin>/skills`, discovered rather than declared."""
    place = directory / "skills"
    if not place.is_dir():
        return ()
    return tuple(
        sorted(
            child.name
            for child in place.iterdir()
            if child.is_dir() and not child.name.startswith(".") and (child / "SKILL.md").is_file()
        )
    )
