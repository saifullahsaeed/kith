"""Talking to a language server, synchronously.

The same reasoning as `services/mcp/client.py`: this codebase has no `async` in it, the
protocol is JSON-RPC 2.0 over a pipe, and hand-speaking it is smaller than the machinery
needed to drive an async client from inside a generator inside a worker thread.

Three things make this harder than the MCP client, and each one shapes the design:

**The framing is by byte count, not by line.** LSP prefixes every message with
`Content-Length: N\\r\\n\\r\\n`. A `readline` loop cannot read it, because a message body
legitimately contains newlines. So the pipe is opened in binary and read by count.

**The server talks first, and keeps talking.** Diagnostics are not a reply to anything — they
arrive as `textDocument/publishDiagnostics` whenever the server has finished thinking, which
may be twice for one file as it refines its answer. A per-request reader like the MCP client's
would either miss them or consume a reply meant for someone else. So there is one persistent
reader thread that dispatches by message shape, and callers wait on events it sets.

**The server asks us questions.** pyright sends `workspace/configuration` and
`client/registerCapability` during startup and *blocks until they are answered*. A client that
treats every inbound message as a notification hangs on initialize and looks, from the
outside, exactly like a server that failed to start. Answering them is not optional.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

#: How we introduce ourselves. Servers log it and a few branch on it.
CLIENT_INFO = {"name": "kith", "version": "1.0.0"}

#: How long to wait for a server to come up. pyright reads a whole project before it answers
#: `initialize`, and on a large repository that is genuinely slow the first time.
START_TIMEOUT = 30.0

#: A normal request. Generous because the answer may be blocked behind indexing.
REQUEST_TIMEOUT = 20.0

#: After the first batch of diagnostics arrives, how long to keep listening for a revision.
#: Servers publish a fast approximate answer and then a better one; returning the first is how
#: you report import errors that resolve themselves a quarter of a second later.
DIAGNOSTIC_SETTLE = 0.5


class LSPError(RuntimeError):
    """The server could not be reached, or answered with something unusable."""


def to_uri(path: str | Path) -> str:
    """A filesystem path as the `file://` URI the protocol wants."""
    return "file://" + quote(str(Path(path).resolve()))


def from_uri(uri: str) -> str:
    """Back the other way, for turning a server's answer into something readable."""
    if not uri.startswith("file://"):
        return uri
    return unquote(urlparse(uri).path)


#: Extension to the protocol's language id. Distinct from the tree-sitter table in
#: `code/outline.py`: these are the ids servers expect, and they differ (`typescriptreact`,
#: not `tsx`).
LANGUAGE_IDS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascriptreact",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".swift": "swift",
    ".kt": "kotlin",
}


def language_id(path: str | Path) -> str:
    return LANGUAGE_IDS.get(Path(str(path)).suffix.lower(), "plaintext")


