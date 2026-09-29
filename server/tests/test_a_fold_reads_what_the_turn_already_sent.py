"""A fold is the conversation's own prompt with one more message on the end, not a transcript.

Measured Sep 13-27 on deepseek-v4.1-flash, a million-token window: of ten folds, five came back
as whole briefs, three stopped at the 4,000-token output cap without their last section — one
ending "…deliberately deferred.\\n-" — and two came back as the next chat reply. Every one was a
single request no host had seen before: up to ~520k tokens, all billed uncached, the history
rendered as `--- user:` / `--- assistant:` with the instruction in a system message at the far
end of it, sent with no session id down the routing path for requests that belong to no
conversation. A model handed a long dialogue carries it on. And what each fold cost was thrown
away, so none of it reached the meter, the session budget or the transcript.

So a fold now sends what the turn would have sent — cut where the fold ends, on the same host,
with the same tools — and asks last. What comes back is taken only if it is a brief, and what it
cost is counted either way.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from kith import settings
from kith.domain.chat import Config
from kith.llm import ledger
from kith.services import agent_loop, brief, conversations, history
from kith.services.activity import feed

#: A small window, so a few short turns reach the fold point: 80% of it at the seed ratio is
#: about 10,500 characters of history — a little under the twelve messages `_turns` makes.
_WINDOW = 3_550

_SCHEMAS = [{"type": "function", "function": {"name": "read_file", "description": "reads a file"}}]


def _cfg(**overrides) -> Config:
    base = Config(model="m", num_ctx=0, num_predict=0, system="", think=False, context_window=_WINDOW)
    return replace(base, **overrides)


def _turns(n: int, size: int = 1000) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": chr(65 + i % 26) * size} for i in range(n)
    ]


def _a_brief(said: str) -> str:
    return "\n\n".join(f"## {section}\n{said}" for section in brief.SECTIONS)


def _frame(part: list[dict]) -> list[dict]:
    """What `turn.prompt` hands the fold: the persona in front of the history."""
    return [{"role": "system", "content": "You are Kith."}, *part]


def _stream(text: str = "", *, finish: str = "stop", calls: list | None = None, stats: dict | None = None):
    """What a transport yields for one reply."""
    usage = {"finishReason": finish, "promptTokens": 1_000, "cachedTokens": 900, "costUsd": 0.01}
    events: list[dict[str, Any]] = [{"type": "delta", "role": "text", "text": text}] if text else []
    events.append({"type": "turn", "content": text, "tool_calls": calls or [], "stats": stats or usage})
    return iter(events)


class TestTheRequestBetweenTurns:
    def test_it_is_a_prefix_of_what_the_last_turn_sent(self, monkeypatch):
        """The property the cache needs, stated directly: everything before the instruction is
        exactly what the previous turn sent, so the host that served it can read it back."""
        before = _turns(20, 1000)
        prior = {"through": 8, "text": _a_brief("the first eight")}
        last_sent = _frame(history._as_sent(before, 8, prior["text"]))
        now = [*before, {"role": "user", "content": "and now?"}, {"role": "assistant", "content": "ok"}]
        seen: list[list[dict]] = []

        def summarise(messages, config, host, **kwargs):
            seen.append(messages)
            return brief.Reply(_a_brief("new"), None, "brief")

        monkeypatch.setattr(history, "prior_brief", lambda _conversation: prior)
        monkeypatch.setattr(history, "_summarize", summarise)
        history.fold(now, _cfg(), "", framing=_frame, schemas=_SCHEMAS)

        request = seen[0]
        assert request[:-1] == last_sent[: len(request) - 1]
        assert len(request) - 1 > len(_frame([])), "the history itself is in it, not only the head"

    def test_the_instruction_is_the_last_message_and_it_is_the_persons_slot(self):
        seen: list[list[dict]] = []
        history.compact(
            _turns(12), lambda request: seen.append(request) or "", max_chars=5_000, keep_recent=2
        )

        last = seen[0][-1]
        assert last["role"] == "user"
        assert last["content"].startswith("Stop here.")
        assert not any(m["role"] == "system" for m in seen[0]), "no instruction rides as a system message"

    def test_it_goes_to_the_conversations_own_session_with_the_turns_tools(self, monkeypatch):
        """`routing.resolved` and a blank session id were the path for requests that belong to no
        conversation. A fold belongs to one."""
        from kith.llm import openai_compat

        conversation = conversations.start(settings.AGENT_DB_PATH, "hi")
        for message in _turns(12):
            conversations.record_event(conversation["id"], "message", message)
        sent: list[dict] = []

        def cloud(messages, config, host=None, tools=None, tool_choice="auto", routing=None):
            sent.append({"config": config, "tools": tools, "tool_choice": tool_choice, "routing": routing})
            return _stream(_a_brief("done"))

        monkeypatch.setattr(openai_compat, "stream_once", cloud)
        cfg = _cfg(api_key="k", base_url="https://openrouter.ai/api/v1")
        _, made = history.fold(
            conversations.full_messages(conversation["id"]),
            cfg,
            conversation["id"],
            framing=_frame,
            schemas=_SCHEMAS,
        )

        assert made is not None
        assert sent[0]["config"].session_id == conversation["sessionId"]
        assert sent[0]["tools"] == _SCHEMAS
        assert sent[0]["tool_choice"] == "auto", "a changed tool_choice can change the rendered prompt"
        assert sent[0]["routing"] is not None
        assert sent[0]["config"].num_predict == brief.tokens(_WINDOW), "only the answer cap is its own"


class TestOnlyABriefIsTaken:
    def test_a_brief_cut_off_at_the_cap_is_refused(self):
        """Three of ten real folds. Five of six headings used to pass the shape check."""
        five = "\n\n".join(f"## {section}\nsomething" for section in brief.SECTIONS[:5]) + "\n-"
        reply = brief.read(_stream(five, finish="length"))
        assert reply.text == "" and reply.outcome == "cut off"
        assert reply.stats is not None, "it was still paid for"

    def test_a_reply_to_the_conversation_is_refused(self):
        reply = brief.read(_stream("Two things: it's finished, and I was wrong about one of my own numbers."))
        assert reply.text == "" and reply.outcome == "not a brief"

    def test_a_tool_call_is_refused(self):
        """The request offers the conversation's own tools — a different tools block would be a
        different prompt — so one can be taken. That is not a brief."""
        call = [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]
        reply = brief.read(_stream(_a_brief("x"), calls=call))
        assert reply.text == "" and reply.outcome == "called a tool"

    def test_an_error_is_refused(self):
        reply = brief.read(iter([{"type": "error", "message": "503"}]))
        assert reply.text == "" and reply.outcome.startswith("failed")

    def test_a_brief_is_taken_from_its_first_heading(self):
        reply = brief.read(_stream("Here is the brief.\n\n" + _a_brief("done")))
        assert reply.outcome == "brief"
        assert reply.text == _a_brief("done")


class TestARefusedFoldSendsWhatTheLastTurnSent:
    def test_the_brief_it_had_rather_than_the_whole_history(self):
        """A refused fold used to return the full history — every turn the brief had replaced —
        and try again on every later turn. The last turn's prompt is the safe thing to send."""
        convo = _turns(24, 1000)
        prior = {"through": 8, "text": _a_brief("the first eight")}

        kept, made = history.compact(convo, lambda _request: "", prior, max_chars=5_000, keep_recent=2)

        assert made is None
        assert kept == history._as_sent(convo, 8, prior["text"])
        assert len(kept) < len(convo)


