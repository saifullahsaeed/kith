"""Finding a name by what it *is*, rather than by the characters that spell it.

`grep foo` answers a question nobody asked: where do those three characters appear. The
answer includes the definition, every call, the word in a comment, a substring of `foobar`,
the string `"foo"` in a fixture, and the changelog entry — and separating them is manual work
he pays for in reading. This answers the question actually being asked: **where is this
defined, and where is it called.**

That distinction is worth more here than it looks, for a reason that has nothing to do with
elegance. Text search runs a binary, and which binary it finds is a property of the *launcher*
rather than the machine: a GUI-launched macOS app inherits `launchd`'s `PATH`, where ripgrep is
not, so every shipped copy silently fell back to `grep -E` and its basic-regex semantics.
`infra/executables.py` repairs that now. This does not depend on the repair, because it runs no
binary at all — which is the more durable answer to a class of problem that will recur the next
time something is discovered on `PATH`.

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

#: Directory names that mean "this is a test", and filename shapes that mean the same. Covers
#: the conventions of the languages here: `test_x.py`, `x_test.go`, `x.test.ts`, `x.spec.ts`,
#: and the folders every ecosystem agrees on.
_TEST_DIRS: frozenset[str] = frozenset({"tests", "test", "__tests__", "spec", "e2e", "testing"})
_TEST_MARKS: tuple[str, ...] = ("test_", "_test.", ".test.", ".spec.", "spec_", "_spec.")


def is_test(relative: str) -> bool:
    """Whether a hit is in test code, from its path alone.

    Not a judgement about quality — a split. "Four call sites" and "four call sites, three of
    them tests" are different facts about how safe a change is, and the second one is the
    question actually being asked before an edit: who depends on this, and is any of it
    covering me.

    From the path rather than the content on purpose. Reading files to classify them would
    double the cost of a search to answer something the convention already states, and every
    ecosystem here states it the same way.
    """
    parts = relative.replace("\\", "/").split("/")
    if any(one in _TEST_DIRS for one in parts[:-1]):
        return True
    name = parts[-1]
    # `test_x.py`, `x_test.go`, `x.test.ts` — but deliberately not a bare `startswith("test")`.
    # That matched `engine/run/testing.py`, which *runs* tests and is production code, and the
    # misclassification made this module claim a symbol had no test coverage when it had four
    # tests one directory over. A heuristic that is wrong in the direction of "safe to delete"
    # is worse than no heuristic.
    if any(mark in name for mark in _TEST_MARKS):
        return True
    return name.rsplit(".", 1)[0] in ("test", "tests", "spec")


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
    #: Whether any test file was in scope at all. Without this, "0 in tests" is ambiguous
    #: between "nothing covers this" and "no tests were looked at" — and reporting the first
    #: when the second is true invites deleting something that is covered. Searching `kith/`
    #: rather than the folder above it produces exactly that, which is how this was found.
    saw_tests = False

    files = repomap.candidates(here)[:MAX_FILES]
    for seen_files, path in enumerate(files, 1):
        try:
            source, language = outline.source_of(path)
        except outline.OutlineError:
            continue  # unreadable, too big, binary — the map skips these too
        searched += 1
        relative = str(path.relative_to(here)) if path.is_relative_to(here) else str(path)
        saw_tests = saw_tests or is_test(relative)
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
        "saw_tests": saw_tests,
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

    # The blast radius, in one line. Before changing something the question is not only "how
    # many places use this" but "is any of that a test" — four call sites of which three are
    # tests is a safe change with cover; four with none is a change nothing is watching.
    from_tests = [one for one in calls if is_test(one["path"])]
    from_code = [one for one in calls if not is_test(one["path"])]
    head = f"{name} — {len(definitions)} definition(s), {len(calls)} call(s)"
    if calls and found.get("saw_tests"):
        head += f": {len(from_code)} in code, {len(from_tests)} in tests"
        if not from_tests:
            head += " — nothing covering it"
    elif calls:
        # No test file was in scope, so the split would be an accident of where the search
        # started rather than a fact about the code. Saying "0 in tests" here is how somebody
        # deletes a covered function.
        head += " — no tests were in scope, so this says nothing about coverage"

    rows: list[str] = [head]
    if definitions:
        rows.append("")
        rows.append("defined")
        rows += [f"  {one['path']}:{one['line']}  {one['kind']}  {one['text']}" for one in definitions]
    if from_code:
        rows.append("")
        rows.append("called from code")
        rows += [f"  {one['path']}:{one['line']}  {one['text']}" for one in from_code]
    if from_tests:
        rows.append("")
        rows.append("called from tests")
        rows += [f"  {one['path']}:{one['line']}  {one['text']}" for one in from_tests]

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
