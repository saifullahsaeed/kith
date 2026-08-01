"""Every tool he can call reads as English in the Work feed.

Seventeen of the fifty-five did not. Those fell through to the raw `name(arg=value)` string,
so the feed was half plain prose and half source code, and which you got depended on which
tool he happened to reach for. The thirty-eight that *were* mapped had the opposite problem:
the name was recovered by splitting the text on "(" and discarding everything after it, so an
afternoon of real work read as "read a file / wrote a file / ran a command" with the only
interesting part — which file, which command — thrown away on arrival.

This test reaches across into the interface, which is unusual and deliberate. The two lists
have to agree and nothing else checks that they do: adding a tool is a one-line change in
Python, and the cost of forgetting the matching phrase is invisible until someone watches him
work and sees a function call. Better a failing test than a feed that degrades a little every
time he gains a capability.
"""

from __future__ import annotations

import re

from kith import settings
from kith.tools import registry

# The tables moved out of tool-language.ts into a module both surfaces import: the chat thread
# describes the same calls, and while the phrases lived inside the panel the thread rendered
# them as "1 tool call" and "Used tool: read_skill". One vocabulary, one file to guard.
PANEL = settings.SERVER_ROOT.parent / "ui" / "src" / "lib" / "tool-language.ts"


def tool_table() -> str:
    """The body of the TOOL table in tool-language.ts.

    Anchored on `const TOOL` and the first `};` rather than on the full type annotation. The
    first version matched the whole signature and broke the moment a field was added to it —
    a test that fails because the code it guards was improved is worse than no test.
    """
    source = PANEL.read_text()
    assert "const TOOL" in source, "the TOOL table has been renamed; update this test"
    return source.split("const TOOL", 1)[1].split("\n};", 1)[0]


def phrased_tools() -> set[str]:
    """The tool names the Work panel has a phrase for."""
    return set(re.findall(r"^\s{2}([a-z_]+):\s*\{", tool_table(), re.M))


def test_every_tool_reads_as_english() -> None:
    registered = {schema["function"]["name"] for schema in registry.schemas()}
    missing = sorted(registered - phrased_tools())
    assert not missing, (
        "these tools have no phrase in tool-language.ts and will print as raw "
        f"name(arg=value) in the live feed: {missing}"
    )


def test_the_table_has_no_entries_for_tools_that_do_not_exist() -> None:
    registered = {schema["function"]["name"] for schema in registry.schemas()}
    # A stale entry is harmless at runtime but it is a lie about what he can do, and it hides
    # a rename: the old name keeps its phrase while the new one silently has none.
    stale = sorted(phrased_tools() - registered)
    assert not stale, f"tool-language.ts describes tools that no longer exist: {stale}"


def test_the_subject_of_each_verb_is_a_real_argument() -> None:
    """`of: "path"` has to name an argument the tool actually takes.

    Otherwise the line renders as a bare verb forever and nobody notices, because a missing
    subject looks exactly like a tool that never had one.
    """
    pairs = re.findall(r"^\s{2}([a-z_]+):\s*\{[^}]*\bof:\s*\"([a-z_]+)\"", tool_table(), re.M)

    schemas = {
        schema["function"]["name"]: set((schema["function"].get("parameters") or {}).get("properties", {}))
        for schema in registry.schemas()
    }
    wrong = [(tool, subject) for tool, subject in pairs if tool in schemas and subject not in schemas[tool]]
    assert not wrong, f"these name an argument the tool does not take: {wrong}"


def test_the_feed_carries_the_arguments_not_just_a_sentence() -> None:
    """The panel needs data, not prose to parse back apart.

    A value containing ", " breaks any split of the rendered sentence, which is why the name
    and arguments travel as their own fields.
    """
    from kith.autonomy.prompts import _short_args

    args = _short_args({"command": "npm run build && echo done, finally", "cwd": "."})
    assert args["command"].startswith("npm run build")
    # Whole, including the comma that would have broken a parser.
    assert "done, finally" in args["command"]


def test_long_values_are_trimmed_before_they_travel() -> None:
    from kith.autonomy.prompts import _ARG_CHARS, _short_args

    args = _short_args({"content": "x" * 5_000})
    # A file's entire contents must not go down the event stream to be cut off by CSS at the
    # far end; that is a megabyte of traffic to render forty characters.
    assert len(args["content"]) <= _ARG_CHARS
    assert args["content"].endswith("…")


def test_newlines_are_flattened() -> None:
    from kith.autonomy.prompts import _short_args

    args = _short_args({"command": "line one\nline two"})
    # Feed rows are one line high. A heredoc arriving with its newlines intact either breaks
    # the row or gets silently clipped mid-sentence.
    assert "\n" not in args["command"]
    assert args["command"] == "line one line two"


def test_every_tool_has_an_icon_group() -> None:
    """A phrase without a group falls back to a generic wrench.

    The icons are there so a column can be read at a glance — six terminals and one globe
    says "he built for a while and looked one thing up" without reading a word. A row that
    quietly defaults undoes that for the tool it happens to be, and nothing on screen says so.
    """
    entries = re.findall(r"^\s{2}([a-z_]+):\s*\{([^}]*)\},$", tool_table(), re.M)
    ungrouped = sorted(name for name, body in entries if "group:" not in body)
    assert not ungrouped, f"these would render with the fallback icon: {ungrouped}"


def test_every_group_named_by_a_tool_exists() -> None:
    source = PANEL.read_text()
    declared = set(re.findall(r"^  ([a-z]+): \{ icon:", source.split("const GROUP", 1)[1], re.M))
    used = set(re.findall(r'group: "([a-z]+)"', tool_table()))
    # A typo here is a silent fallback, not an error — TypeScript checks the key against
    # `typeof GROUP`, but only while the table stays literal.
    assert used <= declared, f"groups used but not declared: {sorted(used - declared)}"
