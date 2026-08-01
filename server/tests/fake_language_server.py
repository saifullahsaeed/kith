"""A language server that does exactly what a test needs and nothing else.

The awkward parts of LSP are not the requests — they are the framing, the traffic nobody
asked for, and the ways a server misbehaves. None of those can be tested against pyright,
because pyright behaves. So this one misbehaves on demand.

Run as `python fake_language_server.py [behaviour]`:

    normal     answer properly, publish diagnostics on didOpen
    asks       send `workspace/configuration` during initialize and WAIT for the reply —
               pyright does this, and a client that ignores it hangs forever
    chatty     publish diagnostics twice, the second correcting the first
    silent     accept initialize, then never answer anything again
    crash      exit as soon as initialize arrives
    garbage    emit a malformed frame before answering
    split      write each reply in two flushes, to catch a short read
"""

from __future__ import annotations

import json
import sys
import threading
import time

behaviour = sys.argv[1] if len(sys.argv) > 1 else "normal"
out = sys.stdout.buffer
lock = threading.Lock()


def send(message: dict, split: bool = False) -> None:
    body = json.dumps(message).encode()
    header = f"Content-Length: {len(body)}\r\n\r\n".encode()
    with lock:
        if split and len(body) > 4:
            out.write(header + body[:4])
            out.flush()
            time.sleep(0.02)
            out.write(body[4:])
        else:
            out.write(header + body)
        out.flush()


def read_frame():
    length = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    if length <= 0:
        return {}
    data = b""
    while len(data) < length:
        chunk = sys.stdin.buffer.read(length - len(data))
        if not chunk:
            return None
        data += chunk
    return json.loads(data.decode())


CAPABILITIES = {
    "capabilities": {
        "textDocumentSync": 1,
        "definitionProvider": True,
        "referencesProvider": True,
        "renameProvider": True,
    },
    "serverInfo": {"name": f"fake-{behaviour}", "version": "1.0"},
}

answered_config = threading.Event()

while True:
    message = read_frame()
    if message is None:
        break
    method = message.get("method")
    message_id = message.get("id")

    if method == "initialize":
        if behaviour == "crash":
            sys.exit(3)
        if behaviour == "garbage":
            with lock:
                out.write(b"Content-Length: notanumber\r\n\r\n{}")
                out.flush()
        if behaviour == "noise":
            # The realistic version: a server that prints something to stdout that is not a
            # message at all. `services/mcp/client.py` has the same note — they do this.
            with lock:
                out.write(b"warning: config not found, using defaults\r\n")
                out.flush()
        if behaviour == "asks":
            # Ask the client something and refuse to continue until it replies. A client that
            # treats every inbound message as a notification never gets past this line.
            #
            # Read the reply inline rather than waiting on the main loop to deliver it: this
            # server is single-threaded, so waiting here for a message only this loop can
            # read is a deadlock in the *test double*, which is a confusing way to discover
            # that the client was fine all along.
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 9001,
                    "method": "workspace/configuration",
                    "params": {"items": [{"section": "fake"}]},
                }
            )
            reply = read_frame()
            if reply is None or reply.get("id") != 9001:
                sys.exit(4)
            answered_config.set()
        send({"jsonrpc": "2.0", "id": message_id, "result": CAPABILITIES}, split=(behaviour == "split"))
        continue

    if message_id == 9001 or (message_id is not None and method is None and not answered_config.is_set()):
        answered_config.set()
        continue

    if method == "initialized":
        continue

    if method == "textDocument/didOpen":
        if behaviour == "silent":
            continue
        uri = message["params"]["textDocument"]["uri"]
        send(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": uri,
                    "diagnostics": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 5},
                            },
                            "severity": 1,
                            "message": "first answer",
                            "source": "fake",
                        }
                    ],
                },
            }
        )
        if behaviour == "chatty":
            # The real pattern: a fast wrong answer, then a better one a moment later.
            # `uri` bound as a default — the loop rebinds it, and a closure would read
            # whichever file happened to be opened last.
            def later(uri: str = uri) -> None:
                time.sleep(0.15)
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/publishDiagnostics",
                        "params": {
                            "uri": uri,
                            "diagnostics": [
                                {
                                    "range": {
                                        "start": {"line": 2, "character": 1},
                                        "end": {"line": 2, "character": 4},
                                    },
                                    "severity": 2,
                                    "message": "second answer",
                                    "source": "fake",
                                }
                            ],
                        },
                    }
                )

            threading.Thread(target=later, daemon=True).start()
        continue

    if method == "shutdown":
        send({"jsonrpc": "2.0", "id": message_id, "result": None})
        continue
    if method == "exit":
        break

    if message_id is None:
        continue

    if behaviour == "silent":
        continue

    if method == "textDocument/definition":
        send(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "uri": message["params"]["textDocument"]["uri"],
                    "range": {"start": {"line": 4, "character": 2}, "end": {"line": 4, "character": 7}},
                },
            }
        )
    elif method == "textDocument/references":
        send(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": [
                    {
                        "uri": message["params"]["textDocument"]["uri"],
                        "range": {"start": {"line": 7, "character": 3}, "end": {"line": 7, "character": 8}},
                    }
                ],
            }
        )
    elif method == "textDocument/rename":
        send(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "changes": {
                        message["params"]["textDocument"]["uri"]: [
                            {
                                "range": {
                                    "start": {"line": 0, "character": 4},
                                    "end": {"line": 0, "character": 9},
                                },
                                "newText": message["params"]["newName"],
                            }
                        ]
                    }
                },
            }
        )
    else:
        send({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": "no"}})
