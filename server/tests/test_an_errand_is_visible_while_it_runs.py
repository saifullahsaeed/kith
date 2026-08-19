"""A sub-agent's work is discarded on purpose, which is exactly why it has to be shown live.

Everything else in a turn appears twice: the thread renders every tool call, and the Work panel
used to render the same list four hundred pixels away — which is why that list was deleted from
the panel. An errand is the one case where the panel is not a second copy. The worker's greps
and reads never enter the conversation and are thrown away when it finishes, so if the panel
stays quiet during one, nobody ever sees the work at all.

And from the outside, a scout thinking for forty seconds and the interface having hung look
identical. Three of them at once look worse.

So the worker publishes as it goes. Into the live feed, not the transcript: nothing here is
stored, re-sent to a model or billed, and the tool contract is unchanged — a handler still
returns exactly one value.
"""

from __future__ import annotations

from pathlib import Path

from kith.services import activity, agent_loop
from kith.tools import delegation


def _published(monkeypatch) -> list[dict]:
    """Every feed line, captured instead of pushed."""
    seen: list[dict] = []

    def spy(kind, text, *, tokens=None, tool=None, args=None, errand=None, conversation=""):
        seen.append(
            {
                "kind": kind,
                "text": text,
                "tool": tool,
                "args": args,
                "errand": errand,
                "conversation": conversation,
            }
        )

    monkeypatch.setattr(activity.feed, "publish", spy)
    return seen


def _rounds(*script):
    calls: list[int] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        calls.append(1)
        text, tool_calls = script[min(len(calls) - 1, len(script) - 1)]
        if text:
            yield {"type": "delta", "role": "text", "text": text}
        yield {"type": "turn", "content": text, "tool_calls": list(tool_calls), "stats": {}}

    return fake


def _a_call(name: str, **arguments):
    import json

    return {"function": {"name": name, "arguments": json.dumps(arguments)}}


class TestAnErrandNarratesItself:
    def test_it_opens_steps_and_closes(self, db: Path, monkeypatch):
        seen = _published(monkeypatch)
        monkeypatch.setattr(
            agent_loop,
            "_stream_once",
            _rounds(("", [_a_call("list_projects")]), ("Nothing filed.", [])),
        )

        delegation.delegate_subtask(db, {"objective": "is anything filed about ledgers"})

        states = [line["errand"]["state"] for line in seen if line["errand"]]
        assert states == ["running", "step", "done"]

    def test_every_line_carries_the_same_errand_id(self, db: Path, monkeypatch):
        """Three scouts sent in one round interleave in the feed, and the id is the only thing
        that keeps them from rendering as one confused list."""
        seen = _published(monkeypatch)
        monkeypatch.setattr(
            agent_loop,
            "_stream_once",
            _rounds(("", [_a_call("list_projects")]), ("done", [])),
        )

        delegation.delegate_subtask(db, {"objective": "look at something"})

        ids = {line["errand"]["id"] for line in seen if line["errand"]}
        assert len(ids) == 1
        assert all(line["errand"]["objective"] == "look at something" for line in seen if line["errand"])

    def test_a_step_carries_the_tool_and_its_arguments_not_a_sentence(self, db: Path, monkeypatch):
        """The interface holds the only table of phrases there is. A second one here would be a
        second thing to keep in step with the registry, and `test_mind_feed` guards one of them."""
        seen = _published(monkeypatch)
        monkeypatch.setattr(
            agent_loop,
            "_stream_once",
            _rounds(("", [_a_call("list_projects", limit=3)]), ("done", [])),
        )

        delegation.delegate_subtask(db, {"objective": "look at something"})

        step = next(line for line in seen if line["errand"] and line["errand"]["state"] == "step")
        assert step["tool"] == "list_projects"
        assert step["args"] == {"limit": "3"}

    def test_the_closing_line_carries_the_cost_and_not_the_findings(self, db: Path, monkeypatch):
        """The report is already going into the conversation, rendered properly. A second copy
        squeezed into a 400px column is a paragraph in a gutter; what the panel can say that the
        thread cannot is how much looking the paragraph stands on."""
        seen = _published(monkeypatch)
        monkeypatch.setattr(
            agent_loop,
            "_stream_once",
            _rounds(
                ("", [_a_call("list_projects"), _a_call("list_projects")]),
                ("A long and detailed finding about ledgers.", []),
            ),
        )

        delegation.delegate_subtask(db, {"objective": "look at something"})

        closing = next(line for line in seen if line["errand"] and line["errand"]["state"] == "done")
        assert closing["text"] == "list_projects x2"
        assert "ledgers" not in closing["text"]

    def test_a_feed_that_is_broken_does_not_take_the_errand_down(self, db: Path, monkeypatch):
        """Bookkeeping about the call must never fail the call. The same rule `touched.record`
        follows, for the same reason: the worker's answer is already the answer."""

        def explode(*a, **k):
            raise RuntimeError("the feed is on fire")

        monkeypatch.setattr(activity.feed, "publish", explode)
        monkeypatch.setattr(agent_loop, "_stream_once", _rounds(("Found it.", [])))

        answer = delegation.delegate_subtask(db, {"objective": "look at something"})

        assert answer["findings"] == "Found it."
