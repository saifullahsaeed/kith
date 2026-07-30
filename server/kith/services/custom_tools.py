"""Kith's self-made tools.

He writes a small program and registers it with ``create_tool``; it then appears
alongside his built-in tools and he can call it — in the same reply or later. The
definition lives in the agent DB; the code runs in his **sandbox** (never in the
server), so a self-made tool is exactly as contained as anything else he runs on
his computer.

A tool's code receives its call arguments as a JSON object — on stdin, and in the
``KITH_ARGS`` environment variable — and returns its result on stdout.
"""

from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path

from kith.infra import sandbox
from kith.infra.db import repositories as repo


def schemas(agent_db_path: Path) -> list[dict]:
    """Ollama tool declarations for every custom tool Kith has built."""
    out = []
    for tool in repo.custom_tools.list_custom_tools(agent_db_path):
        out.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": f"{tool['description']}  (a tool you built)",
                    "parameters": {
                        "type": "object",
                        "properties": tool["parameters"],
                        "required": tool["required"],
                    },
                },
            }
        )
    return out


def exists(agent_db_path: Path, name: str) -> bool:
    return repo.custom_tools.get_custom_tool(agent_db_path, name) is not None


def run(agent_db_path: Path, name: str, args: dict) -> dict:
    """Execute a self-made tool in the sandbox and return its output."""
    tool = repo.custom_tools.get_custom_tool(agent_db_path, name)
    if tool is None:
        return {"ok": False, "error": f"no such custom tool: {name}"}

    interpreter = "python3" if tool["language"] == "python" else "bash"
    extension = "py" if tool["language"] == "python" else "sh"
    code_b64 = base64.b64encode(tool["code"].encode()).decode()
    args_b64 = base64.b64encode(json.dumps(args or {}).encode()).decode()

    # Materialise code + args in a temp dir, run, clean up, preserve exit code.
    script = (
        "d=$(mktemp -d); "
        f'printf %s {shlex.quote(code_b64)} | base64 -d > "$d/tool.{extension}"; '
        f'printf %s {shlex.quote(args_b64)} | base64 -d > "$d/args.json"; '
        f'KITH_ARGS="$(cat "$d/args.json")" {interpreter} "$d/tool.{extension}" < "$d/args.json"; '
        'rc=$?; rm -rf "$d"; exit $rc'
    )
    result = sandbox.run_command(script)
    return {"ok": result.exit_code == 0, "exitCode": result.exit_code, "output": result.output}
