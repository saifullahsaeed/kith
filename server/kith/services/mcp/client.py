"""Talking to an MCP server over stdio, synchronously.

**Why not the official SDK.** `kith/` contains zero `async def`, zero `await` and zero
`asyncio` — it is synchronous Flask with generators throughout, and the tool loop already
runs calls under a ThreadPoolExecutor. The SDK is async-only, so using it means a dedicated
event-loop thread and `run_coroutine_threadsafe` at every call site, inside a generator
inside a worker thread. That is a large amount of machinery, in the hardest place to debug
it, to reach a protocol that is line-delimited JSON-RPC 2.0 over a pipe.

So this speaks it directly, the same way `llm/openai_compat.py` hand-parses SSE rather than
taking a streaming client. Three methods are needed and no more: `initialize`, `tools/list`,
`tools/call`.

**stdio only, for now.** It is what servers actually ship, it needs no OAuth dance, and
`infra/workspace.py` already spawns subprocesses with a timeout and an environment. HTTP is
a second transport behind the same interface when something needs it.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading

#: The revision this client implements. A server that answers with a different one still
#: works — the three methods used here have been stable across revisions — but it is worth
#: sending honestly rather than echoing whatever the server prefers.
PROTOCOL_VERSION = "2024-11-05"

#: How we introduce ourselves. Servers log this, and some gate behaviour on it.
CLIENT_INFO = {"name": "kith", "version": "1.0.0"}


class MCPError(RuntimeError):
    """The server could not be reached, or answered with something unusable."""


class StdioServer:
    """One running server process, and the conversation with it.

    Not thread-safe by accident: a single pipe carries request and response, so two callers
    interleaving would each read the other's reply. `_lock` makes a call atomic, which
    matters because the agent loop runs parallel-safe tools concurrently and an MCP tool
    could join that set later.
    """

    def __init__(self, command: str, args: list[str], env: dict[str, str], connect_timeout: float):
        self._command = command
        self._args = list(args)
        self._env = dict(env)
        self._connect_timeout = connect_timeout
        self._process: subprocess.Popen | None = None
        self._next_id = 0
        self._lock = threading.Lock()
        self.server_info: dict = {}

    # -- lifecycle ---------------------------------------------------------- #

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                [self._command, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # Kept separate and drained nowhere: a server that logs chattily to stderr
                # would otherwise fill a pipe buffer and deadlock mid-conversation. DEVNULL
                # loses the logs, which is the right trade for a background process nobody
                # is reading — a failure still surfaces as a dead pipe on the next call.
                stderr=subprocess.DEVNULL,
                env={**os.environ, **self._env},
                text=True,
                bufsize=1,  # line buffered, which is what the framing assumes
            )
        except (OSError, ValueError) as exc:
            raise MCPError(f"could not start {self._command!r}: {exc}") from None

        hello = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=self._connect_timeout,
        )
        self.server_info = hello.get("serverInfo") or {}
        # Required by the protocol, and servers do wait for it before answering anything
        # else. It is a notification, so there is no reply to read.
        self._notify("notifications/initialized")

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        for close in (process.stdin, process.stdout):
            try:
                if close:
                    close.close()
            except OSError:
                pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            # A server that will not go quietly gets killed. Left running it would hold a
            # pipe and, for something like a database bridge, a connection.
            with contextlib.suppress(OSError):
                process.kill()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # -- the three methods -------------------------------------------------- #

    def list_tools(self, timeout: float) -> list[dict]:
        """Every tool, following `nextCursor` until the server stops paging.

        Bounded at 20 pages. An unbounded follow is a server able to hang a startup by
        paging forever, and no real server has thousands of tools.
        """
        found: list[dict] = []
        cursor: str | None = None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            page = self._request("tools/list", params, timeout=timeout)
            found.extend(page.get("tools") or [])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return found

    def call(self, name: str, arguments: dict, timeout: float) -> dict:
        """Run one tool and return its result in a shape the loop can read.

        MCP reports a tool's *own* failure as `isError` on a successful response, not as a
        JSON-RPC error — the distinction being "the tool ran and said no" versus "the call
        did not happen". Both reach the model as a result it can read, but only the second
        is an exception here.
        """
        answer = self._request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        return {
            "content": _readable(answer.get("content") or []),
            "isError": bool(answer.get("isError")),
            **({"structured": answer["structuredContent"]} if "structuredContent" in answer else {}),
        }

    # -- the wire ----------------------------------------------------------- #

    def _request(self, method: str, params: dict, timeout: float) -> dict:
        with self._lock:
            if self._process is None or self._process.stdin is None or self._process.stdout is None:
                raise MCPError("the server is not running")
            self._next_id += 1
            message = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
            try:
                self._process.stdin.write(json.dumps(message) + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MCPError(f"{method}: the server closed its input ({exc})") from None

            reply = self._read_reply(self._next_id, timeout)
        if "error" in reply:
            detail = reply["error"]
            raise MCPError(f"{method}: {detail.get('message') or detail}")
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    def _notify(self, method: str) -> None:
        if self._process is None or self._process.stdin is None:
            return
        try:
            self._process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # a notification has no reply, so nothing downstream depends on it landing

    def _read_reply(self, wanted: int, timeout: float) -> dict:
        """Read until the reply with our id arrives, or time runs out.

        Skipping over other traffic is not optional: a server may interleave notifications
        (`notifications/message`, progress) and requests of its own, and taking the first
        line as the answer would return a log entry as a tool result.

        The timeout is enforced with a watchdog rather than by polling, because a blocking
        `readline` on a pipe cannot be interrupted otherwise — and a server that accepts a
        call and never answers would hang the whole turn.
        """
        result: dict = {}
        failure: list[str] = []

        def pump() -> None:
            try:
                while True:
                    assert self._process is not None and self._process.stdout is not None
                    line = self._process.stdout.readline()
                    if not line:
                        failure.append("the server closed its output")
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue  # servers do print the odd non-JSON line to stdout
                    if message.get("id") == wanted:
                        result.update(message)
                        return
            except (OSError, ValueError) as exc:
                failure.append(str(exc))

        reader = threading.Thread(target=pump, name="kith-mcp-read", daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            # The thread is parked on readline and cannot be joined. Killing the process is
            # what frees it; the caller gets a clear timeout rather than a hung turn.
            self.stop()
            raise MCPError(f"no reply within {timeout:g}s")
        if failure:
            raise MCPError(failure[0])
        return result


def _readable(content: list) -> str:
    """MCP content blocks, flattened to something a model can read.

    Text passes through. Anything else is named rather than inlined — an image block here
    would be base64, and this codebase has already paid for base64 arriving somewhere that
    counts it as text: 2.8 million tokens to look at three pages of a CV.
    """
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind == "resource":
            resource = block.get("resource") or {}
            parts.append(str(resource.get("text") or f"[resource {resource.get('uri', '')}]"))
        else:
            parts.append(f"[{kind or 'content'} omitted — not text]")
    return "\n".join(part for part in parts if part)
