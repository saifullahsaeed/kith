"""Talking to him."""

from __future__ import annotations

import base64
import itertools
import json
import re
import time
from pathlib import Path

from flask import Response

from kith.api.blueprint import api
from kith.autonomy.prompts import _describe_call, _short_args
from kith.config import (
    AGENT_DB_PATH,
    default_config,
    merge_overrides,
    model_capabilities,
    ollama_host,
)
from kith.domain import clock
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo
from kith.schemas import (
    ChatRequestSchema,
)
from kith.services import conversations, memory_context, session_context
from kith.services.agent_loop import stream_agent

#: What a conversation is for.
#:
#: This used to open with "CAPTURE, DON'T DO (most important)" — asked to build something, he
#: was to file a task, refuse to touch a work tool, and say roughly when he would get to it.
#: The intent was sound when he could only really work unattended: a conversation was the
#: wrong place for a long job, so it became an intake desk.
#:
#: The cost was that everything consequential happened at the intake desk anyway. Laying out a
#: project, deciding a roadmap, choosing what to build first — all of it lands in a
#: conversation, and a conversation was the one path with no round budget worth the name, no
#: loop detection, and no row in any log. He would file the work correctly and then be unable
#: to lift a finger, which reads as an assistant that does nothing.
#:
#: So a conversation is now where the work happens. He has the same tools, the same limits and
#: the same discipline he has when nobody is watching, and the difference between the two is
#: only that here you are present to redirect him.
CHAT_DIRECTIVE = (
    "Your person is here, talking to you. This is where the work happens — you have every "
    "tool you have when you are on your own, and they are present to redirect you.\n\n"
    "1. DO THE WORK. When they hand you something, start on it. Not "
    "'I've noted that and I'll get to it' — that was a rule from when a conversation could "
    "not carry real work, and it made you useless in the one place that matters most. Read "
    "the file, run the command, make the change, and show them what happened.\n"
    "2. GIVE IT A HOME FIRST, if it is more than one sitting. A project and its first "
    "milestone's tasks, so the work survives being put down — the `running-a-project` skill "
    "is how. Every task is an OUTCOME with a checkable finish line in its description (a "
    "command that exits 0, a test that passes, a file that exists); 'verify/inspect X' is a "
    "done-condition, not a task of its own. Lay out only this first milestone, then say the "
    "plan back so they can review it before it runs on its own — a plan they have seen is one "
    "they can fix. Then start on the first task in the same breath.\n"
    "3. ONE STEP AT A TIME, AND SAY WHAT YOU DID. Work in the smallest useful increments and "
    "report each one plainly. They can see your tool calls, so do not narrate them — tell "
    "them what it means and what you are doing next.\n"
    "4. STOP WHEN YOU ARE GENUINELY STUCK, and say what you need. Not a guess dressed as an "
    "answer, and not silence. They are right here, so asking costs almost nothing — which is "
    "exactly why it should be a real question about a real obstacle.\n"
    "5. NO GUESSING. If the request is ambiguous, ask which they mean or say plainly what you "
    "do not know.\n"
    "6. Only ever use tools that actually exist in your tool list. Never invent a tool.\n"
    "7. Always finish with a short, clear, human reply. Never leave them with raw tool output "
    "or your own thinking-out-loud."
)


def _build_messages(messages, config, conversation_id: str = ""):
    """The persona, then the turns, then the state he is in right now.

    The order is a caching decision, and it is worth more than it looks. Everything a
    provider can reuse has to sit in an unchanging prefix: the persona and the turn
    directive never change, so they go first and alone.

    What follows them changes constantly — the clock to the minute, his mood, how long
    since he last acted, whatever memory is present — and it used to be concatenated into
    the same system message. That is what made a repeated "hey" cost full price twice:
    the moment he replies, `last_activity_at` moves, "1 hour ago" becomes "just now", and
    the message is no longer byte-identical. Providers that cache automatically match at
    message granularity, so one changed word at the end discarded ~8,000 cacheable tokens
    at the start. Measured: identical consecutive messages, 0% cached; with the volatile
    part moved out, 99.7%.

    Putting it last is also the better prompt. It is the freshest thing he knows, and the
    directive already sat at the end for exactly that reason.
    """
    out = []
    persona = (config.system or "").strip()
    if persona:
        # Byte-identical on every request Kith ever makes. Nothing else may join it.
        out.append({"role": "system", "content": f"{persona}\n\n{CHAT_DIRECTIVE}".strip()})
    for message in messages:
        if message.get("role") not in ("user", "assistant"):
            continue
        # An assistant turn with nothing in it carries no information and is refused by some
        # providers outright — "the message at position N with role 'assistant' must not be
        # empty". Nothing writes one today (`record` skips empty text), but a conversation
        # that acquired one from any source would be *permanently* unusable: every later
        # message rebuilds the same history and fails the same way, with nothing on screen
        # to say why. Skipping it costs nothing and cannot be the wrong call.
        if message.get("role") == "assistant" and not str(message.get("content") or "").strip():
            continue
        out.append(_with_attachments(message))
    now = _present_state(conversation_id)
    if now:
        out.append({"role": "system", "content": now})
    return out


