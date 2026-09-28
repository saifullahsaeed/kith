"""An agent asked for a shape hands it back through a checked `submit` call, not a paragraph.

It used to be asked, in prose, to end its report with a JSON value, and the report was then
scanned for the last thing that looked like one. The shape of the tools decides what a model
does far more reliably than an instruction does, so the shape is now a tool: its parameters are
the schema, a wrong call is refused with what is wrong, and a right one ends the agent.
"""

from __future__ import annotations

import json
from pathlib import Path

from kith.services import agent_loop
from kith.tools import submission, workers


class TestTheShapeBecomesParameters:
    def test_an_example_shape_becomes_json_schema(self):
        assert submission.to_json_schema({"files": ["str"], "count": "int", "done": "bool"}) == {
            "type": "object",
            "properties": {
                "files": {"type": "array", "items": {"type": "string"}},
                "count": {"type": "integer"},
                "done": {"type": "boolean"},
            },
            "required": ["files", "count", "done"],
        }

    def test_json_schema_passes_through(self):
        given = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
        assert submission.to_json_schema(given) is given

    def test_a_list_is_wrapped_because_parameters_are_an_object(self):
        params, wrapped = submission.parameters(["str"])
        assert wrapped and params["properties"]["value"] == {"type": "array", "items": {"type": "string"}}

    def test_what_is_wrong_is_said(self):
        schema = submission.to_json_schema({"files": ["str"], "count": "int"})
        wrong = submission.problems({"files": "a.py"}, schema)
        assert any("`files` should be array" in one for one in wrong)
        assert any("missing `count`" in one for one in wrong)
        assert submission.problems({"files": ["a.py"], "count": 2}, schema) == []


def _submits(calls: list[dict], seen: list[set[str]]):
    """A model that calls `submit` with each of ``calls`` in turn, one per round."""

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        seen.append({s["function"]["name"] for s in (tools or [])})
        arguments = calls[min(len(seen), len(calls)) - 1]
        call = {"function": {"name": "submit", "arguments": json.dumps(arguments)}}
        yield {"type": "turn", "content": "", "tool_calls": [call], "stats": {}}

    return fake


class TestAnAgentEndsOnAValidSubmit:
    def test_a_wrong_call_is_refused_and_the_right_one_ends_it(self, db: Path, monkeypatch):
        seen: list[set[str]] = []
        monkeypatch.setattr(
            agent_loop, "_stream_once", _submits([{"files": "a.py"}, {"files": ["a.py"]}], seen)
        )
        answer, _report = workers.run_agent(
            db, "scout", "find the files", "", context="", schema={"files": ["str"]}
        )
        assert answer["submitted"] == {"files": ["a.py"]}
        assert len(seen) == 2, "one refused call, one accepted, and then it stopped"
        assert "submit" in seen[0]

    def test_a_list_comes_back_as_the_list(self, db: Path, monkeypatch):
        seen: list[set[str]] = []
        monkeypatch.setattr(agent_loop, "_stream_once", _submits([{"value": ["x", "y"]}], seen))
        answer, _report = workers.run_agent(db, "scout", "list them", "", context="", schema=["str"])
        assert answer["submitted"] == ["x", "y"]

    def test_without_a_schema_there_is_no_submit(self, db: Path, monkeypatch):
        seen: list[set[str]] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            seen.append({s["function"]["name"] for s in (tools or [])})
            yield {"type": "delta", "role": "text", "text": "done"}
            yield {"type": "turn", "content": "done", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        workers.run_agent(db, "scout", "look", "", context="")
        assert "submit" not in seen[0]
