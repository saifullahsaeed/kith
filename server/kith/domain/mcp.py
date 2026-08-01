"""An MCP server as a thing you have configured, before anything is run.

Structure only, no process and no network — the same split `domain/connection.py` makes
between "this is missing a field" and "this is unreachable". Conflating them is how a typo
in a command reads as an outage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: What a label may be. It becomes part of every tool name this server contributes, so it has
#: to survive being embedded in `mcp__<label>__<tool>` and being parsed back out — which is
#: why the separator itself is excluded rather than merely discouraged.
_LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

#: The prefix every MCP tool carries.
#:
#: Namespaced rather than checked for collisions, so a built-in always wins by construction.
#: A server that ships a tool called `shell` is not a security question at that point — it
#: simply cannot shadow anything, because nothing of ours is spelled `mcp__…__shell`.
PREFIX = "mcp__"
SEPARATOR = "__"


def tool_name(label: str, tool: str) -> str:
    return f"{PREFIX}{label}{SEPARATOR}{tool}"


def split_tool_name(name: str) -> tuple[str, str]:
    """`mcp__files__read` -> ("files", "read"), or ("", "") if it is not one of ours.

    Splits on the *first* separator after the prefix, because a server's own tool name may
    contain underscores — `mcp__github__create_pull_request` is one server and one tool, not
    a label of "github__create".
    """
    if not name.startswith(PREFIX):
        return "", ""
    rest = name[len(PREFIX) :]
    label, found, tool = rest.partition(SEPARATOR)
    return (label, tool) if found and label and tool else ("", "")


@dataclass(frozen=True)
class MCPServer:
    """One configured server: how to start it, and whether it is switched on."""

    label: str
    command: str
    args: tuple[str, ...] = ()
    #: Extra environment for the child process. Values are frequently credentials, which is
    #: why `public()` reports the names and never the values.
    env: dict[str, str] = field(default_factory=dict)
    #: Off means: not started, not listed, not in any prompt. The switch exists because a
    #: server's tools cost tokens on every round of every turn, so "installed" and "in use"
    #: have to be separable — otherwise the only way to stop paying for one is to delete it
    #: and lose its configuration.
    enabled: bool = True

    def problems(self) -> list[str]:
        """What is structurally wrong, in words worth showing someone."""
        found: list[str] = []
        if not _LABEL.match(self.label or ""):
            found.append(
                "A label must be lower-case letters, digits or hyphens, up to 32 characters. "
                "It becomes part of every tool name this server adds."
            )
        if not (self.command or "").strip():
            found.append("There is no command to run.")
        if SEPARATOR in (self.label or ""):
            # Belt and braces over the pattern above: a label containing the separator makes
            # `split_tool_name` ambiguous, and the failure would be a tool call routed to the
            # wrong server rather than anything that looks like a config error.
            found.append(f"A label cannot contain {SEPARATOR!r}.")
        return found

    @property
    def is_usable(self) -> bool:
        return not self.problems()

    def public(self) -> dict:
        """Safe to send to a client.

        The environment's *names* go out and its values never do. They are the natural place
        for an API token, and a settings page that round-trips one is a settings page that
        leaks it to anything that can read the response.
        """
        return {
            "label": self.label,
            "command": self.command,
            "args": list(self.args),
            "envKeys": sorted(self.env),
            "enabled": self.enabled,
            "problems": self.problems(),
        }

    def stored(self) -> dict:
        """The full record, values included, for the config database only."""
        return {
            "label": self.label,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "enabled": self.enabled,
        }

    @classmethod
    def from_stored(cls, raw: dict) -> MCPServer:
        return cls(
            label=str(raw.get("label") or "").strip().lower(),
            command=str(raw.get("command") or "").strip(),
            args=tuple(str(a) for a in (raw.get("args") or [])),
            env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
            enabled=bool(raw.get("enabled", True)),
        )
