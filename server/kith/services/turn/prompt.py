"""Assembling the prompt one turn is sent.

The persona and the skill index at the head, the folded history, the attachments turned into
something a model can read, then what is true right now — the clock, his mood, the files he has
open, what he knows about this project. Everything between "here is what was said" and "here is
the request".

It lived in `api/routes/chat.py` because the chat route was the first caller. It was never
route work: `services/scheduler.py` reached up into that module for `_build_messages` when a
reminder needed a turn, which is how the arrow pointed the wrong way for months, and the fix at
the time was to move the *turn* out rather than the prompt.

**Order is the specification here, not a style.** `config.system` is the region
`llm/caching` treats as the stable head, so anything appended to it is billed at read price
after the first request and anything that moves invalidates the persona sitting in front of it.
The present-state block goes last, after the recent turns, because it carries the clock — the
one part guaranteed to differ every turn, and therefore the one part that must never sit inside
the cached prefix.

`tool_chars` arrives as a number rather than being measured here. The fold needs to know how
big the tools block is, and asking would mean this module importing `kith.tools` — an adapter,
which nothing below it may import. The route measures it and passes it down, the same shape
`history.fold` already takes.
"""

from __future__ import annotations

import base64
import itertools
import re
from pathlib import Path

from kith.config import model_capabilities, ollama_host
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo
from kith.services import conversations, history, memory_context, project_memory, touched
from kith.settings import AGENT_DB_PATH

#: What a conversation is for.
#:
#: This used to open with "CAPTURE, DON'T DO (most important)" — asked to build something, he
#: was to file a task, refuse to touch a work tool, and say roughly when he would get to it.
#: The intent was sound when a conversation could not carry a long job: it was the
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
#:
#: Points 3 and 4 are the other half of "do the work", and they were added after watching the
#: opposite failure. 3 used to read only "ASK WHEN IT WOULD CHANGE WHAT YOU BUILD" — being stuck
#: does not change what you build, so a turn that needed something had no sanctioned way to say
#: so and wrote a paragraph instead. 4 is the specific shape that keeps recurring: he states a
#: condition ("I won't mark it done until the evidence is in the logs"), and then treats it as
#: something that will happen to him rather than something to go and look at. Measured on two
#: consecutive turns with the full toolset and nothing narrowed: zero tool calls, 409,337 prompt
#: tokens, and the evidence already thirty-one minutes old in a log he never opened. The turn
#: after — same question, pushed — found it in three calls. See
#: `tests/test_being_blocked_is_a_question_not_a_stop.py`.
CHAT_DIRECTIVE = (
    "Your person is here, in this conversation, and can redirect you. That is the only thing "
    "this note adds to what you already know about how you work.\n\n"
    "1. DO THE WORK. When they hand you something, start on it. Not 'I've noted that and I'll "
    "get to it' — read the file, run the command, make the change, and show them what "
    "happened.\n"
    "2. GIVE IT A HOME FIRST, if it is more than one sitting. A project and its first "
    "milestone's tasks, so the work survives being put down — the `running-a-project` skill is "
    "how. Say the plan back before it runs, so they can fix it. Then start on the first task "
    "in the same breath.\n"
    "3. ASK WHEN IT WOULD CHANGE WHAT YOU BUILD, and ask when you are BLOCKED. They are right "
    "here, so a real question costs almost nothing. A guess dressed as a decision costs the "
    "whole task — and so does going quiet. Use `ask`; it holds the turn open for the answer.\n"
    "4. A CONDITION YOU STATE IS A CONDITION YOU CHECK. 'I won't call it done until X' and 'I "
    "still need to confirm Y' are your next action, not something you are waiting on. Go and "
    "look — read the log, run the query, list the directory. Only if looking is genuinely not "
    "something you can do is it a question for them, and then it is `ask`, not a paragraph. "
    "Ending your turn with an unchecked condition is the one way to make no progress at all."
)

#: Where attachments land. Inside his folder on purpose: writing there needs no permission,
#: the path is short enough to type in a shell command, and it is somewhere a person would
#: think to look. Named for what it is rather than hidden.
ATTACHMENT_DIR = "inbox"

#: How much of a text attachment rides inside the message.
#:
#: Not a limit on what can be attached — the file is written to disk whatever its size, and the
#: path is always given. This is a limit on what is spent carrying it in the prompt. Twenty
#: thousand characters is a few thousand tokens: enough for a stack trace, a config, a long
#: instruction, or most of a source file, and small enough that pasting something enormous costs
#: a fraction of the window rather than the whole conversation.
TEXT_INLINE_CHARS = 20_000


def _conversation_chars(messages) -> int:
    """How much prose a message list carries — the number the fold is deciding about."""
    return sum(len(str(m.get("content") or "")) for m in messages)


