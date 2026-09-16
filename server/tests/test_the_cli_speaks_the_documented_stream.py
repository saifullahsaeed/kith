"""Every event `/api/chat` says it sends, the CLI knows what to do with.

There is no compiler on this seam. The route documents its stream in an OpenAPI description —
that description is the contract another agent reads — and the renderer handles those types in
an if/elif chain that type-checks perfectly whether or not the two agree. A type added to the
server and not here is a turn that silently drops part of itself on the floor; a type removed
there and left here is dead code nobody notices for a year.

So the documented types are read out of the route and compared against what the renderer
branches on, the same way `test_the_client_types_are_not_lying.py` reads a TypeScript
declaration and compares it against the service that fills it.

The second half of the file is about the property that makes the whole CLI composable: prose
goes to stdout and everything else goes to stderr. It is worth a test rather than a comment
because the failure is invisible until somebody pipes the output somewhere and pastes the
result — by which time the reasoning is in the document.
"""

from __future__ import annotations

import ast
import io
import re
from pathlib import Path

from kith.cli import render

ROUTE = Path(__file__).resolve().parent.parent / "kith" / "api" / "routes" / "chat.py"
RENDER = Path(__file__).resolve().parent.parent / "kith" / "cli" / "render.py"


def _documented_types() -> set[str]:
    """The event names out of the `/api/chat` doc description."""
    source = ROUTE.read_text()
    start = source.index('@api.post("/chat")')
    end = source.index("def chat(payload):")
    return set(re.findall(r'\{"type":"(\w+)"', source[start:end]))


def _handled_types() -> set[str]:
    """What `render.human` branches on — every literal compared against `kind`.

    Both comparison shapes count: `kind == "delta"` and `kind in ("compacting", ...)`, which
    is why the tuple is walked rather than only the constants.
    """
    tree = ast.parse(RENDER.read_text())
    human = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "human"
    )
    handled: set[str] = set()
    for node in ast.walk(human):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "kind":
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    handled.add(comparator.value)
                elif isinstance(comparator, ast.Tuple):
                    handled.update(
                        element.value
                        for element in comparator.elts
                        if isinstance(element, ast.Constant) and isinstance(element.value, str)
                    )
    return handled


SERVER = Path(__file__).resolve().parent.parent / "kith"

#: Everywhere a stream event is constructed. The transports are in here because `writing` is
#: born in one of them — found by this test documenting it and then failing to find anything
#: that sent it, which is exactly the drift it is for.
EMITTERS = (
    SERVER / "services" / "agent_loop.py",
    SERVER / "api" / "routes" / "chat.py",
    SERVER / "llm" / "openai_compat.py",
    SERVER / "llm" / "ollama.py",
)

#: Emitted into the stream but never documented and never rendered, with the reason.
#:
#: `image_url` and `text` are *message content parts* — the shape a multimodal message is
#: built in before it is sent to the provider — not stream events. They share the `type` key
#: and nothing else.
#:
#: `turn` is the transports' own internal envelope — `{"type": "turn", "content", "tool_calls",
#: "stats"}` — which the loop unpacks into the events above. It never reaches a client.
#:
#: `function` is the OpenAI tool-call wire schema — `{"type": "function", "function": {...}}` —
#: which the transport builds to send *to* the provider. It travels the opposite direction
#: from everything else in this file.
NOT_EVENTS = {"image_url", "text", "turn", "function"}


def _emitted_types() -> set[str]:
    """Every `{"type": "..."}` the agent loop and the chat route construct.

    This is the half of the seam that was missing, and the reason it mattered: the route
    documented seven types while the loop yielded fifteen. The documented ones were the ones
    inside a turn; the eight undocumented ones were all the ones that explain why a turn has
    gone quiet — a fold, a retry, a pause for sub-agents. A client written against the docs
    was correct by the docs and wrong in practice, and nothing compared the two.
    """
    found: set[str] = set()
    for source in EMITTERS:
        for node in ast.walk(ast.parse(source.read_text())):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "type"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    found.add(value.value)
    return found - NOT_EVENTS


#: Documented, and deliberately ignored, with the reason.
#:
#: `writing` is a throttled progress ping for a tool call still being composed — the route's own
#: description says it is live only and never recorded. Rendering it would print a line that is
#: immediately superseded by the `tool_call` it precedes.
IGNORED = {"writing"}


def test_every_documented_event_is_handled_or_deliberately_ignored():
    documented = _documented_types()
    assert documented, "the route's stream documentation moved — this test is reading nothing"
    unhandled = documented - _handled_types() - IGNORED
    assert not unhandled, (
        f"/api/chat documents {sorted(unhandled)} and the CLI renderer ignores them — "
        "handle them in render.human, or add them to IGNORED with a reason"
    )


def test_the_renderer_does_not_invent_events_the_server_never_sends():
    """The other direction: a branch for a type nothing emits is dead code that reads as live."""
    # `conversation` is sent by the route body rather than named in its description — it is
    # yielded before the turn starts so a chat opened without an id can learn its own.
    invented = _handled_types() - _documented_types() - {"conversation"}
    assert not invented, f"the renderer handles {sorted(invented)}, which nothing sends"


# --------------------------------------------------------------------------- #