class LanguageServer:
    """One running language server, and the conversation with it."""

    def __init__(
        self,
        command: list[str],
        root: str | Path,
        label: str = "",
        env: dict[str, str] | None = None,
    ):
        self.command = list(command)
        self.root = Path(str(root)).resolve()
        self.label = label or (command[0] if command else "language server")
        self._env = dict(env or {})

        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._next_id = 0
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()

        #: id -> slot. A slot is filled by the reader and its event set.
        self._pending: dict[int, dict[str, Any]] = {}
        #: uri -> the last diagnostics published for it.
        self._diagnostics: dict[str, list[dict]] = {}
        #: uri -> event, set every time diagnostics arrive for that uri.
        self._published: dict[str, threading.Event] = {}
        #: uri -> version, so a change is announced as a new version like the protocol wants.
        self._open: dict[str, int] = {}
        #: Work the server has told us it is busy with. See `wait_until_idle`.
        self._working: set[str] = set()
        self._idle = threading.Event()
        self._idle.set()

        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self._stderr_tail: list[str] = []
        self._died: str = ""

    # -- lifecycle ---------------------------------------------------------- #

    def start(self, timeout: float = START_TIMEOUT) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.root),
                env={**os.environ, **self._env},
            )
        except (OSError, ValueError) as exc:
            raise LSPError(f"could not start {self.label}: {exc}") from None

        self._reader = threading.Thread(target=self._pump, name=f"kith-lsp-{self.label}", daemon=True)
        self._reader.start()
        # Drained rather than DEVNULL'd: a server that refuses to start explains why on
        # stderr, and "it did not answer" is a useless thing to report when the real message
        # was "no tsconfig.json found". Kept to a tail so a chatty server cannot grow forever.
        threading.Thread(target=self._drain_stderr, name=f"kith-lsp-err-{self.label}", daemon=True).start()

        hello = self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "clientInfo": CLIENT_INFO,
                "rootUri": to_uri(self.root),
                "rootPath": str(self.root),
                "workspaceFolders": [{"uri": to_uri(self.root), "name": self.root.name}],
                "capabilities": _CAPABILITIES,
            },
            timeout=timeout,
        )
        self.capabilities = (hello or {}).get("capabilities") or {}
        self.server_info = (hello or {}).get("serverInfo") or {}
        self.notify("initialized", {})

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        # Ask politely first: a server killed mid-write can leave a lock file or a stale
        # index, and gopls in particular does real cleanup on shutdown.
        with contextlib.suppress(Exception):
            self._write({"jsonrpc": "2.0", "id": -1, "method": "shutdown", "params": None}, process)
            self._write({"jsonrpc": "2.0", "method": "exit"}, process)
        try:
            process.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            with contextlib.suppress(OSError):
                process.kill()
        for stream in (process.stdin, process.stdout, process.stderr):
            with contextlib.suppress(Exception):
                if stream:
                    stream.close()
        with self._state_lock:
            for slot in self._pending.values():
                slot["error"] = "the server was stopped"
                slot["event"].set()
            self._pending.clear()
            self._open.clear()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def why_it_died(self) -> str:
        """What the server said on its way out, for an error worth reading."""
        if self._died:
            return self._died
        return " ".join(self._stderr_tail[-4:]).strip()

    # -- documents ---------------------------------------------------------- #

    def open_document(self, path: str | Path, text: str | None = None) -> str:
        """Tell the server about a file, or re-tell it if the file has changed.

        Everything else here needs the document to be open — a server answers `references`
        against the copy it holds, not against the disk. Re-opening on every call rather than
        tracking edits is deliberate: the file may have been changed by `edit_file`, by a
        build, or by the person, and a stale copy produces confidently wrong line numbers.
        """
        target = Path(str(path)).resolve()
        uri = to_uri(target)
        if text is None:
            try:
                text = target.read_text(errors="replace")
            except OSError as exc:
                raise LSPError(f"cannot read {path}: {exc}") from None

        with self._state_lock:
            self._published[uri] = threading.Event()
            version = self._open.get(uri, 0) + 1
            self._open[uri] = version
            known = version > 1

        if known:
            self.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    # Whole-document sync. Incremental would mean tracking ranges through
                    # every edit path in the app to save bytes on a local pipe.
                    "contentChanges": [{"text": text}],
                },
            )
        else:
            self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id(target),
                        "version": version,
                        "text": text,
                    }
                },
            )
        return uri

    # There is deliberately no `close_document`. Nothing needs one: `stop()` takes the whole
    # process with it, so there is no leak a close would plug, and a method with no caller is
    # a claim about the design that nothing checks. `test_nothing_here_is_unreachable.py`
    # caught it sitting here unused within an hour of it being written.

    def diagnostics(self, uri: str, timeout: float = REQUEST_TIMEOUT) -> list[dict]:
        """Wait for what the server thinks is wrong with this file.

        Two waits, not one. The first is for anything at all, because the server has to
        analyse the file and may be busy indexing the project. The second is a short settle:
        having published once, servers frequently publish again a moment later with a better
        answer, and the difference between the two is usually every import in the file being
        briefly unresolvable. Returning the first answer means reporting errors that were
        never real.
        """
        with self._state_lock:
            event = self._published.get(uri)
        if event is None:
            return []

        if not event.wait(timeout):
            if not self.alive:
                raise LSPError(f"{self.label} stopped: {self.why_it_died or 'no reason given'}")
            return []

        deadline = time.monotonic() + DIAGNOSTIC_SETTLE
        while time.monotonic() < deadline:
            event.clear()
            if not event.wait(max(0.0, deadline - time.monotonic())):
                break
        with self._state_lock:
            return list(self._diagnostics.get(uri) or [])

    # -- the wire ----------------------------------------------------------- #

    def request(self, method: str, params: Any, timeout: float = REQUEST_TIMEOUT) -> Any:
        process = self._process
        if process is None or process.poll() is not None:
            raise LSPError(
                f"{self.label} is not running{': ' + self.why_it_died if self.why_it_died else ''}"
            )

        with self._send_lock:
            self._next_id += 1
            message_id = self._next_id
        slot: dict[str, Any] = {"event": threading.Event()}
        with self._state_lock:
            self._pending[message_id] = slot

        try:
            self._write({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params}, process)
        except Exception:
            with self._state_lock:
                self._pending.pop(message_id, None)
            raise

        if not slot["event"].wait(timeout):
            with self._state_lock:
                self._pending.pop(message_id, None)
            if not self.alive:
                raise LSPError(f"{self.label} stopped: {self.why_it_died or 'no reason given'}")
            raise LSPError(f"{self.label} did not answer {method} within {timeout:g}s")

        with self._state_lock:
            self._pending.pop(message_id, None)
        if slot.get("error"):
            raise LSPError(f"{method}: {slot['error']}")
        return slot.get("result")

    def notify(self, method: str, params: Any) -> None:
        process = self._process
        if process is None:
            return
        with contextlib.suppress(Exception):
            self._write({"jsonrpc": "2.0", "method": method, "params": params}, process)

    def _write(self, message: dict, process: subprocess.Popen) -> None:
        body = json.dumps(message).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        with self._send_lock:
            if process.stdin is None:
                raise LSPError(f"{self.label} has no input to write to")
            try:
                process.stdin.write(header + body)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise LSPError(f"{self.label} closed its input ({exc})") from None

    # -- reading ------------------------------------------------------------ #

    def _pump(self) -> None:
        """One thread, reading frames for the life of the server.

        Everything the server says arrives here: replies to our requests, notifications it
        volunteers, and requests of its own. Dispatching by shape rather than by expectation
        is what makes unsolicited diagnostics work at all.
        """
        process = self._process
        stream = process.stdout if process else None
        if stream is None:
            return
        try:
            while True:
                message = _read_frame(stream)
                if message is None:
                    break
                self._dispatch(message)
        except Exception as exc:  # a dead pipe is the normal way this ends
            self._died = str(exc)
        finally:
            # Nobody is going to answer the in-flight requests now.
            with self._state_lock:
                for slot in self._pending.values():
                    slot["error"] = f"{self.label} stopped: {self.why_it_died or 'the pipe closed'}"
                    slot["event"].set()
                self._pending.clear()
                for event in self._published.values():
                    event.set()
                self._working.clear()
            # Nothing is going to finish now either; a waiter must not hang on a dead server.
            self._idle.set()

    def _dispatch(self, message: dict) -> None:
        message_id = message.get("id")
        method = message.get("method")

        if method is None and message_id is not None:
            with self._state_lock:
                slot = self._pending.get(message_id)
            if slot is not None:
                if "error" in message:
                    detail = message["error"] or {}
                    slot["error"] = detail.get("message") if isinstance(detail, dict) else str(detail)
                else:
                    slot["result"] = message.get("result")
                slot["event"].set()
            return

        if method == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = params.get("uri") or ""
            with self._state_lock:
                self._diagnostics[uri] = list(params.get("diagnostics") or [])
                event = self._published.get(uri)
            if event is not None:
                event.set()
            return

        if method == "$/progress":
            self._note_progress(message.get("params") or {})
            return

        if message_id is not None:
            # A request from the server. It is *waiting* for this, so an unhandled one is a
            # hang rather than a missed feature.
            self._answer_server_request(message_id, str(method or ""), message.get("params"))

    def _note_progress(self, params: dict) -> None:
        """Track what the server says it is busy with.

        pyright announces its initial workspace scan this way, and until that scan finishes a
        cross-file question has a perfectly well-formed wrong answer: `references` returns an
        empty list, because as far as the server knows nothing references anything yet.
        Measured on a three-file project — 0 references at 0.3s, 3 at 0.8s. An empty list is
        indistinguishable from "nothing uses this", which is the single most dangerous wrong
        answer this whole layer could give: it is the answer you act on by deleting something.
        """
        token = str(params.get("token") or "")
        kind = str(((params.get("value") or {}).get("kind")) or "")
        if not token:
            return
        with self._state_lock:
            if kind == "begin":
                self._working.add(token)
            elif kind == "end":
                self._working.discard(token)
            busy = bool(self._working)
        if busy:
            self._idle.clear()
        else:
            self._idle.set()

    def wait_until_idle(self, timeout: float = 15.0) -> bool:
        """Wait for the server to stop working. True if it is idle, False if it timed out.

        Best-effort by design: a server that reports no progress at all is treated as idle
        immediately, because the alternative is waiting the full timeout on every call to a
        server that was never going to say anything.
        """
        return self._idle.wait(timeout)

    def _answer_server_request(self, message_id: Any, method: str, params: Any) -> None:
        process = self._process
        if process is None:
            return
        if method == "window/workDoneProgress/create":
            # The token exists from here on; `begin` may arrive before we would otherwise
            # know about it. Registering at creation closes that gap.
            token = str((params or {}).get("token") or "")
            if token:
                with self._state_lock:
                    self._working.add(token)
                self._idle.clear()
            result = None
            with contextlib.suppress(Exception):
                self._write({"jsonrpc": "2.0", "id": message_id, "result": result}, process)
            return
        if method == "workspace/configuration":
            # One entry per item asked about. `null` means "no opinion, use your defaults",
            # which is what we want — the project's own config files should win.
            items = (params or {}).get("items") or []
            result: Any = [None for _ in items]
        elif method in ("workspace/workspaceFolders",):
            result = [{"uri": to_uri(self.root), "name": self.root.name}]
        else:
            # registerCapability, workDoneProgress/create, applyEdit and the rest. Answering
            # null is a valid "fine" for the ones that matter and harmless for the others.
            result = None
        with contextlib.suppress(Exception):
            self._write({"jsonrpc": "2.0", "id": message_id, "result": result}, process)

    def _drain_stderr(self) -> None:
        process = self._process
        stream = process.stderr if process else None
        if stream is None:
            return
        with contextlib.suppress(Exception):
            for raw in iter(stream.readline, b""):
                line = raw.decode(errors="replace").strip()
                if line:
                    self._stderr_tail.append(line)
                    del self._stderr_tail[:-20]


