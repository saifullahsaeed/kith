"""The animation he wrote was refused, and only their screen knows.

A flow script that routes through a pair with no arrow between them, or names a node the diagram
does not have, loses its animation: the picture is drawn, and one line under it says why. On
their screen. He cannot see their screen — so without this the same broken route comes back in
the next reply, and the one after that, and the only way it is ever fixed is the person typing
the error out by hand.

The validator's own words go back unedited, because they are already the fix: the mistake, the
vocabulary, and the correction in one line. What is checked here is that they arrive, that they
arrive *narrowed* — this is a field off the wire, from a request anything local can make — and
that they survive the step that rebuilds a message from role and content alone, which is exactly
where the canvas note was silently composed away.
"""

from __future__ import annotations

from kith.services.turn import prompt
from kith.services.turn.prompt import _with_diagrams

NO_EDGE = 'flow: no edge between "CompA" and "Modules"'
UNKNOWN = 'flow: unknown node "UserTypesEmail" in a route — nodes in this diagram: Core, Gateway'


def test_the_refusal_arrives_under_what_they_said():
    message = _with_diagrams(
        {"role": "user", "content": "and the second one?", "diagrams": [NO_EDGE]}
    )
    assert message["content"].startswith("and the second one?")
    assert "did not run" in message["content"]
    assert NO_EDGE in message["content"]
    # The field itself never goes to the provider; the note does.
    assert "diagrams" not in message


def test_a_message_with_no_broken_diagram_is_untouched():
    message = {"role": "user", "content": "morning"}
    assert _with_diagrams(dict(message)) == message


def test_an_empty_list_adds_nothing():
    assert _with_diagrams({"role": "user", "content": "hi", "diagrams": []}) == {
        "role": "user",
        "content": "hi",
    }


def test_every_one_of_them_is_named():
    message = _with_diagrams(
        {"role": "user", "content": "fix these", "diagrams": [NO_EDGE, UNKNOWN]}
    )
    assert NO_EDGE in message["content"]
    assert UNKNOWN in message["content"]


def test_a_refusal_cannot_become_a_paragraph():
    """It arrives over HTTP from a page anything local can post to."""
    message = _with_diagrams(
        {"role": "user", "content": "hi", "diagrams": ["x" * 5_000, "", "   ", 7, None]}
    )
    note = message["content"]
    assert len(note) < 500
    # The two that are not strings and the two that are blank are gone, not rendered as "None".
    assert "None" not in note
    assert note.count("- ") == 1


def test_only_so_many_are_carried():
    message = _with_diagrams(
        {"role": "user", "content": "hi", "diagrams": [f"flow: broken {n}" for n in range(9)]}
    )
    assert message["content"].count("- flow: broken") == 4


def test_the_note_survives_the_walk_to_the_provider(monkeypatch):
    """The unit tests above all pass with the feature entirely disconnected.

    Which is not hypothetical: the canvas note was composed *inside* the step that rebuilds a
    message from role and content alone, so it was stripped before it ever ran — on every
    message, with nothing failing. This asks the question the model asks: what arrived.
    """
    monkeypatch.setattr(prompt, "_present_state", lambda conversation_id="": "")
    out = prompt._assemble(
        [], [{"role": "user", "content": "and the second one?", "diagrams": [NO_EDGE]}], ""
    )
    assert len(out) == 1
    assert NO_EDGE in out[0]["content"]
    assert set(out[0]) == {"role", "content"}


def test_a_refusal_and_a_canvas_reading_both_arrive(monkeypatch):
    """Two notes, written by two steps, one of which narrows the message."""
    monkeypatch.setattr(prompt, "_present_state", lambda conversation_id="": "")
    out = prompt._assemble(
        [],
        [
            {
                "role": "user",
                "content": "so?",
                "canvas": [{"title": "tuner", "values": {"foldAt": 5}}],
                "diagrams": [NO_EDGE],
            }
        ],
        "",
    )
    body = out[0]["content"]
    assert "- foldAt: 5" in body
    assert NO_EDGE in body
    assert set(out[0]) == {"role", "content"}