def _build_messages(
    messages,
    config,
    conversation_id: str = "",
    _folded: dict | None = None,
    *,
    tool_chars: int = 0,
):
    """The persona, then the turns, then the state he is in right now.

    The order is a caching decision, and it is worth more than it looks. Everything a
    provider can reuse has to sit in an unchanging prefix: the persona and the turn
    CHAT_DIRECTIVE never change, so they go first and alone.

    What follows them changes constantly — the clock to the minute, his mood, how long
    since he last acted, whatever memory is present — and it used to be concatenated into
    the same system message. That is what made a repeated "hey" cost full price twice:
    the moment he replies, `last_activity_at` moves, "1 hour ago" becomes "just now", and
    the message is no longer byte-identical. Providers that cache automatically match at
    message granularity, so one changed word at the end discarded ~8,000 cacheable tokens
    at the start. Measured: identical consecutive messages, 0% cached; with the volatile
    part moved out, 99.7%.

    Putting it last is also the better prompt. It is the freshest thing he knows, and the
    CHAT_DIRECTIVE already sat at the end for exactly that reason.
    """
    out = []
    persona = (config.system or "").strip()
    if persona:
        # Byte-identical on every request Kith ever makes. Nothing else may join it.
        out.append({"role": "system", "content": f"{persona}\n\n{CHAT_DIRECTIVE}".strip()})
    # Fold the older turns of a long conversation into a running brief before replaying them,
    # so the prompt stops growing without bound. The brief sits here deliberately — after the
    # cached persona, before the recent turns — so it never disturbs the stable prefix, and it
    # is persisted so a fold is not re-run every turn. Below the size threshold this returns the
    # history untouched, so a short conversation is byte-for-byte what it was.
    folded, fresh = history.fold(messages, config, conversation_id, ollama_host(), tool_chars=tool_chars)
    if fresh is not None and conversation_id:
        conversations.record_summary(conversation_id, fresh["through"], fresh["text"])
    # Reported rather than inferred by the caller. Comparing what went in against what comes
    # out cannot see a fold: this function also prepends the persona and appends the present
    # state, so a heavily folded turn can still produce a longer list than it was given.
    if _folded is not None:
        _folded.update(
            happened=fresh is not None or len(folded) < len(messages),
            fromChars=_conversation_chars(messages),
            toChars=_conversation_chars(folded),
        )
    for message in folded:
        role = message.get("role")
        if role == "system":
            # The folded brief. Passed straight through — it carries no attachments.
            out.append({"role": "system", "content": str(message.get("content") or "")})
            continue
        if role == "assistant" and message.get("tool_calls"):
            # A replayed tool call from an earlier turn (see `conversations.full_messages`).
            # Nothing to attach and no empty-content check applies — it carries no `content`
            # at all, that's not the same as having nothing to say.
            out.append(
                {"role": "assistant", "content": message.get("content"), "tool_calls": message["tool_calls"]}
            )
            continue
        if role == "tool":
            # A replayed tool result. Passed straight through, same reason.
            out.append(
                {
                    "role": "tool",
                    "tool_name": message.get("tool_name", ""),
                    "content": message.get("content", ""),
                }
            )
            continue
        if role not in ("user", "assistant"):
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
        # `_live` is for the ledger, not the provider — `openai_compat._to_openai` rebuilds
        # every message from role and content alone, so nothing internal can reach a host.
        # It earns its own line because this block is the one region rewritten every turn,
        # and therefore the one place where new context is nearly free to add: everything
        # ahead of it stays cached. Counted inside "System prompt", a block that has grown
        # to ten thousand tokens is indistinguishable from a large persona.
        out.append({"role": "system", "content": now, "_live": True})
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

    # Text is given, not referred to.
    #
    # Everything above answers "where is this file" — the right answer for a PDF or a spreadsheet,
    # which he opens with his own tools because he has a computer. It is the wrong answer for text.
    # The composer lifts a large paste out of the message and carries it alongside as a file, so
    # pointing at the path would mean the log someone just handed him costs a tool call to read,
    # on the one kind of attachment whose entire content is already in the request.
    for attachment, where in saved:
        block = _text_block(attachment, where)
        if block:
            text = f"{text}\n\n{block}".strip()

    if not inline:
        return {"role": message["role"], "content": text}
    parts: list[dict] = [{"type": "text", "text": text}] if text else []
    parts += [{"type": "image_url", "image_url": {"url": str(image["data"])}} for image in inline]
    return {"role": message["role"], "content": parts}


def _text_block(attachment: dict, where: str) -> str:
    """A text attachment's actual contents, fenced and named — or "" if this is not one.

    Truncation says so, out loud and in the same breath as the path.
    A silently shortened file is the worst version of this: he reads what he was given, believes
    it is the whole thing, and answers confidently about a config whose second half he never saw.
    """
    if str(attachment.get("kind")) == "image":
        return ""
    media = str(attachment.get("mediaType") or "")
    if not (media.startswith("text/") or media in ("application/json", "application/xml")):
        return ""

    data = str(attachment.get("data") or "")
    if not data:
        return ""
    try:
        payload = data.split(",", 1)[1] if data.startswith("data:") and "," in data else data
        body = base64.b64decode(payload, validate=False).decode("utf-8", errors="replace")
    except Exception:
        # Undecodable means it was never really text. The path above still stands, and he can open
        # it however he likes — which is a better answer than a block of replacement characters.
        return ""
    if not body.strip():
        return ""

    name = str(attachment.get("name") or "attachment")
    if len(body) > TEXT_INLINE_CHARS:
        kept = body[:TEXT_INLINE_CHARS]
        return (
            f"`{name}` — the first {TEXT_INLINE_CHARS:,} characters of {len(body):,}. "
            f"The whole file is at `{where}`; read it if the rest matters.\n"
            f"```\n{kept}\n```"
        )
    return f"`{name}`:\n```\n{body}\n```"


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
        memory_context.presence_block(AGENT_DB_PATH),
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
    # Last, so it is the closest thing to what was just asked. Everything above is about him;
    # this is the only part that is about the conversation, and it is the part that stops him
    # opening a file he has already read — measured at 54% of every read he makes.
    blocks.append(touched.manifest(AGENT_DB_PATH, conversation_id))
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
