"""A call the output ceiling cut off is answered as cut off, not run with nothing in it.

Off a real conversation — 2026-10-01, `20261001-165111177-a0a492.jsonl`. Twice in a row, Sol wrote
a `write_file` of a generated screen script until the round hit its 16,384-token ceiling.
OpenRouter reported `finish_reason: tool_calls`, `native_finish_reason: max_output_tokens`, and
the arguments arrived as half a JSON object. The loop read them as `{}`, ran `write_file({})`,
and handed back `KeyError: 'path'` — about seven minutes and two full rewrites, with nothing to
say the content was there and the ceiling had eaten it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from kith import tools
from kith.config import default_config
from kith.services import agent_loop


def _cut_write() -> dict:
    """Half a call: the path is in it, the content never closed."""
    half = '{"path": "screens.py", "content": "S=[]\\ndef add(a, slug, title'
    return {
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "write_file", "arguments": half}}],
        "stats": {"finishReason": "tool_calls", "nativeFinishReason": "max_output_tokens"},
    }


def _spoke(text: str = "done") -> dict:
    return {"content": text, "tool_calls": [], "stats": {"finishReason": "stop"}}


def _script(*responses: dict):
    sent: list[dict] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        sent.append({"convo": [dict(m) for m in convo]})
        yield dict(responses[min(len(sent) - 1, len(responses) - 1)], type="turn")

    return fake, sent


def _run(fake, monkeypatch, db: Path, run_tool=None) -> list[dict]:
    monkeypatch.setattr(agent_loop, "_stream_once", fake)
    monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)
    host = tools.host(db, language_server=False)
    if run_tool is not None:
        host = replace(host, run=run_tool)
    return list(
        agent_loop._run_turn(
            [{"role": "user", "content": "write the screens"}],
            default_config(),
            "host",
            db,
            host,
            max_rounds=4,
        )
    )


def _results(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "tool_result"]


class TestACutOffCall:
    def test_it_is_not_run(self, db: Path, monkeypatch):
        ran: list[tuple] = []
        fake, _sent = _script(_cut_write(), _spoke())
        _run(fake, monkeypatch, db, run_tool=lambda *a, **k: ran.append(a) or {"ok": True})

        assert ran == [], "a call with half its arguments must not reach the tool"

    def test_he_is_told_it_was_the_ceiling(self, db: Path, monkeypatch):
        fake, _sent = _script(_cut_write(), _spoke())
        result = _results(_run(fake, monkeypatch, db))[0]["result"]

        error = json.dumps(result)
        assert "cut off" in error and "output ceiling" in error
        assert "KeyError" not in error

    def test_it_names_the_way_out(self, db: Path, monkeypatch):
        """Sent again whole, it is cut off at the same place — so the answer says to make it smaller."""
        fake, _sent = _script(_cut_write(), _spoke())
        error = json.dumps(_results(_run(fake, monkeypatch, db))[0]["result"])

        assert "in parts" in error


class TestWhatIsNotAffected:
    def test_a_whole_call_in_a_round_that_ran_out_still_runs(self, db: Path, monkeypatch):
        """Only the arguments decide. A call that closed before the ceiling did is complete."""
        ran: list[tuple] = []
        whole = {
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": '{"paths": ["x"]}'}}],
            "stats": {"finishReason": "length"},
        }
        fake, _sent = _script(whole, _spoke())
        _run(fake, monkeypatch, db, run_tool=lambda *a, **k: ran.append(a) or {"ok": True})

        assert len(ran) == 1

    def test_arguments_that_are_not_json_say_so_without_blaming_the_ceiling(self, db: Path, monkeypatch):
        broken = {
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{paths: x"}}],
            "stats": {"finishReason": "tool_calls", "nativeFinishReason": "completed"},
        }
        fake, _sent = _script(broken, _spoke())
        error = json.dumps(_results(_run(fake, monkeypatch, db))[0]["result"])

        assert "not valid JSON" in error and "ceiling" not in error

    def test_an_empty_last_call_counts_only_when_the_round_ran_out(self):
        assert agent_loop._unfinished("", last=True, ran_out=True)
        assert not agent_loop._unfinished("", last=True, ran_out=False)
        assert not agent_loop._unfinished("", last=False, ran_out=True)
        assert not agent_loop._unfinished('{"a": 1}', last=True, ran_out=True)
