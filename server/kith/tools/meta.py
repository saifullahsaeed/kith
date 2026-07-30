"""Building himself new tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kith.infra.db import repositories as repo
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import STR
from kith.tools.registry import tool

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def _reserved() -> set[str]:
    """Built-in names a self-made tool may not take.

    Read at call time, not import time: this module is imported *while* the registry
    is being populated, so a module-level snapshot would be missing every tool
    registered after it — and he could shadow one of them.
    """
    from kith.tools.registry import names

    return set(names())


def _create_tool(path: Path, a: dict) -> dict:
    name = (a.get("name") or "").strip()
    if not _NAME_RE.match(name):
        raise ValueError("name must be lowercase letters/digits/underscores, starting with a letter")
    if name in _reserved():
        raise ValueError(f"'{name}' is a built-in tool name; choose another")
    code = (a.get("code") or "").strip()
    if not code:
        raise ValueError("code is required")
    language = a.get("language") if a.get("language") in ("python", "bash") else "python"
    created = repo.custom_tools.add_custom_tool(
        path,
        name=name,
        description=a.get("description") or name,
        code=code,
        parameters=_normalize_params(a.get("parameters")),
        required=a.get("required") if isinstance(a.get("required"), list) else [],
        language=language,
    )
    return {
        "name": created["name"],
        "language": created["language"],
        "note": "Tool created — you can call it now.",
    }


def _normalize_params(params: Any) -> dict:
    """Be lenient about how the model describes a tool's parameters."""
    if not isinstance(params, dict):
        return {}
    out = {}
    for key, value in params.items():
        if isinstance(value, dict):
            out[key] = value
        elif isinstance(value, str):
            out[key] = {"type": "string", "description": value}
        else:
            out[key] = {"type": "string"}
    return out


@tool(
    "create_tool",
    "Build yourself a new tool. Provide a name, a description, the parameters "
    "it takes, and code that implements it. The code (python or bash) receives "
    "the call arguments as a JSON object on stdin and in $KITH_ARGS, and should "
    "print its result to stdout. Once created, the tool appears in your tools "
    "and you can call it — now or later.",
    {
        "name": {**STR, "description": "lowercase letters/digits/underscores, e.g. add_numbers"},
        "description": {**STR, "description": "What the tool does (for your future self)."},
        "parameters": {
            "type": "object",
            "description": 'The tool\'s arguments as JSON-schema properties, e.g. {"a":{"type":"number"}}.',
        },
        "required": {"type": "array", "items": STR, "description": "Which parameters are required."},
        "language": {**STR, "enum": ["python", "bash"], "description": "Defaults to python."},
        "code": {
            **STR,
            "description": "The implementation. Reads args JSON from stdin/$KITH_ARGS, prints the result.",
        },
    },
    required=("name", "description", "code"),
)
def create_tool(path: Path, args: dict):
    return _create_tool(path, args)


@tool(
    "list_tools",
    "List the tools you have built for yourself.",
    {**PAGE_PARAMS},
    required=(),
)
def list_tools(path: Path, args: dict):
    # Each row carries the tool's source, so a dozen is a lot of characters.
    return paging.page(repo.custom_tools.list_custom_tools(path), args, default=10)


@tool(
    "delete_tool",
    "Remove one of your self-made tools.",
    {"name": STR},
    required=("name",),
)
def delete_tool(path: Path, args: dict):
    return {"deleted": repo.custom_tools.delete_custom_tool(path, args["name"])}
