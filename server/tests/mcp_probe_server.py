#!/usr/bin/env python3
"""A real MCP server over stdio, for the tests to talk to.

A real one rather than a mock, because every bug worth catching here is in the protocol
handling: framing, skipping traffic that is not the reply, the difference between a tool
that fails and a call that fails. A mocked client would agree with whatever the client
believes and prove nothing.

Deliberately awkward in the ways real servers are:

* it prints a non-JSON line before answering `initialize`, because servers do log to stdout;
* it sends a notification with no id in the middle of the conversation;
* `boom` reports failure the MCP way — `isError` on a *successful* response — which is a
  different thing from a JSON-RPC error and has to stay different;
* `slow` never answers, so the read timeout has something to bite on.
"""

from __future__ import annotations

import json
import sys
import time

TOOLS = [
    {
        "name": "add",
        "description": "Add two numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {"name": "boom", "description": "Always reports a tool error.", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "Never answers.", "inputSchema": {"type": "object"}},
    {"name": "shell", "description": "Named like a built-in, on purpose.", "inputSchema": {"type": "object"}},
    # No usable schema at all: the client must substitute an empty object rather than pass
    # this on, since a malformed schema is a 400 on the whole request and would take down
    # every turn rather than just this tool.
    {"name": "shapeless", "description": "Has no input schema.", "inputSchema": "not a schema"},
]


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        method, rid = message.get("method"), message.get("id")

        if method == "initialize":
            sys.stdout.write("starting up — this line is not JSON\n")
            sys.stdout.flush()
            send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "hello"}})
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "probe", "version": "0.1"},
                    },
                }
            )
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = (message.get("params") or {}).get("name")
            args = (message.get("params") or {}).get("arguments") or {}
            if name == "add":
                total = args.get("a", 0) + args.get("b", 0)
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "result": {"content": [{"type": "text", "text": str(total)}]},
                    }
                )
            elif name == "boom":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "result": {"content": [{"type": "text", "text": "it went wrong"}], "isError": True},
                    }
                )
            elif name == "slow":
                time.sleep(30)
            elif name == "shell":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "result": {"content": [{"type": "text", "text": "not the real shell"}]},
                    }
                )
            else:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32601, "message": f"no tool called {name}"},
                    }
                )


if __name__ == "__main__":
    main()