class TestWhatAFoldCostIsCounted:
    def test_between_turns_it_is_charged_and_written_down_whatever_came_back(self, db, monkeypatch):
        conversation = conversations.start(db, "hi")["id"]
        charged: list[tuple] = []
        monkeypatch.setattr(feed, "charge_session", lambda *args: charged.append(args) or False)
        stats = {"promptTokens": 800, "cachedTokens": 700, "costUsd": 0.02, "finishReason": "length"}
        monkeypatch.setattr(history, "_summarize", lambda *a, **k: brief.Reply("", stats, "cut off"))

        history.fold(_turns(12), _cfg(), conversation)

        assert charged == [(conversation, 100, 0.02)]
        lines = [e for e in conversations.read(conversation) if e.get("type") == "stats"]
        assert len(lines) == 1
        assert lines[0]["purpose"] == "fold" and lines[0]["outcome"] == "cut off"
        assert lines[0]["stats"]["uncachedTokens"] == 100

    def test_within_a_turn_it_is_a_stats_event_like_any_model_request(self, monkeypatch):
        """Which puts it on the meter, the transcript and the session budget through the paths
        every round already takes."""
        convo, schemas, room = _a_long_turn()
        monkeypatch.setattr(agent_loop, "_stream_once", lambda *a, **k: _stream(_a_brief("notes")))

        events, (_book, folds) = _drive(_make_room(convo, schemas, room))

        kinds = [event["type"] for event in events]
        assert kinds == ["compacting", "stats"]
        assert events[1]["purpose"] == "fold" and events[1]["outcome"] == "brief"
        assert events[1]["stats"]["uncachedTokens"] == 100
        assert folds == 1