def _with_attachments(message: dict) -> dict:
    """Turn a message with attachments into something he can actually use.

    Every attachment is written into his folder first, and *then* the question of what the
    model can see is asked. That order is the whole design.

    The old version got both halves wrong. Images were inlined without checking whether the
    model had vision at all — the docstring claimed it checked and the code did not. And a
    non-image was reduced to "[They attached: report.pdf. Read it with your own tools.]" with
    no path, no bytes, and nothing written anywhere: he was told to open a file that did not
    exist. Meanwhile the composer only accepted `image/*`, so the file branch could not run
    even in principle.

    Writing it down first means the fallback is real. A model with no vision still gets told
    about the picture and where it is, and he can open it with his own tools — `sips` for its
    size, python for its pixels — which is a worse answer than seeing it and a much better one
    than the attachment silently evaporating.
    """
    text = message.get("content", "") or ""
    attachments = [a for a in (message.get("attachments") or []) if isinstance(a, dict)]
    if not attachments:
        return {"role": message["role"], "content": text}

    saved: list[tuple[dict, str]] = []
    for attachment in attachments:
        try:
            saved.append((attachment, _save_attachment(attachment)))
        except Exception as exc:
            saved.append((attachment, f"(could not be saved: {exc})"))

    can_see = bool(model_capabilities().get("images"))
    inline = [a for a, _ in saved if str(a.get("kind")) == "image" and a.get("data")] if can_see else []

    lines = []
    for attachment, where in saved:
        name = str(attachment.get("name") or "a file")
        seeing = " (shown to you below)" if attachment in inline else ""
        lines.append(f"- `{where}`{seeing}" if where.startswith("inbox/") else f"- {name} {where}")
    note = "They attached:\n" + "\n".join(lines)
    if not can_see and any(str(a.get("kind")) == "image" for a, _ in saved):
        note += "\n\nYou cannot be shown images with this model, so open it yourself if it matters."
    text = f"{text}\n\n{note}".strip()

    if not inline:
        return {"role": message["role"], "content": text}
    parts: list[dict] = [{"type": "text", "text": text}] if text else []
    parts += [{"type": "image_url", "image_url": {"url": str(image["data"])}} for image in inline]
    return {"role": message["role"], "content": parts}


#: Where attachments land. Inside his folder on purpose: writing there needs no permission,
#: the path is short enough to type in a shell command, and it is somewhere a person would
#: think to look. Named for what it is rather than hidden.
ATTACHMENT_DIR = "inbox"


