"""The tool registry: one definition per tool, schema and behaviour together.

Kith's tools used to live in two lists — 500 lines of JSON-schema declarations at
the top of a module, and a dictionary of handlers 500 lines below. Adding a tool
meant editing both, and nothing checked that they agreed: a schema with no handler
was a runtime error the model discovered for us, and a handler with no schema was
simply invisible, dead code the model could never call.

Here a tool IS its schema plus its behaviour, registered once:

    @tool("remember", "Keep something worth remembering.",
          {"content": STR}, required=("content",))
    def remember(path: Path, args: dict):
        return embeddings.remember(path, args["content"])

Which means they cannot drift, the function stays directly importable and testable,
and `run` is the single chokepoint every call passes through — the natural home for
the permission layer this will eventually need.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A tool takes the agent database path and its parsed arguments, and returns
# anything JSON-serialisable. Errors raise; the caller turns them into a result the
# model can read rather than letting them escape.
Handler = Callable[[Path, dict], Any]


@dataclass(frozen=True)
class Tool:
    """One thing Kith can do."""

    name: str
    description: str
    properties: dict
    required: tuple[str, ...]
    run: Handler

    def schema(self) -> dict:
        """The OpenAI function-calling shape both providers expect."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": list(self.required),
                },
            },
        }


_REGISTRY: dict[str, Tool] = {}


def register(entry: Tool) -> None:
    """Add a tool. Duplicate names are a programming error, not a last-wins merge —
    silently shadowing a tool would change what Kith can do with no sign of it."""
    if entry.name in _REGISTRY:
        raise ValueError(f"tool {entry.name!r} is already registered")
    _REGISTRY[entry.name] = entry


def tool(
    name: str,
    description: str,
    properties: dict | None = None,
    required: tuple[str, ...] = (),
) -> Callable[[Handler], Handler]:
    """Declare a tool. Returns the function unchanged, so it stays directly callable."""

    def decorate(handler: Handler) -> Handler:
        register(Tool(name, description, properties or {}, tuple(required), handler))
        return handler

    return decorate


def get(name: str) -> Tool | None:
    """The tool, or None. For asking *whether* a tool exists — see `require` to use one."""
    return _REGISTRY.get(name)


def require(name: str) -> Tool:
    """The tool, or a KeyError naming what is actually registered.

    `get(name).run(...)` reads fine and fails badly: a renamed or unregistered tool surfaces as
    `AttributeError: 'NoneType' object has no attribute 'run'`, which names neither the tool
    asked for nor the ones available. Every caller that intends to *use* a tool wants this.
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"no tool {name!r}. Registered: {', '.join(sorted(_REGISTRY))}") from None


def names() -> list[str]:
    return sorted(_REGISTRY)


def all_tools() -> dict[str, Tool]:
    """A copy — callers must not be able to mutate the registry by accident."""
    return dict(_REGISTRY)


def schemas(only: set[str] | None = None) -> list[dict]:
    """Tool declarations to hand the model.

    ``only`` scopes the set to the tools a phase may use — the landing reserve is the
    one caller left. That is a real
    saving, not tidiness: every schema is prompt tokens on every round.

    Declaration order is preserved rather than sorted — related tools stay adjacent
    in the prompt, which reads better to the model than alphabetical order.
    """
    return [entry.schema() for entry in _REGISTRY.values() if only is None or entry.name in only]
