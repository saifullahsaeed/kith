"""Finding one definition in a file, so it can be read instead of the file.

The tool advice has always been "outline first, then `read_file` with offset and limit", and
it is good advice that costs two rounds and relies on him doing arithmetic on line numbers he
just read. This closes that loop: name the definition, get the definition.

**This locates; it does not read.** `locate` returns a line span and nothing else, and the
caller passes that span to the reader it already has. That is deliberate — `read_file` owns
the permission check, the line numbering, the output budget and the "there is more, ask with
offset=" sentence, and a second reader here would be a second set of all four, drifting.

Names are matched the way someone would write them. `server_for` finds it if there is only
one; `Manager.server_for` finds that one specifically when several classes have a method by
that name. Ambiguity and absence are both **answered rather than guessed at** — the error
names the candidates, or the definitions that do exist, because "not found" with no list is
what sends him back to reading the whole file, which is the thing this exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kith.engine.code import outline

#: How many names an error message may list before it stops being help and starts being a
#: second file to read. Enough to recognise what you meant to type.
_MOST_CANDIDATES = 40


class ExcerptError(Exception):
    """No single definition answers to that name. The message says which ones do."""


@dataclass(frozen=True)
class Span:
    """Where one definition lives, and what it is."""

    #: Fully qualified — `Manager.server_for` — which is what an ambiguous request should be
    #: retried with, so it is what the caller is told it got.
    qualified: str
    kind: str
    line: int
    end_line: int
    #: Lines in the whole file, so a caller can say "31 of 365" and the model can tell the
    #: difference between reading a method and reading most of a module.
    of_lines: int

    @property
    def count(self) -> int:
        """Lines in the definition, inclusive — `read_file`'s `limit`."""
        return max(1, self.end_line - self.line + 1)


def qualify(symbols: list[outline.Symbol]) -> list[tuple[str, outline.Symbol]]:
    """Each symbol with its dotted path, from the depth column.

    `of_source` walks the tree in order and records how deep each definition sits, so the
    enclosing scope of a symbol at depth *d* is the last symbol seen at depth *d-1*. That is
    enough to rebuild `Manager.server_for` without the parser holding parent links, and it is
    why this is a function over `Symbol` rather than a change to the walk.
    """
    out: list[tuple[str, outline.Symbol]] = []
    stack: list[str] = []
    for symbol in symbols:
        del stack[symbol.depth :]
        stack.append(symbol.name)
        out.append((".".join(stack), symbol))
    return out


def locate(path: str | Path, name: str) -> Span:
    """Where `name` is defined in `path`.

    Raises `ExcerptError` naming the alternatives when the name is ambiguous, and naming what
    the file does contain when it matches nothing.
    """
    wanted = (name or "").strip()
    if not wanted:
        raise ExcerptError("no symbol name given")

    # Raises OutlineError — unknown extension, too big, binary, no grammar — in the same words
    # `outline` would use, because it is the same function.
    source, language = outline.source_of(path)
    pairs = qualify(outline.of_source(source, language))
    if not pairs:
        raise ExcerptError(f"{path} has no definitions to look in")

    of_lines = source.count(b"\n") + 1

    exact = [(q, s) for q, s in pairs if q == wanted]
    if len(exact) == 1:
        return _span(exact[0], of_lines)

    # A bare name, or a tail of one: `server_for` and `Manager.server_for` should both find
    # `Manager.server_for`, because both are things a person would type having just read an
    # outline.
    tail = [(q, s) for q, s in pairs if q == wanted or q.endswith("." + wanted)]
    if len(tail) == 1:
        return _span(tail[0], of_lines)
    if len(tail) > 1:
        raise ExcerptError(
            f"{wanted} is ambiguous in {path} — {len(tail)} definitions match. "
            f"Ask for one of: {_listed(q for q, _ in tail)}"
        )

    raise ExcerptError(f"no definition called {wanted} in {path}. It defines: {_listed(q for q, _ in pairs)}")


def _span(pair: tuple[str, outline.Symbol], of_lines: int) -> Span:
    qualified, symbol = pair
    # A symbol whose end the parser could not give is read from its start rather than refused:
    # one line is a worse answer than the right one and a much better answer than none.
    end = symbol.end_line if symbol.end_line >= symbol.line else symbol.line
    return Span(qualified=qualified, kind=symbol.kind, line=symbol.line, end_line=end, of_lines=of_lines)


def _listed(names) -> str:
    everything = list(names)
    shown = everything[:_MOST_CANDIDATES]
    text = ", ".join(shown)
    if len(everything) > len(shown):
        text += f", … and {len(everything) - len(shown)} more"
    return text
