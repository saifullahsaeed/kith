"""`submit`: how a workflow agent hands back a structured answer, checked where it is made.

A workflow can ask an agent for a shape — ``agent(..., schema={"files": ["str"]})``. That used to
be a sentence appended to the prompt ("end your report with ONE JSON value…"), a scan of the
prose for the last JSON-looking thing, and one more round asking again when there was none. It
worked when the model remembered, which is the property this codebase keeps measuring and
keeps finding is not a property: an instruction is advice, and the shape of the tools is what
decides.

So an agent asked for a shape is given one more tool, `submit`, whose parameters *are* the shape.
A call that does not match is refused with what is wrong, and the model fixes it and calls
again — the check happens at the tool-call layer, the way Claude Code's StructuredOutput tool
does it. A valid call ends the agent. Not registered in the tool registry: it exists only for
the agent that was asked, and only for as long as that agent runs.

Schemas come in two spellings, because both are in use. The example shape the workflow
description has always shown — ``{"files": ["str"], "done": "bool"}`` — and real JSON Schema
(``{"type": "object", "properties": …}``). The first is converted to the second.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from kith.domain.tooling import ToolHost

NAME = "submit"

_WORDS = {
    "str": "string",
    "string": "string",
    "text": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
}


def is_json_schema(schema: Any) -> bool:
    """Whether ``schema`` is already JSON Schema rather than an example of the shape."""
    return (
        isinstance(schema, dict)
        and isinstance(schema.get("type"), str)
        and (schema["type"] != "object" or isinstance(schema.get("properties"), dict))
    )


def to_json_schema(shape: Any) -> dict:
    """An example shape as JSON Schema. JSON Schema passes through unchanged."""
    if is_json_schema(shape):
        return shape
    if isinstance(shape, dict):
        return {
            "type": "object",
            "properties": {str(key): to_json_schema(value) for key, value in shape.items()},
            "required": [str(key) for key in shape],
        }
    if isinstance(shape, list):
        return {"type": "array", "items": to_json_schema(shape[0])} if shape else {"type": "array"}
    if isinstance(shape, bool):
        return {"type": "boolean"}
    if isinstance(shape, int):
        return {"type": "integer"}
    if isinstance(shape, float):
        return {"type": "number"}
    if isinstance(shape, str):
        word = _WORDS.get(shape.strip().lower())
        return {"type": word} if word else {"type": "string", "description": shape}
    return {}


def parameters(schema: Any) -> tuple[dict, bool]:
    """The tool's parameters for ``schema``, and whether the value is wrapped in ``value``.

    A tool's parameters must be an object, and a workflow may ask for a list. Wrapped, then
    unwrapped on the way back, so the script gets exactly the shape it asked for.
    """
    converted = to_json_schema(schema)
    if converted.get("type") == "object":
        return converted, False
    return {"type": "object", "properties": {"value": converted}, "required": ["value"]}, True


def problems(value: Any, schema: dict, where: str = "the value") -> list[str]:
    """What is wrong with ``value`` against ``schema``, as sentences. Empty when it fits.

    The subset of JSON Schema a workflow actually writes: `type` (one or a list), `properties`,
    `required`, `items`, `enum`. Anything else is accepted as written, which errs toward letting
    an answer through rather than refusing one on a keyword this does not read.
    """
    found: list[str] = []
    wanted = schema.get("type")
    kinds = wanted if isinstance(wanted, list) else [wanted] if wanted else []
    if kinds and not any(_is(value, kind) for kind in kinds):
        return [f"{where} should be {' or '.join(str(k) for k in kinds)}, not {_kind_of(value)}"]
    if "enum" in schema and isinstance(schema["enum"], list) and value not in schema["enum"]:
        found.append(f"{where} should be one of {schema['enum']}")
    if isinstance(value, dict):
        given = schema.get("properties")
        props: dict = given if isinstance(given, dict) else {}
        for key in schema.get("required") or []:
            if key not in value:
                found.append(f"{where} is missing `{key}`")
        for key, sub in props.items():
            if key in value and isinstance(sub, dict):
                found += problems(value[key], sub, f"`{key}`")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            found += problems(item, schema["items"], f"{where}[{index}]")
    return found


def _is(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "null":
        return value is None
    return True


def _kind_of(value: Any) -> str:
    for kind in ("boolean", "integer", "number", "string", "array", "object", "null"):
        if _is(value, kind):
            return kind
    return type(value).__name__


def with_submit(host: ToolHost, schema: Any, handed: dict) -> ToolHost:
    """``host`` with a `submit` tool for ``schema``. A valid call puts the value in ``handed``.

    Offered on every round whatever `only` narrows to, because it is the one way this agent can
    finish with what it was asked for. The loop derives what may run from what was offered, so
    offering it is also permitting it.
    """
    params, wrapped = parameters(schema)
    declared = {
        "type": "function",
        "function": {
            "name": NAME,
            "description": (
                "Hand back your answer as the structured value the workflow asked for. Call it "
                "once, when you are done — the call ends your work. If it is refused, fix what "
                "it says and call it again."
            ),
            "parameters": params,
        },
    }

    def schemas(only=None):
        return [*host.schemas(only=only), declared]

    def run(name, arguments, allow=None):
        if name != NAME:
            return host.run(name, arguments, allow)
        given = arguments if isinstance(arguments, dict) else {}
        wrong = problems(given, params, "your answer")
        if wrong:
            return {
                "ok": False,
                "error": "Not accepted: " + "; ".join(wrong[:8]) + ". Fix it and call submit again.",
            }
        handed["value"] = given.get("value") if wrapped else given
        return {"ok": True, "result": "Received. You are done."}

    return replace(host, schemas=schemas, run=run)
