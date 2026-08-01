"""The four questions worth asking a language server.

Not a wrapper around the whole protocol — a language server can do thirty things and most of
them are for an editor with a cursor and a human attached. These four are the ones that
change what he can do:

* **diagnostics** — what is wrong with this file, now, in milliseconds. `check_code` runs the
  project's whole checker and returns clipped output; useful, slow, and something he has to
  remember. This is per-file and immediate.
* **references** — who uses this, *exactly*. grep answers a similar question and gets it wrong
  in both directions: it matches the word in comments and strings, and it misses re-exports
  and aliased imports.
* **definition** — where does this come from, without guessing at a filename.
* **rename** — change a name everywhere it is used and nowhere it merely appears. This project
  has a scar from the alternative: a regex rename that mangled three comments *about* a word
  while missing uses of it.

Everything here takes a **symbol name**, not a cursor position. A model has a name; it does
not have a cursor, and making it compute a character offset before it can ask a question is
a round spent on arithmetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kith.services.lsp import client as lsp
from kith.services.lsp.manager import Unavailable, manager

#: Cap on references returned. A common helper in a large codebase has hundreds, and the
#: hundredth is not what he is looking for — the count is, and it is reported separately.
MAX_REFERENCES = 60


#: How long to let a server finish indexing before asking it a question about the whole
#: project. Only paid once per server — after the initial scan it is already idle.
INDEX_TIMEOUT = 25.0

#: How many times to re-ask a cross-file question that came back empty, and how long to wait
#: between tries.
#:
#: Waiting on the server's own progress reporting is not sufficient, and the trace says why.
#: Asked at t+0.11s, pyright has not yet *created* the progress token for its workspace scan,
#: so "is it busy" is answered "no" — correctly, and uselessly. It reports 0 references at
#: t+0.25 and 3 at t+0.56, having said nothing in between that distinguishes the two.
#:
#: An empty answer is therefore ambiguous in the worst possible direction: "nothing uses
#: this" and "I have not looked yet" are the same reply, and the first is the one somebody
#: deletes a function on. So an empty answer is re-asked. A symbol that genuinely has no
#: references costs about a second to establish, which is the right side to be wrong on.
EMPTY_RETRIES = 4
RETRY_PAUSE = 0.35


def _server_and_uri(path: str | Path, settled: bool = False) -> tuple[Any, str, str]:
    """A server with this file open, optionally waited on until it has finished indexing.

    `settled` is for the cross-file questions. Until a server has scanned the project it
    answers `references` with an empty list — not an error, a confident and wrong "nothing
    uses this", which is the answer somebody deletes a function on. Diagnostics do not need
    it: they are about this file and the server publishes them when it is ready.
    """
    server = manager.for_file(path)
    text = Path(str(path)).read_text(errors="replace")
    uri = server.open_document(path, text)
    if settled:
        server.wait_until_idle(INDEX_TIMEOUT)
    return server, uri, text


def _ask_until_answered(server: Any, method: str, params: dict, empty) -> Any:
    """Ask a cross-file question, re-asking while the answer is empty.

    `empty` decides what counts as no answer, because the three callers disagree: a list for
    references, a location for definition, a WorkspaceEdit for rename.
    """
    import time

    result = None
    for attempt in range(EMPTY_RETRIES):
        result = server.request(method, params)
        if not empty(result):
            return result
        if attempt < EMPTY_RETRIES - 1:
            time.sleep(RETRY_PAUSE)
            server.wait_until_idle(INDEX_TIMEOUT)
    return result


def _covers(edit: Any, expected: set[str]) -> bool:
    """Does this WorkspaceEdit touch every file we know uses the symbol?"""
    if not expected:
        return bool(lsp.edits_from_workspace_edit(edit))
    touched = {str(Path(path).resolve()) for path, _ in lsp.edits_from_workspace_edit(edit)}
    return expected <= touched


def _position_of(text: str, symbol: str, near_line: int, path: str | Path) -> dict:
    found = lsp.find_symbol_position(text, symbol, near_line)
    if found is None:
        raise Unavailable(
            f"`{symbol}` does not appear in {Path(str(path)).name}. Check the spelling, or "
            "grep for it to find which file it is actually in."
        )
    return lsp.position(*found)


def diagnostics(path: str | Path) -> dict:
    """What the language server thinks is wrong with one file."""
    server, uri, _ = _server_and_uri(path)
    raw = server.diagnostics(uri)
    found = lsp.readable_diagnostics(uri, raw)
    errors = sum(1 for one in found if one["severity"] == "error")
    return {
        "path": str(path),
        "server": server.label,
        "clean": not found,
        "errors": errors,
        "warnings": len(found) - errors,
        "problems": found,
    }


def references(path: str | Path, symbol: str, near_line: int = 0) -> dict:
    """Every place a symbol is used."""
    server, uri, text = _server_and_uri(path, settled=True)
    position = _position_of(text, symbol, near_line, path)
    result = _ask_until_answered(
        server,
        "textDocument/references",
        {
            "textDocument": {"uri": uri},
            "position": position,
            "context": {"includeDeclaration": False},
        },
        empty=lambda answer: not lsp.locations(answer, limit=1),
    )
    found = lsp.locations(result, limit=MAX_REFERENCES + 1)
    return {
        "symbol": symbol,
        "server": server.label,
        "count": len(found),
        "truncated": len(found) > MAX_REFERENCES,
        "references": found[:MAX_REFERENCES],
    }


def definition(path: str | Path, symbol: str, near_line: int = 0) -> dict:
    """Where a symbol comes from."""
    server, uri, text = _server_and_uri(path, settled=True)
    position = _position_of(text, symbol, near_line, path)
    result = _ask_until_answered(
        server,
        "textDocument/definition",
        {"textDocument": {"uri": uri}, "position": position},
        empty=lambda answer: not lsp.locations(answer, limit=1),
    )
    found = lsp.locations(result)
    if not found:
        # A builtin, or something the server cannot resolve. Not an error — the honest answer
        # is that there is nowhere to go.
        return {"symbol": symbol, "server": server.label, "found": False, "definitions": []}
    return {"symbol": symbol, "server": server.label, "found": True, "definitions": found}


def rename(path: str | Path, symbol: str, new_name: str, near_line: int = 0) -> dict:
    """Rename a symbol everywhere it is used, and nowhere it merely appears.

    Applied here rather than handed back as a plan. A `WorkspaceEdit` the caller has to apply
    itself is a second chance to get it wrong, and the whole value of this over a search and
    replace is that the edit is exact.

    Not atomic across files in the way `edit_files` is, and honest about it: the server's edit
    is applied file by file, and a write that fails partway says which files were already
    changed. Making it atomic would mean holding every file in memory and is worth doing if
    this ever fails in practice; pretending it is atomic would not be.
    """
    if not new_name or not new_name.strip():
        raise Unavailable("a rename needs a new name")
    new_name = new_name.strip()
    if new_name == symbol:
        return {"symbol": symbol, "renamed": 0, "files": [], "note": "that is already its name"}

    server, uri, text = _server_and_uri(path, settled=True)
    position = _position_of(text, symbol, near_line, path)
    # Establish who uses this *before* renaming it, for two reasons. It warms the index, so
    # the rename is not the request that races it. And it gives something to check the
    # server's answer against — the failure that matters here is not an empty rename, it is a
    # *partial* one: a definition renamed while its call sites keep the old name, which looks
    # like success and leaves the project broken. Counting files cannot be spoofed by timing.
    expected = {
        str(Path(one["path"]).resolve())
        for one in lsp.locations(
            _ask_until_answered(
                server,
                "textDocument/references",
                {
                    "textDocument": {"uri": uri},
                    "position": position,
                    "context": {"includeDeclaration": True},
                },
                empty=lambda answer: not lsp.locations(answer, limit=1),
            ),
            limit=500,
        )
    }

    result = _ask_until_answered(
        server,
        "textDocument/rename",
        {"textDocument": {"uri": uri}, "position": position, "newName": new_name},
        empty=lambda answer: not _covers(answer, expected),
    )
    changes = lsp.edits_from_workspace_edit(result)
    if expected and not _covers(result, expected):
        missed = sorted(expected - {str(Path(p).resolve()) for p, _ in changes})
        raise Unavailable(
            f"{server.label} offered a rename that misses {len(missed)} file(s) that use "
            f"`{symbol}` ({', '.join(Path(one).name for one in missed[:4])}). Nothing was "
            "changed — a half-done rename is worse than none. Try again in a moment, or "
            "rename it with edit_files if you are sure of every site."
        )
    if not changes:
        return {
            "symbol": symbol,
            "renamed": 0,
            "files": [],
            "note": (
                f"{server.label} would not rename `{symbol}` — it may be defined outside this "
                "project, or be a keyword or builtin."
            ),
        }

    # Everything in memory first, so a bad edit is found before any file is touched.
    staged: list[tuple[Path, str, int]] = []
    for file_path, edits in changes:
        target = Path(file_path)
        if not target.is_file():
            continue
        try:
            before = target.read_text()
        except OSError as exc:
            raise Unavailable(f"cannot read {target} to rename in it: {exc}") from None
        staged.append((target, lsp.apply_text_edits(before, edits), len(edits)))

    written: list[str] = []
    total = 0
    for target, after, count in staged:
        try:
            target.write_text(after)
        except OSError as exc:
            raise Unavailable(
                f"renamed in {', '.join(written) or 'nothing'} and then could not write "
                f"{target}: {exc}. The rename is partly applied — check `changes`."
            ) from None
        written.append(target.name)
        total += count
        # The server is holding a stale copy of a file we just rewrote.
        server.open_document(target, after)

    return {
        "symbol": symbol,
        "newName": new_name,
        "server": server.label,
        "renamed": total,
        "files": [str(target) for target, _, _ in staged],
    }