def _stream(*events):
    import json

    for event in events:
        yield json.dumps(event) + "\n", event


def test_prose_goes_to_stdout_and_commentary_goes_to_stderr():
    out, err = io.StringIO(), io.StringIO()
    code, conversation = render.human(
        _stream(
            {"type": "conversation", "id": "20260916-1-aaa"},
            {"type": "delta", "role": "reasoning", "text": "let me check the auth flow"},
            {"type": "tool_call", "id": "1", "name": "read_file", "arguments": {"path": "auth.py"}},
            {"type": "delta", "role": "text", "text": "The token is a 0600 file."},
            {"type": "stats", "stats": {"promptTokens": 100, "responseTokens": 20}},
            {"type": "done"},
        ),
        out=out,
        err=err,
    )

    assert code == 0
    assert conversation == "20260916-1-aaa"
    # Exactly the answer, and nothing else. This is what `$(kith send ...)` captures.
    assert out.getvalue() == "The token is a 0600 file.\n"
    commentary = err.getvalue()
    assert "let me check the auth flow" in commentary
    assert "read_file" in commentary
    assert "20260916-1-aaa" in commentary


def test_an_errored_turn_exits_non_zero_without_losing_what_was_said():
    out, err = io.StringIO(), io.StringIO()
    code, _ = render.human(
        _stream(
            {"type": "delta", "role": "text", "text": "I got as far as"},
            {"type": "error", "message": "the provider hung up"},
            {"type": "done"},
        ),
        out=out,
        err=err,
    )
    assert code == 1
    assert "I got as far as" in out.getvalue()
    assert "the provider hung up" in err.getvalue()


def test_quiet_keeps_the_answer_and_drops_everything_else():
    out, err = io.StringIO(), io.StringIO()
    render.human(
        _stream(
            {"type": "delta", "role": "reasoning", "text": "thinking"},
            {"type": "tool_call", "id": "1", "name": "read_file", "arguments": {}},
            {"type": "delta", "role": "text", "text": "done."},
            {"type": "done"},
        ),
        out=out,
        err=err,
        quiet=True,
    )
    assert out.getvalue() == "done.\n"
    assert err.getvalue() == ""


def test_json_mode_writes_the_servers_own_bytes():
    """A passthrough, not a re-serialisation — the promise `--json` makes to another agent."""
    raw = [
        '{"type":"conversation","id":"c1"}\n',
        '{"type":"delta","role":"text","text":"hi"}\n',
        '{"type":"done"}\n',
    ]
    import json

    out = io.StringIO()
    code, conversation = render.passthrough(((line, json.loads(line)) for line in raw), out=out)
    assert code == 0
    assert conversation == "c1"
    assert out.getvalue() == "".join(raw)


def test_every_event_the_loop_emits_is_documented():
    """The direction that was missing, and the one that actually broke.

    A type the server sends and the description omits is invisible to everything written
    against the description — which, for a public API with an OpenAPI spec, is everything
    that is not the one client written alongside it.
    """
    undocumented = _emitted_types() - _documented_types()
    assert not undocumented, (
        f"the loop streams {sorted(undocumented)} and /api/chat does not document them — "
        "add them to the route's description, or stop sending them"
    )


def test_every_documented_event_is_one_the_loop_actually_sends():
    """And the reverse: documentation for an event nothing emits is a client written to
    handle something that will never arrive."""
    phantom = _documented_types() - _emitted_types()
    assert not phantom, f"/api/chat documents {sorted(phantom)}, which nothing sends"


def test_a_stream_that_stops_mid_turn_is_not_reported_as_success():
    """A killed turn and a finished turn must not have the same exit code.

    Found in use, not in review. The agent was editing his own source while a turn streamed;
    the dev server's reloader restarted under the open response and the turn stopped mid-word.
    The renderer fell out of its loop and returned 0, so a half-written answer was
    indistinguishable from a complete one — which for a command another agent reads the exit
    code of is the worst shape a failure can take.
    """
    out, err = io.StringIO(), io.StringIO()
    code, _ = render.human(
        _stream(
            {"type": "delta", "role": "text", "text": "I got as far as"},
            # and then nothing. No `done`.
        ),
        out=out,
        err=err,
    )
    assert code == 1
    assert "I got as far as" in out.getvalue(), "what did arrive is still the answer"
    assert "incomplete" in err.getvalue()


def test_json_mode_also_fails_on_a_truncated_stream():
    """It matters more here: the caller is a program, every line it received is valid JSON
    either way, and the exit code is the only thing that can tell it the turn was cut off."""
    import json as _json

    raw = ['{"type":"delta","role":"text","text":"half an ans"}\n']
    out = io.StringIO()
    code, _ = render.passthrough(((line, _json.loads(line)) for line in raw), out=out)
    assert code == 1
    assert out.getvalue() == "".join(raw), "the bytes are still passed through unchanged"


def test_a_finished_turn_still_exits_zero():
    out, err = io.StringIO(), io.StringIO()
    code, _ = render.human(
        _stream({"type": "delta", "role": "text", "text": "done."}, {"type": "done"}),
        out=out,
        err=err,
    )
    assert code == 0
    assert "incomplete" not in err.getvalue()