def _read_frame(stream) -> dict | None:
    """One `Content-Length`-framed JSON-RPC message, or None at end of stream."""
    length = -1
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break  # blank line ends the headers
        if line.lower().startswith(b"content-length:"):
            try:
                length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                # A byte-framed stream cannot be resynchronised without a valid length: the
                # body would be read as the next message's headers and every message after
                # it would be garbage. Fail loudly here rather than hang until a timeout.
                raise LSPError(f"invalid Content-Length: {line.decode(errors='replace')!r}") from None
        # Any other header line, or a stray log line a server printed to stdout, is skipped.
    if length < 0:
        raise LSPError("a message arrived with no Content-Length header")
    if length == 0:
        return {}
    # `read` on a pipe can return early; a short read here would desynchronise the stream
    # for every message after it, which presents as bizarre intermittent parse failures.
    chunks, remaining = [], length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    try:
        return json.loads(b"".join(chunks).decode(errors="replace"))
    except ValueError:
        return {}


#: What we tell the server we can do. Declaring only what is used keeps servers from sending
#: things nothing reads — several will skip work entirely for a capability the client did
#: not announce.
_CAPABILITIES: dict[str, Any] = {
    "textDocument": {
        "synchronization": {"dynamicRegistration": False, "didSave": False},
        "publishDiagnostics": {"relatedInformation": True, "versionSupport": False},
        "definition": {"dynamicRegistration": False, "linkSupport": True},
        "references": {"dynamicRegistration": False},
        "documentSymbol": {"dynamicRegistration": False, "hierarchicalDocumentSymbolSupport": True},
        "rename": {"dynamicRegistration": False, "prepareSupport": False},
        "hover": {"dynamicRegistration": False, "contentFormat": ["plaintext", "markdown"]},
    },
    "workspace": {
        "workspaceFolders": True,
        "configuration": True,
        "applyEdit": False,
        "symbol": {"dynamicRegistration": False},
        "didChangeConfiguration": {"dynamicRegistration": False},
    },
    "window": {"workDoneProgress": True},
}


