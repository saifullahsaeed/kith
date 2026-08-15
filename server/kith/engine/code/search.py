"""Finding a name by what it *is*, rather than by the characters that spell it.

`grep foo` answers a question nobody asked: where do those three characters appear. The
answer includes the definition, every call, the word in a comment, a substring of `foobar`,
the string `"foo"` in a fixture, and the changelog entry — and separating them is manual work
he pays for in reading. This answers the question actually being asked: **where is this
defined, and where is it called.**

That distinction is worth more here than it looks, for a reason that has nothing to do with
elegance. `read_text`-and-regex is the fallback when ripgrep is missing, and in the packaged
application ripgrep is *always* missing — `shutil.which("rg")` cannot find it under the
environment a GUI-launched macOS app inherits, so every user has been getting `grep -E`
semantics rather than ripgrep's. A structural search does not care which binary is installed,
because it does not run one.

**How a call is recognised, across nineteen grammars.** One flat set of node types, the way
`outline._WANTED` is one flat set, and for the same reason: a table per language is the same
knowledge copied nineteen times with the maintenance multiplied to match. The set below was
not written from memory — every entry was found by parsing `target(1)` and `o.target(2)` in
each language and recording what the tree actually called the result.

**How the callee's name is read.** Most grammars put the thing being called first, so the
first named child is the callee. Java, Ruby and PHP do not: they put the *receiver* first and
the method name in a `name` or `method` field, so a first-child rule returns `o` for
`o.target(2)` — measured, in all three. Hence the field lookup before the positional
fallback, which takes all nineteen from 15/18 to 18/18.

**Dart is not supported for calls and says so.** Its grammar in this pack does not produce a
call node at all — `target(1)` parses to a bare `identifier` with the arguments unattached —
so there is nothing to match. Reporting zero calls in a Dart file would be a confident wrong
answer about somebody's code, which is the thing `outline`'s docstring already refuses to do.
Definitions in Dart still work, because those come from the outline walk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kith.engine.code import outline, repomap

#: Node types that are a call, across every grammar in `outline.LANGUAGES`.
#:
#: Found by parsing, not by recall. `command_name` is deliberately absent even though bash
#: produces one: it is a *child* of `command`, so admitting both counts every shell call twice.
_CALLS: frozenset[str] = frozenset(
    {
        "call",  # python, ruby, elixir
        "call_expression",  # ts, tsx, js, go, rust, c, cpp, swift, kotlin, scala
        "command",  # bash
        "function_call",  # lua
        "function_call_expression",  # php, plain function
        "invocation",  # sql
        "member_call_expression",  # php, method on an object
        "method_invocation",  # java
    }
)

#: Where a grammar keeps the name of the thing being called, when it does not keep it first.
#: Tried in order, before falling back to the first named child.
_NAME_FIELDS: tuple[str, ...] = ("name", "method", "function")

#: Languages whose grammar here gives no call node, so "no calls found" would be a lie.
NO_CALL_SUPPORT: frozenset[str] = frozenset({"dart"})

#: Most hits to gather before stopping. A name like `get` in a large repository has thousands,
#: and the honest response is the first hundred plus the count — not all of them, and not a
#: silent hundred.
MAX_HITS = 100

#: Most files to parse for one search. Parsing is the cost here, not walking.
MAX_FILES = 2_000

#: Longest source line returned with a hit. A minified bundle that slipped past the extension
#: check should not spend the whole result on one line.
_LINE_CHARS = 200


class SearchError(Exception):
    """The search could not be run, with a reason worth reading."""


@dataclass(frozen=True)
class Hit:
    """One place a name is defined or called."""

    #: Relative to the searched root, because that is what he can pass back to `read_file`.
    path: str
    line: int
    #: `"call"`, or the definition's kind from the outline — `class`, `def`, `method`.
    kind: str
    #: The source line, stripped and clipped. Enough to tell a real hit from a near miss
    #: without opening the file.
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "kind": self.kind, "text": self.text}


def _callee(node, source: bytes) -> str | None:
    """The name being called, or None if this node does not name one."""
    for field in _NAME_FIELDS:
        got = node.child_by_field_name(field)
        if got is not None:
            return _last_identifier(got, source)
    kids = node.named_children
    return _last_identifier(kids[0], source) if kids else None


def _last_identifier(node, source: bytes) -> str | None:
    """The last identifier-shaped leaf under `node`.

    `deep.a.target(3)` and `o.target(2)` both want `target`, and taking the last leaf gets it
    without knowing whether this grammar spells attribute access `attribute`, `field_expression`
    or `selector_expression`.
    """
    found: str | None = None

    def walk(here) -> None:
        nonlocal found
        if not here.children:
            text = source[here.start_byte : here.end_byte].decode(errors="replace")
            if text and text.replace("_", "").isalnum():
                found = text  # keeps overwriting, so the last leaf in reading order wins
        for child in here.children:
            walk(child)

    walk(node)
    return found


def calls_in(source: bytes, language: str, name: str) -> list[int]:
    """Line numbers in `source` where `name` is called. Empty for a grammar with no call node.

    This parses, and `find` has already parsed the same bytes to get the definitions — so a
    search costs two parses per file. That was left alone deliberately after measuring it:
    across the 174 parseable files in `kith/`, one pass is 0.09s and two are 0.15s. Threading a
    tree through `of_source` to save 60ms would complicate the signature every other caller
    uses, to win something nobody can perceive.
    """
    if language in NO_CALL_SUPPORT:
        return []
    parser = outline.parser_for(language)
    if parser is None:
        return []
    lines: list[int] = []
    stack = [parser.parse(source).root_node]
    while stack:
        node = stack.pop()
        if node.type in _CALLS and _callee(node, source) == name:
            lines.append(node.start_point[0] + 1)
        stack.extend(node.children)
    return lines


def find(root: str | Path, name: str) -> dict[str, Any]:
    """Every definition and call of `name` under `root`.

    Reports what it could not cover rather than presenting a partial answer as a complete one:
    `truncated` when the hit ceiling was reached, `unsupported` for files whose language has no
    call node. A search that quietly stops at a hundred is how someone concludes a symbol is
    unused and deletes it.
    """
    wanted = (name or "").strip()
    if not wanted:
        raise SearchError("no name to search for")

    here = Path(str(root))
    if not here.exists():
        raise SearchError(f"there is no {root} to search")
    if not here.is_dir():
        raise SearchError(f"{root} is a file — search takes a folder")

    definitions: list[Hit] = []
    calls: list[Hit] = []
    unsupported: set[str] = set()
    searched = 0
    truncated = 0

    files = repomap.candidates(here)[:MAX_FILES]
    for seen_files, path in enumerate(files, 1):
        try:
            source, language = outline.source_of(path)
        except outline.OutlineError:
            continue  # unreadable, too big, binary — the map skips these too
        searched += 1
        relative = str(path.relative_to(here)) if path.is_relative_to(here) else str(path)
        text_lines = source.decode(errors="replace").splitlines()

        for symbol in outline.of_source(source, language):
            if symbol.name == wanted:
                _add(definitions, Hit(relative, symbol.line, symbol.kind, _line(text_lines, symbol.line)))

        if language in NO_CALL_SUPPORT:
            unsupported.add(language)
            continue
        for line_no in calls_in(source, language, wanted):
            _add(calls, Hit(relative, line_no, "call", _line(text_lines, line_no)))

        if len(definitions) + len(calls) >= MAX_HITS:
            truncated = len(files) - seen_files
            break

    return {
        "name": wanted,
        "root": str(root),
        "definitions": [one.as_dict() for one in definitions],
        "calls": [one.as_dict() for one in calls],
        "files_searched": searched,
        "files_unsearched": truncated,
        "unsupported": sorted(unsupported),
    }


def _add(into: list[Hit], hit: Hit) -> None:
    if len(into) < MAX_HITS:
        into.append(hit)


def _line(lines: list[str], number: int) -> str:
    if 1 <= number <= len(lines):
        return lines[number - 1].strip()[:_LINE_CHARS]
    return ""


def render(found: dict[str, Any]) -> str:
    """The result as something to read, with the counts that make it trustworthy."""
    name = found["name"]
    definitions = found["definitions"]
    calls = found["calls"]
    if not definitions and not calls:
        note = f"No definition or call of {name} in {found['files_searched']} source files."
        if found["unsupported"]:
            note += f" (Calls are not searchable in: {', '.join(found['unsupported'])}.)"
        return note

    rows: list[str] = [f"{name} — {len(definitions)} definition(s), {len(calls)} call(s)"]
    if definitions:
        rows.append("")
        rows.append("defined")
        rows += [f"  {one['path']}:{one['line']}  {one['kind']}  {one['text']}" for one in definitions]
    if calls:
        rows.append("")
        rows.append("called")
        rows += [f"  {one['path']}:{one['line']}  {one['text']}" for one in calls]

    tail: list[str] = []
    if len(definitions) + len(calls) >= MAX_HITS:
        tail.append(f"stopped at {MAX_HITS} hits")
    if found["files_unsearched"]:
        tail.append(f"{found['files_unsearched']} files not searched")
    if found["unsupported"]:
        tail.append(f"calls not searchable in {', '.join(found['unsupported'])}")
    if tail:
        rows.append("")
        rows.append(f"[{'; '.join(tail)}]")
    return "\n".join(rows)