def _save_attachment(attachment: dict) -> str:
    """Write one attachment into his folder and return the relative path.

    Relative, because the persona tells him to prefer relative paths and to write them in
    backticks — which makes the path a link his person can click, so an attachment they sent
    is one they can also open again from the reply.
    """
    data = str(attachment.get("data") or "")
    if not data:
        raise ValueError("no data was sent")
    payload = data.split(",", 1)[1] if data.startswith("data:") and "," in data else data
    raw = base64.b64decode(payload, validate=False)

    # Their filename, not a generated one — he is going to talk about this file to them, and
    # "inbox/receipt-march.pdf" is a thing they recognise. Stripped of anything that could
    # walk out of the folder.
    name = Path(str(attachment.get("name") or "attachment")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip() or "attachment"

    folder = sandbox.root() / ATTACHMENT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if target.exists() and target.read_bytes() != raw:
        # A second, different file with the same name. Overwriting would silently replace
        # something they sent earlier and might still be talking about.
        stem, suffix = target.stem, target.suffix
        for n in itertools.count(2):
            candidate = folder / f"{stem}-{n}{suffix}"
            if not candidate.exists() or candidate.read_bytes() == raw:
                target = candidate
                break
    target.write_bytes(raw)
    return f"{ATTACHMENT_DIR}/{target.name}"


def _present_state(conversation_id: str = "") -> str:
    """Everything about him that is true only at this moment."""
    blocks = [
        memory_context.self_block(AGENT_DB_PATH),
        clock.presence_block(AGENT_DB_PATH),
        memory_context.people_block(AGENT_DB_PATH),
        memory_context.messages_block(AGENT_DB_PATH),
        memory_context.projects_block(AGENT_DB_PATH),
        memory_context.work_block(AGENT_DB_PATH),
    ]
    present = memory_context.context_block(AGENT_DB_PATH)
    if present:
        blocks.append(f"[Your memory right now]\n{present}")
    # What he knows about the project he is in. Injected rather than fetched, deliberately:
    # a file he has to remember to open is a file he will not open, which is the shape of
    # nearly every failure this codebase has a comment about.
    blocks.append(_project_memory_block(conversation_id))
    return "\n\n".join(block for block in blocks if block).strip()


def _project_memory_block(conversation_id: str = "") -> str:
    """`.kith/memory.md` for whatever this session is working on.

    Asked of the session, not of the board. The first version looked for "the only active
    project with a folder", which is a guess that gives the right answer exactly until there
    are two — and two at once is the point of sessions, so it was a guess with a deadline.

    Falls back to the single-active-project case for a conversation that has not adopted a
    project yet, because a session usually acquires one part-way through rather than at the
    start, and until it does the one open project is very probably the one being discussed.
    """
    from kith.services import project_memory

    try:
        project = None
        if conversation_id:
            bound = repo.conversations.project_of(AGENT_DB_PATH, conversation_id)
            if bound:
                project = repo.projects.get_project(AGENT_DB_PATH, bound)
        if project is None:
            active = [
                row
                for row in repo.projects.list_projects(AGENT_DB_PATH)
                if row.get("status") == "active" and row.get("directory")
            ]
            if len(active) != 1:
                return ""
            project = active[0]
    except Exception:
        return ""
    if not project or not project.get("directory"):
        return ""
    return project_memory.block(project["directory"], project.get("name") or "")


class _Recorder:
    """Writes a turn to the transcript as it happens, keeping its shape.

    Deltas are buffered and flushed as blocks rather than written per token: a line per
    token would be a hundred-thousand-line file for one afternoon, and the block is the
    unit anything reading it back wants anyway.

    A block is flushed when the channel changes — reasoning to prose, prose to a tool call —
    which is exactly how the live view decides where one part ends and the next begins. That
    is deliberate: a resumed conversation should look like the one you had, not like a
    transcript of it.
    """

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.channel = ""
        self.buffer: list[str] = []
        self.said: list[str] = []

    def saw(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "delta":
            role = "reasoning" if event.get("role") == "reasoning" else "text"
            if role != self.channel:
                self._flush()
                self.channel = role
            self.buffer.append(event.get("text") or "")
            return
        # Anything else ends whatever block was open, so ordering survives.
        self._flush()
        if kind in ("tool_call", "tool_result", "stats"):
            conversations.record_event(self.conversation_id, kind, _readable(event))

    def finish(self, error: str | None = None, stopped: bool = False) -> None:
        self._flush()
        if error:
            conversations.record_event(self.conversation_id, "error", {"message": error})
        if stopped:
            conversations.record_event(self.conversation_id, "stopped", {})
        # The whole reply as one message, which is what the next turn's prompt needs. Written
        # on the error and disconnect paths too: an interrupted turn is exactly the one whose
        # half-answer you want to keep.
        text = "".join(self.said).strip()
        if text:
            conversations.record(AGENT_DB_PATH, self.conversation_id, "assistant", text)

    def _flush(self) -> None:
        text = "".join(self.buffer)
        self.buffer = []
        if not text.strip():
            self.channel = ""
            return
        if self.channel == "reasoning":
            conversations.record_event(self.conversation_id, "reasoning", {"text": text})
        elif self.channel == "text":
            conversations.record_event(self.conversation_id, "said", {"text": text})
            self.said.append(text)
        self.channel = ""


def _readable(event: dict) -> dict:
    """One transcript line, with a picture's bytes left out of it.

    The conversation stopped carrying base64 when the image leak was fixed, and the
    transcript went on storing every byte: a six-round turn that looked at one page came to
    360KB, of which 344,943 was a single string.

    That is not a context cost — nothing re-reads it — but it is against the whole point of
    the file. The reason transcripts are plain-text JSONL rather than rows in a table is that
    you can grep them, open them in an editor, and still read them in ten years. One `grep`
    hit that prints 345,000 characters of base64 is none of those things, and it is bytes on
    disk forever for every image he ever looks at.

    Nothing is lost. The path is in the same record, the file is in his folder, and the
    interface only ever tested this field for truthiness to say "looked at it" — it never
    rendered the data URI. So the trail still says which image, when, and that he saw it.
    """
    if event.get("type") != "tool_result":
        return event
    from kith.services.agent_loop import _without_image

    return {**event, "result": _without_image(event.get("result"))}


class _MindFeed:
    """A chat turn, on the record.

    Chat used to leave no trace anywhere except its own transcript: no line on the Mind
    feed, no row in the flight recorder. Every mode the runner has wrote both, and the one
    path where most of the real work happens wrote neither — so "what has he been doing"
    could be answered for the unattended steps and not for the afternoon you spent together,
    and a tick that cost 40k tokens was visible while a chat turn that cost 200k was not.

    It matters more now that the Mind panel is per session. A conversation you have never
    left working would otherwise show an empty panel forever, which reads as broken rather
    than as "he has not gone off on his own here".

    Deliberately the same shapes the runner emits — `reply` as the head, `tool`, `tokens`,
    `done` — so the panel renders a turn exactly as it renders a step. A second vocabulary
    for the same events would have meant a second renderer.
    """

    def __init__(self, conversation_id: str, opening: str = "") -> None:
        self.conversation_id = conversation_id
        self.opening = opening
        self.tools: list[str] = []
        self.rounds = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.tokens_uncached = 0
        self.said = ""
        self.error: str | None = None
        self.started = time.monotonic()
        self._publish("reply", f"you: {opening.strip()[:80]}" if opening.strip() else "you: (attachment)")

    def _publish(self, kind: str, text: str, **fields) -> None:
        try:
            from kith.autonomy.runner import runner

            runner.publish(kind, text, conversation=self.conversation_id, **fields)
        except Exception:
            # A feed line must never be the thing that takes a turn down.
            pass

    def saw(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "tool_call":
            self.tools.append(event["name"])
            self._publish(
                "tool",
                _describe_call(event["name"], event.get("arguments") or {}),
                tool=event["name"],
                args=_short_args(event.get("arguments") or {}),
            )
        elif kind == "delta" and event.get("role") == "text":
            self.said += event.get("text") or ""
        elif kind == "stats":
            stats = event.get("stats") or {}
            fresh = int(stats.get("uncachedTokens") or 0)
            out = int(stats.get("responseTokens") or 0)
            self.rounds += 1
            self.tokens_in += int(stats.get("promptTokens") or 0)
            self.tokens_out += out
            self.tokens_uncached += fresh
            self._publish(
                "tokens",
                f"{fresh + out:,} tokens",
                tokens={
                    "round": self.rounds,
                    "uncached": fresh,
                    "cached": int(stats.get("cachedTokens") or 0),
                    "out": out,
                },
            )
        elif kind == "error":
            self.error = event.get("message")
            self._publish("error", str(self.error))

    def finish(self) -> None:
        self._publish("done", "turn complete")
        outcome = f"error: {self.error}" if self.error else (self.said.strip()[:280] or "answered")
        try:
            repo.messages.add_tick_log(
                AGENT_DB_PATH,
                clock.now_iso(),
                "chat",
                self.opening.strip()[:80] or None,
                self.tools,
                self.tokens_in,
                self.tokens_out,
                round(time.monotonic() - self.started, 2),
                outcome,
                tokens_uncached=self.tokens_uncached,
            )
        except Exception:
            pass


@api.post("/chat")
@api.input(ChatRequestSchema, arg_name="payload")
@api.doc(
    summary="Stream an agent turn",
    description=(
        "Runs the agentic loop (the model may call its memory/notes/journal/task "
        "tools) and streams `application/x-ndjson`: one JSON object per line.\n"
        '- `{"type":"delta","role":"reasoning"|"text","text":"..."}`\n'
        '- `{"type":"tool_call","id":"...","name":"...","arguments":{...}}`\n'
        '- `{"type":"tool_result","id":"...","name":"...","result":{...}}`\n'
        '- `{"type":"stats","stats":{...}}` (one per model request, so several per turn — '
        "`uncachedTokens` is the prompt with cache hits removed)\n"
        '- `{"type":"error","message":"..."}`\n'
        '- `{"type":"done"}` (terminal)'
    ),
    responses={200: "NDJSON stream of agent events"},
)
def chat(payload):
    config = merge_overrides(default_config(), payload.get("config") or {})
    history = payload.get("messages") or []

    # Which conversation this belongs to. Opened on the first message rather than when the
    # window opens, so idly launching the app does not litter the history with empties.
    #
    # Resolved *before* the prompt is built, not after: what this session is working on
    # decides which project's memory he is shown, and building the prompt first meant that
    # question was asked with no session to ask it about.
    conversation_id = str(payload.get("conversationId") or "").strip()
    latest = next((m.get("content", "") for m in reversed(history) if m.get("role") == "user"), "")
    if not conversation_id:
        conversation_id = conversations.start(AGENT_DB_PATH, latest)["id"]
    messages = _build_messages(history, config, conversation_id)
    conversations.record(AGENT_DB_PATH, conversation_id, "user", latest)

    def generate():
        # Tell the client which conversation it is in before anything else, so a chat
        # started without an id can attach itself and reload into the same place.
        yield json.dumps({"type": "conversation", "id": conversation_id}) + "\n"
        # A turn is a loop, and the transcript keeps its shape: what he reasoned, what he
        # said, what he called and what came back, in the order it happened. Anything less
        # and a resumed conversation is a summary of itself.
        recorder = _Recorder(conversation_id)
        try:
            # Bound for the whole turn, so a tool that acts on a project records that this
            # session is the one working on it. Starting a project here and having nothing
            # know whose it was is how `conversations.project_id` stayed null from the day it
            # was added: read on every turn to pick the project memory, written by nobody.
            with session_context.working_in(conversation_id):
                yield from _turn(recorder, messages, config, conversation_id, latest)
        except GeneratorExit:
            # Client disconnected (e.g. Stop was clicked) — end quietly, but keep what he
            # had already said. A stopped answer is still an answer that was given.
            recorder.finish(stopped=True)
            raise
        except Exception as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"

    return Response(
        generate(),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _turn(recorder: _Recorder, messages: list, config, conversation_id: str, opening: str = ""):
    """One turn of the loop, streamed as it happens.

    Split out from the route so the session binding can wrap it in a `with` and every tool
    call inside knows which conversation it belongs to.
    """
    watcher = _MindFeed(conversation_id, opening)
    for event in stream_agent(
        messages,
        config,
        ollama_host(),
        AGENT_DB_PATH,
        conversation_id=conversation_id,
        # Rounds stay on the declared knob (max_rounds, 40) rather than the tick's
        # hardcoded 16. A conversation genuinely wants more room than an unattended
        # step: you are here, so a long turn is one you can watch and stop, and the
        # tick's 16 exists because nobody is.
        #
        # `expect_durable` stays off, and that was learned the hard way an hour after
        # turning it on. A tick that leaves nothing behind really is a failure — the
        # whole point of one is to make progress nobody asked to watch. But a
        # conversation is not that, and cannot be told apart upfront: "what have you
        # been working on" is answered by answering it. With durability demanded, he
        # replied honestly that the board was empty and then wrote
        # `session-findings-2026-07-31.md` to satisfy the rule — a file nobody wanted,
        # about nothing, because the harness insisted on an artefact.
        #
        # The distinction that matters is not chat versus tick. It is "asked to do
        # something" versus "asked something", and the transport does not know which
        # it is carrying. So the directive above asks him to do the work, and nothing
        # forces him to manufacture evidence of having done it.
    ):
        recorder.saw(event)
        watcher.saw(event)
        # Scrubbed on the way out too, not only on the way into the transcript. The model has
        # not been sent base64 since the image leak was fixed and the transcript stopped
        # storing it shortly after — but this line went on streaming the whole data URI to the
        # browser, where the generic result renderer printed it. So looking at the interface
        # showed forty thousand characters of base64 sitting in a tool result, which is
        # indistinguishable from the bug that is actually fixed, and reasonable grounds to
        # think it was back.
        #
        # It is a real cost as well as a misleading one: 44KB per image over the wire and into
        # the DOM, for a string nothing on the other side can use. The interface only ever
        # tested the field for truthiness to say "looked at it".
        yield json.dumps(_readable(event)) + "\n"
        if event.get("type") == "error":
            recorder.finish(error=event.get("message"))
            watcher.finish()
            return
    recorder.finish()
    watcher.finish()
    yield json.dumps({"type": "done"}) + "\n"