#: Severity numbers the protocol uses, as words.
SEVERITY = {1: "error", 2: "warning", 3: "information", 4: "hint"}


def readable_diagnostics(uri: str, raw: list[dict], limit: int = 60) -> list[dict]:
    """Diagnostics as something compact and sorted, errors first."""
    out = []
    for one in raw:
        start = (one.get("range") or {}).get("start") or {}
        out.append(
            {
                "severity": SEVERITY.get(one.get("severity") or 1, "error"),
                "line": int(start.get("line", 0)) + 1,
                "column": int(start.get("character", 0)) + 1,
                "message": " ".join(str(one.get("message") or "").split()),
                "source": str(one.get("source") or ""),
            }
        )
    order = {"error": 0, "warning": 1, "information": 2, "hint": 3}
    out.sort(key=lambda d: (order.get(d["severity"], 9), d["line"]))
    return out[:limit]


def position(line: int, column: int) -> dict:
    """A 1-based line/column from a human or a grep hit, as the 0-based pair LSP wants."""
    return {"line": max(0, line - 1), "character": max(0, column - 1)}


def locations(result: Any, limit: int = 80) -> list[dict]:
    """`Location | Location[] | LocationLink[]` — all three shapes — as one list.

    Every one of these is a legal answer to `definition`, and which you get depends on the
    server rather than on the request. Normalising here keeps the branch out of four callers.
    """
    if result is None:
        return []
    items = result if isinstance(result, list) else [result]
    out = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri") or ""
        span = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange") or {}
        start = (span or {}).get("start") or {}
        out.append(
            {
                "path": from_uri(str(uri)),
                "line": int(start.get("line", 0)) + 1,
                "column": int(start.get("character", 0)) + 1,
            }
        )
    return out


