"""The shape of a source file, without reading the source file.

His attention is the scarce thing. Asked "where is the retry handled", the only route he had
was grep for a guess, or open the file and read it — and a 2,000-line module read in full is
that module carried on every remaining round of the turn. The read-discipline fragment in his
persona exists because of exactly this, and it can only tell him to be careful; it cannot give
him a cheaper way to find out.

An outline is the cheaper way. Every class, function and method in a file, with its
declaration line and where it starts, for a couple of hundred tokens instead of the file. He
reads it, picks the one he wants, and reads *that* with an offset — which is the discipline
the persona asks for, made mechanical.

**Why tree-sitter rather than the language server.** A language server would also answer this,
and better, but it has to be installed, started, and given a project to index — which is
seconds of latency on the first call and a hard dependency on somebody having run `npm i -g`.
Structure does not need any of that. tree-sitter is a parser: no server, no project, no
configuration, milliseconds, and it works on a lone file in a folder of nothing. So structure
comes from here and *meaning* — who calls this, what type is that — comes from the LSP layer
when there is one. The two are complementary rather than a fallback for each other.

**Why a curated node list rather than "anything with a name".** Every grammar exposes a `name`
field, so walking for it is tempting and nearly works. Measured across seven languages it also
returns function parameters as top-level symbols, every struct field in Go and Rust, every
property in a TypeScript interface, and — in TSX — every JSX element in the render tree. An
outline of a React component that lists `div` and `span` as symbols is worse than no outline,
because it is confidently wrong about what the file contains.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: File extension to the grammar that reads it. Deliberately not exhaustive: a language
#: nobody here writes costs a line to add and, until someone does, an honest "I can't read
#: the shape of this one" is better than a half-supported outline.
LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".dart": "dart",
    ".sql": "sql",
}

#: Node types that are a definition worth listing, across every grammar above. One flat set
#: rather than a table per language: the names barely collide and the ones that do mean the
#: same thing, so a per-language table would be the same set copied nine times with the
#: maintenance burden multiplied to match.
_WANTED: frozenset[str] = frozenset(
    {
        # python
        "class_definition",
        "function_definition",
        # javascript / typescript / tsx
        "class_declaration",
        "abstract_class_declaration",
        "method_definition",
        "function_declaration",
        "generator_function_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        # go
        "type_spec",
        "method_declaration",
        # rust
        "struct_item",
        "enum_item",
        "function_item",
        "function_signature_item",
        "trait_item",
        "mod_item",
        "impl_item",
        "macro_definition",
        # ruby
        "class",
        "method",
        "module",
        "singleton_method",
        # c / c++
        "struct_specifier",
        "class_specifier",
        "namespace_definition",
        "enum_specifier",
        # swift / kotlin / scala / php / dart
        "protocol_declaration",
        "object_declaration",
        "trait_declaration",
        "object_definition",
        "class_definition_body",
    }
)

#: A `const x = () => …` is a function everywhere it matters, and in modern TypeScript it is
#: how most of them are written — but `const n = 4` is not a symbol, and both are
#: `variable_declarator`. So this one node type is admitted only when what it is bound to is
#: callable.
_CALLABLE_VALUES = frozenset({"arrow_function", "function", "function_expression", "generator_function"})

#: Node type to the word for it. Missing types fall back to a tidied form of the type name,
#: which reads acceptably for the long tail ("function signature item" → "function signature").
_KIND = {
    "class_definition": "class",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "class_specifier": "class",
    "class": "class",
    "struct_item": "struct",
    "struct_specifier": "struct",
    "function_definition": "def",
    "function_declaration": "def",
    "generator_function_declaration": "def",
    "function_item": "fn",
    "function_signature_item": "fn",
    "method_definition": "method",
    "method_declaration": "method",
    "method": "method",
    "singleton_method": "method",
    "variable_declarator": "def",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "type_spec": "type",
    "enum_declaration": "enum",
    "enum_item": "enum",
    "enum_specifier": "enum",
    "trait_item": "trait",
    "trait_declaration": "trait",
    "protocol_declaration": "protocol",
    "mod_item": "mod",
    "module": "module",
    "namespace_definition": "namespace",
    "impl_item": "impl",
    "object_declaration": "object",
    "macro_definition": "macro",
}

#: Past this, a file is not something an outline helps with — it is generated, minified, or a
#: data blob that happens to have a code extension. Parsing one costs real time and returns
#: thousands of symbols nobody wanted.
MAX_BYTES = 2_000_000

#: A declaration line longer than this is wrapped source, a giant type signature, or minified
#: code. Enough to recognise the thing; not enough for one symbol to cost what the file would.
_SIGNATURE_CHARS = 160


class OutlineError(Exception):
    """Raised when a file cannot be outlined, with a reason worth reading."""


@dataclass(frozen=True)
class Symbol:
    kind: str
    name: str
    line: int
    depth: int
    signature: str
    #: Last line of the definition, inclusive — the other end of `line`.
    #:
    #: Carried because the parser already knows it and nothing else can recover it. Guessing
    #: from the next symbol's start is wrong in both directions: it swallows whatever sits
    #: between two definitions, and it has no answer at all for the last one in a file. With
    #: both ends, `excerpt.locate` can hand `read_file` a window that is exactly one
    #: definition, which is the difference between reading a method and reading its module.
    end_line: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "line": self.line,
            "end_line": self.end_line,
            "depth": self.depth,
            "signature": self.signature,
        }


def language_for(path: str | Path) -> str | None:
    """The grammar for this file, or None if we do not read it."""
    return LANGUAGES.get(Path(str(path)).suffix.lower())


def _parser(language: str):
    """The parser for a grammar, or None when the pack cannot supply one.

    Some grammars in the pack are fetched on demand, so this can fail at runtime on a machine
    with no network even though the language is in `LANGUAGES`. That is a reason to skip the
    file, not to take down the caller.
    """
    try:
        from tree_sitter_language_pack import get_parser

        return get_parser(language)  # type: ignore[arg-type]
    except Exception:
        return None


def _named(node) -> str:
    field = node.child_by_field_name("name")
    if field is not None:
        try:
            return field.text.decode(errors="replace")
        except Exception:
            return ""
    # Rust `impl Thing` has no name field — the type it is for is the useful label.
    for field_name in ("type", "trait", "declarator", "pattern"):
        other = node.child_by_field_name(field_name)
        if other is not None:
            try:
                return other.text.decode(errors="replace").split("\n")[0][:60]
            except Exception:
                return ""
    return ""


def _kind_of(node_type: str) -> str:
    return _KIND.get(
        node_type, node_type.replace("_", " ").removesuffix(" item").removesuffix(" declaration")
    )


def _exported(node) -> bool:
    """Is this declaration part of an `export` statement?

    Two hops up in practice — `export_statement > lexical_declaration > variable_declarator` —
    with a little slack for grammars that nest differently.
    """
    parent = node.parent
    for _ in range(3):
        if parent is None:
            return False
        if parent.type in ("export_statement", "export_declaration"):
            return True
        parent = parent.parent
    return False


def _wanted(node) -> bool:
    if node.type in _WANTED:
        return True
    if node.type == "variable_declarator":
        # A `const x = () => …` is a function everywhere it matters. An exported `const` that
        # is not callable is still API — the file's whole content, in the case of a lookup
        # table — and leaving it out made the outline of a table module read as though the
        # tables were not in it. A plain local `const n = 4` remains noise.
        value = node.child_by_field_name("value")
        if value is not None and value.type in _CALLABLE_VALUES:
            return True
        return _exported(node)
    return False


def _signature(source: bytes, node) -> str:
    """The declaration itself: the node's first line, trimmed.

    Reconstructing a signature from the tree would mean knowing every grammar's parameter
    shape. The first line of the node already *is* the declaration in every language here,
    and it is the text the reader recognises.
    """
    start = node.start_byte
    end = source.find(b"\n", start)
    if end == -1 or end > node.end_byte:
        end = node.end_byte
    try:
        text = source[start:end].decode(errors="replace").strip()
    except Exception:
        return ""
    # Trailing punctuation that only opens what follows: the brace of a body, the colon of a
    # Python suite, the `=` of an assignment whose value is on the next lines.
    while text and text[-1] in "{=:":
        text = text[:-1].rstrip()
    # A declaration wrapped across lines ends mid-parameters. Say so rather than implying a
    # function of no arguments.
    if text.endswith(("(", ",")):
        text = text.rstrip("(,") + "(…)"
    return text[:_SIGNATURE_CHARS]


def of_source(source: bytes, language: str) -> list[Symbol]:
    """Symbols in already-loaded bytes. Split out so the repo map can reuse the walk."""
    parser = _parser(language)
    if parser is None:
        return []
    tree = parser.parse(source)

    found: list[Symbol] = []

    def walk(node, depth: int) -> None:
        for child in node.children:
            if _wanted(child):
                name = _named(child)
                if name:
                    found.append(
                        Symbol(
                            kind=_kind_of(child.type),
                            name=name,
                            line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                            depth=depth,
                            signature=_signature(source, child),
                        )
                    )
                    walk(child, depth + 1)
                    continue
                # Unnamed but structural (an anonymous impl block): do not list it, but do
                # look inside — its methods are the point.
                walk(child, depth)
                continue
            walk(child, depth)

    walk(tree.root_node, 0)
    return found


def source_of(path: str | Path) -> tuple[bytes, str]:
    """The bytes of a file and the grammar to read it with, or `OutlineError` saying why not.

    Split out of `of_file` so that everything parsing a file refuses it for the same reasons
    and in the same words. Each check below ends in a next step — grep it, read it another
    way, check the install — and a second copy of them would drift from this one the first
    time either was edited. `excerpt.locate` is the first other caller; it will not be the
    last, which is what makes this worth a function rather than a duplication.
    """
    target = Path(str(path))
    language = language_for(target)
    if language is None:
        raise OutlineError(
            f"I can't read the shape of {target.suffix or 'a file with no extension'} files — "
            "read it with read_file, or grep it."
        )
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise OutlineError(f"cannot open {path}: {exc}") from None
    if size > MAX_BYTES:
        raise OutlineError(
            f"{path} is {size:,} bytes — too big to outline, and almost certainly generated. "
            "grep it for what you want instead."
        )
    try:
        source = target.read_bytes()
    except OSError as exc:
        raise OutlineError(f"cannot read {path}: {exc}") from None
    if b"\x00" in source[:8192]:
        raise OutlineError(f"{path} looks like a binary file, not source.")

    if _parser(language) is None:
        # Distinguished from an empty outline on purpose. `of_source` returns `[]` when it
        # cannot parse, and rendered that reads as "this file has no definitions in it" —
        # a confident wrong answer about somebody's code, produced by a missing dependency.
        raise OutlineError(
            f"I can't parse {language} here — the tree-sitter grammar for it is not available "
            "(check the install, or the network if it fetches on demand). grep still works."
        )

    return source, language


def of_file(path: str | Path) -> dict[str, Any]:
    """The outline of one file on disk.

    Raises `OutlineError` with something actionable rather than returning an empty outline,
    which would read as "this file has no functions in it".
    """
    source, language = source_of(path)
    symbols = of_source(source, language)
    return {
        "path": str(path),
        "language": language,
        "symbols": [one.as_dict() for one in symbols],
        "lines": source.count(b"\n") + 1,
    }


def render(outline: dict[str, Any]) -> str:
    """The outline as something to read, which is how it earns its keep.

    Line number first and right-aligned, so the column scans and so the number he needs for
    `read_file`'s offset is the first thing on the row.
    """
    symbols = outline.get("symbols") or []
    if not symbols:
        return f"{outline['path']} — {outline['language']}, no definitions found ({outline.get('lines', 0)} lines)"
    width = max(len(str(one["line"])) for one in symbols)
    rows = [
        f"{str(one['line']).rjust(width)}  {'  ' * one['depth']}{one['signature'] or one['name']}"
        for one in symbols
    ]
    head = (
        f"{outline['path']} — {outline['language']}, "
        f"{len(symbols)} definition{'' if len(symbols) == 1 else 's'} "
        f"in {outline.get('lines', 0)} lines"
    )
    return head + "\n" + "\n".join(rows)