def _a_long_turn(results: int = 40, size: int = 4_000):
    """A turn well past a 20,000-token fold point, with plenty of its own to fold."""
    from kith.llm.budget import ContextBudget
    from kith.services import tuning

    tuning.apply({"fold_at_tokens": 20_000})
    convo: list[dict] = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "read everything"},
    ]
    for i in range(results):
        convo.append(
            {
                "role": "assistant",
                "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file", "arguments": "{}"}}],
            }
        )
        convo.append({"role": "tool", "tool_name": "read_file", "content": f"{i}:" + "x" * size})
    return convo, _SCHEMAS, ContextBudget(window=1_000_000)


def _make_room(convo, schemas, room, folds: int = 0, config: Config | None = None):
    config = config or _cfg(context_window=1_000_000, session_id="the-conversations-own")
    return agent_loop._make_room(
        convo,
        schemas,
        room,
        take_reading=lambda: ledger.take(convo, schemas, window=config.context_window),
        config=config,
        host="",
        routing="the-turns-routing",
        folds=folds,
    )


def _drive(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as done:
            return events, done.value


class TestWithinATurn:
    def test_the_request_is_the_turn_up_to_the_cut_on_its_session_with_its_tools(self, monkeypatch):
        convo, schemas, room = _a_long_turn()
        before = [dict(m) for m in convo]
        sent: list[dict] = []

        def stream(messages, config, host, tools=None, tool_choice="auto", routing=None):
            sent.append(
                {
                    "messages": messages,
                    "config": config,
                    "tools": tools,
                    "choice": tool_choice,
                    "routing": routing,
                }
            )
            return _stream(_a_brief("notes"))

        monkeypatch.setattr(agent_loop, "_stream_once", stream)
        _drive(_make_room(convo, schemas, room))

        request = sent[0]["messages"]
        assert request[:-1] == before[: len(request) - 1]
        assert request[-1]["role"] == "user" and request[-1]["content"].startswith("Stop here.")
        assert sent[0]["config"].session_id == "the-conversations-own", "it used to be blanked"
        assert sent[0]["tools"] == schemas and sent[0]["choice"] == "auto"
        assert sent[0]["routing"] == "the-turns-routing"

    def test_a_turn_tries_at_most_two_folds_failures_included(self, monkeypatch):
        """`_MAX_FOLDS` counted the notes in the conversation, and a fold absorbs the notes before
        it — so the count never passed one — and a failed fold left no notes at all. A summariser
        that always failed was asked again on every round."""
        convo, schemas, room = _a_long_turn()
        calls: list[int] = []

        def refuses(*args, **kwargs):
            calls.append(1)
            return _stream("Sure — carrying on with the reading.")

        monkeypatch.setattr(agent_loop, "_stream_once", refuses)
        folds = 0
        for _ in range(5):
            _events, (_book, folds) = _drive(_make_room(convo, schemas, room, folds=folds))

        assert len(calls) == agent_loop._MAX_FOLDS
        assert not any(m.get("_folded") for m in convo), "a failed fold leaves the turn as it was"

    def test_nothing_is_announced_when_there_is_nothing_to_fold(self, monkeypatch):
        """Past the fold point with too few steps of its own to fold — three enormous reads — so
        there is nothing worth a call. It used to say `compacting` anyway, and the turn was
        written down as folded."""
        convo, schemas, room = _a_long_turn(results=3, size=40_000)
        monkeypatch.setattr(agent_loop, "_stream_once", lambda *a, **k: _stream(_a_brief("notes")))

        events, (_book, folds) = _drive(_make_room(convo, schemas, room))

        assert "compacting" not in [event["type"] for event in events]
        assert folds == 0