def edits_from_workspace_edit(edit: Any) -> list[tuple[str, list[dict]]]:
    """A `WorkspaceEdit` flattened to (path, edits) pairs.

    Two shapes again — `changes` keyed by uri, or `documentChanges` as a list — and a server
    may use either. A rename that silently applied half a WorkspaceEdit because it only
    understood one shape would be the worst possible outcome for this feature.
    """
    if not isinstance(edit, dict):
        return []
    out: list[tuple[str, list[dict]]] = []
    changes = edit.get("changes")
    if isinstance(changes, dict):
        for uri, items in changes.items():
            out.append((from_uri(str(uri)), list(items or [])))
    document_changes = edit.get("documentChanges")
    if isinstance(document_changes, list):
        for entry in document_changes:
            if not isinstance(entry, dict) or "textDocument" not in entry:
                continue  # a create/rename/delete file operation, not a text edit
            uri = (entry.get("textDocument") or {}).get("uri") or ""
            out.append((from_uri(str(uri)), list(entry.get("edits") or [])))
    return out


def apply_text_edits(text: str, edits: list[dict]) -> str:
    """Apply LSP `TextEdit`s to a string.

    Back to front, because every edit's range is stated against the *original* document and
    applying forwards invalidates every range after the first.
    """
    lines = text.split("\n")

    def offset(position_: dict) -> int:
        line = max(0, min(int(position_.get("line", 0)), len(lines)))
        base = sum(len(one) + 1 for one in lines[:line])
        return base + max(0, int(position_.get("character", 0)))

    ordered = sorted(
        (one for one in edits if isinstance(one, dict) and one.get("range")),
        key=lambda one: offset(one["range"]["start"]),
        reverse=True,
    )
    out = text
    for one in ordered:
        start = offset(one["range"]["start"])
        end = offset(one["range"]["end"])
        out = out[:start] + str(one.get("newText") or "") + out[end:]
    return out


def find_symbol_position(text: str, symbol: str, near_line: int = 0) -> tuple[int, int] | None:
    """Where `symbol` appears, as a 1-based (line, column).

    The tools take a name rather than a cursor position, because a model has a name and does
    not have a cursor. When a line is given the nearest occurrence to it wins; otherwise the
    first. Word-boundary matched, so asking about `user` does not land inside `username`.
    """
    import re

    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    hits: list[tuple[int, int]] = []
    for index, line in enumerate(text.split("\n"), start=1):
        for match in pattern.finditer(line):
            hits.append((index, match.start() + 1))
    if not hits:
        return None
    if near_line:
        return min(hits, key=lambda hit: abs(hit[0] - near_line))
    return hits[0]


#: Callback type for the manager's "server died" hook.
OnExit = Callable[[str], None]
