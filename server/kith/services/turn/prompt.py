"""Assembling the prompt one turn is sent.

The persona and the skill index at the head, the folded history, the attachments turned into
something a model can read, then what is true right now — the clock, the files he has open,
what he knows about this project. Everything between "here is what was said" and "here is
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
from kith.services import conversations, history, memory_context, project_context, touched
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

    What follows them changes constantly — the clock to the minute, how long since he
    last acted, whatever memory is present — and it used to be concatenated into
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
    return _assemble(out, folded, conversation_id)


def as_sent(messages, config, conversation_id: str = "", *, tool_chars: int = 0):
    """The same list a turn would send, built without sending anything or spending anything.

    For showing someone their own prompt. The question "what is actually in the context" has
    only ever been answerable in aggregate — the ledger's eleven categories — and a category is
    not the thing: "code he has read: 260k" cannot tell you *which* messages those are, in what
    order, or what any of them says.

    **It must not fold.** A real fold is a summarisation call, and a screen you open in order to
    look at something must never spend money to draw itself. `compact` already treats a
    summariser that comes back empty as "leave the history alone" — its own fail-safe, so a
    broken summariser never breaks a turn — which means handing it one that always returns ""
    reproduces the real prompt exactly in the two cases that matter (short enough not to fold; a
    stored brief that still covers it) and declines to invent one in the third.

    Returns ``(messages, fold_pending)``. ``fold_pending`` is True in that third case: the next
    real turn will summarise before it sends, so what is shown is what would go *if it did not*.
    Said out loud rather than papered over — this is a screen whose entire purpose is that the
    number on it is the number.
    """
    out = []
    persona = (config.system or "").strip()
    if persona:
        out.append({"role": "system", "content": f"{persona}\n\n{CHAT_DIRECTIVE}".strip()})

    folded, pending = history.fold_dry(messages, config, conversation_id, tool_chars=tool_chars)
    return _assemble(out, folded, conversation_id), pending


def _assemble(out: list[dict], folded: list[dict], conversation_id: str) -> list[dict]:
    """Turn a folded history into the message list a provider receives.

    Shared by `_build_messages` and `as_sent` so a preview of the prompt cannot drift from the
    prompt. They differ in exactly one thing — whether they are allowed to pay for a fold — and
    everything after that decision has to be the same list or the screen is describing a request
    that is never made.
    """
    for message in folded:
        role = message.get("role")
        if role == "system":
            # The folded brief. Passed straight through — it carries no attachments. `_summary`
            # is carried with it (and nothing else): it is how the ledger tells folded
            # conversation apart from system instruction, and without it here the marker set at
            # the source is lost on the way to the prompt the ledger actually measures.
            out.append(
                {
                    "role": "system",
                    "content": str(message.get("content") or ""),
                    **({"_summary": True} if message.get("_summary") else {}),
                }
            )
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
        # Canvas and diagrams first, and the order is load-bearing in both directions.
        # `_with_attachments` is the narrowing step — it rebuilds the message from role and
        # content alone, which is how internal keys are kept off the wire — so anything
        # downstream of it has already lost `canvas` and `diagrams` and silently does nothing.
        # And it is the step that can turn `content` into a list of parts for a vision model,
        # which neither of the other two can append to. Reversed, this composed away the whole
        # feature: the readings were dropped on every message and the unit test never noticed,
        # because it called `_with_canvas` directly.
        out.append(_with_attachments(_with_canvas(_with_diagrams(message))))
    sizes: dict[str, int] = {}
    now = _present_state(conversation_id, sizes)
    if now:
        # `_live` is for the ledger, not the provider — `openai_compat._to_openai` rebuilds
        # every message from role and content alone, so nothing internal can reach a host.
        # It earns its own line because this block is the one region rewritten every turn,
        # and therefore the one place where new context is nearly free to add: everything
        # ahead of it stays cached. Counted inside "System prompt", a block that has grown
        # to ten thousand tokens is indistinguishable from a large persona.
        out.append(
            {
                "role": "system",
                "content": now,
                "_live": True,
                # For the ledger, like `_live` itself — `openai_compat._to_openai` rebuilds every
                # message from role and content alone, so nothing internal reaches a host.
                "_project_chars": sizes.get("project", 0),
            }
        )
    return out


#: What a canvas may say about itself before it is talking rather than reporting. Small on
#: purpose: a control's reading is a word or a number, and anything longer arrived from a page a
#: model wrote after reading the open web.
_CANVAS_LIMITS = {"canvases": 8, "values": 32, "text": 200, "title": 80}


def _with_canvas(message: dict) -> dict:
    """Append what they have set on a canvas, as a note under their message.

    A canvas he draws is the first thing in a reply that keeps happening after the reply ends.
    Someone steps a nine-step walkthrough to the fork that needs them, and without this he learns
    nothing — not which step, not that they opened it at all.

    Rendered as a plain note under what they said, rather than delivered as a message of its own,
    and both halves of that are deliberate. Not its own turn, because moving a slider is not a
    question and this app has been burned by machinery that invents user messages. Not a
    structured field either: the model reads one thing, which is the conversation, and a note in
    it is visible in the transcript, foldable like everything else, and impossible to forget to
    render somewhere.

    Clamped rather than trusted. The values came from a sandboxed page written by a model that
    reads the web — the UI already validates them at the frame boundary, and this is the second
    check, because the first one runs on the far side of an HTTP request that anything local can
    make.
    """
    readings = [c for c in (message.get("canvas") or []) if isinstance(c, dict)]
    if not readings:
        return {k: v for k, v in message.items() if k != "canvas"}

    blocks: list[str] = []
    for reading in readings[: _CANVAS_LIMITS["canvases"]]:
        values = reading.get("values")
        if not isinstance(values, dict) or not values:
            continue
        lines = []
        for key, value in list(values.items())[: _CANVAS_LIMITS["values"]]:
            if not isinstance(key, str) or not re.fullmatch(r"[\w.-]{1,40}", key):
                continue
            if isinstance(value, bool):
                shown = "yes" if value else "no"
            elif isinstance(value, (int, float)):
                shown = str(value)
            elif isinstance(value, str):
                shown = value[: _CANVAS_LIMITS["text"]]
            else:
                continue
            lines.append(f"- {key}: {shown}")
        if not lines:
            continue
        title = str(reading.get("title") or "").strip()[: _CANVAS_LIMITS["title"]]
        head = f'On the canvas "{title}" they have set:' if title else "On the canvas they have set:"
        blocks.append(head + "\n" + "\n".join(lines))

    content = message.get("content")
    if not blocks or not isinstance(content, str):
        return {k: v for k, v in message.items() if k != "canvas"}
    joined = "\n\n".join(blocks)
    return {
        **{k: v for k, v in message.items() if k != "canvas"},
        "content": f"{content}\n\n{joined}".strip(),
    }


#: What a refused diagram may say about itself. One line each, and few of them: a reply with five
#: broken diagrams has one problem, not five.
_DIAGRAM_LIMITS = {"diagrams": 4, "text": 300}


def _with_diagrams(message: dict) -> dict:
    """Append the flow scripts that would not compile, as a note under their message.

    He draws an animated diagram, the choreography names a node that is not in the graph or a hop
    with no arrow under it, and the animation is refused — the picture is still shown, and one
    line under it says why. On their screen. He cannot see their screen, so without this the same
    broken route comes back in the next reply and the one after that, and the only way it is ever
    fixed is the person typing the error out by hand.

    The validator's own words, unedited, because they are already the fix: "unknown node
    \"UserTypesEmail\" in a route — nodes in this diagram: Core, Gateway, IdP, User" is the
    mistake, the vocabulary and the correction in one line.

    A note under what they said rather than a message of its own, for the reasons `_with_canvas`
    gives at length: a diagram that did not animate is not a question, and this app has been
    burned by machinery that invents user messages. Clamped rather than trusted for the same
    reason as well — it arrives over an HTTP request that anything local can make.
    """
    refused = [
        line.strip()[: _DIAGRAM_LIMITS["text"]]
        for line in (message.get("diagrams") or [])
        if isinstance(line, str) and line.strip()
    ][: _DIAGRAM_LIMITS["diagrams"]]
    without = {k: v for k, v in message.items() if k != "diagrams"}
    content = message.get("content")
    if not refused or not isinstance(content, str):
        return without

    head = (
        "An animated diagram in your last reply did not run — its flow script would not compile, "
        "so the diagram was drawn without the animation:"
    )
    lines = "\n".join(f"- {line}" for line in refused)
    return {**without, "content": f"{content}\n\n{head}\n{lines}".strip()}


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


def _present_state(conversation_id: str = "", _sizes: dict | None = None) -> str:
    """Everything about him that is true only at this moment, and everything about the project
    he is in.

    Two regions, and the split is the whole of the fix for a conversation that wandered. The
    first is about *him* — the clock, his memories, his channel, what he last did — and is the
    same whatever he is working on. The second is about **one project**, and until it existed
    there was no such region at all: every active project and every active task across all of
    them arrived on every turn with no project named on any row, and the one thing that would
    have grounded him — the project's own memory — was skipped whenever more than one project
    was open, because the fallback that found it declined to guess.

    So the project is resolved *once*, here, and everything downstream is told the answer rather
    than working it out again. `project_context.block` carries the project; `memory_context`
    carries the board only when there is no project to carry instead. Never both: two blocks
    describing the same tasks in different words is worse than either one alone.
    """
    project = project_context.resolve(AGENT_DB_PATH, conversation_id)
    blocks = [
        memory_context.presence_block(AGENT_DB_PATH),
        memory_context.messages_block(AGENT_DB_PATH, int(project["id"]) if project else None),
    ]
    if project:
        # Named, not described. See `project_context.others` — he has to be able to recognise
        # that something belongs elsewhere, and nothing more than that.
        blocks.append(project_context.others(AGENT_DB_PATH, int(project["id"])))
    else:
        # Nothing has been chosen, so the menu is the right answer.
        blocks.append(memory_context.projects_block(AGENT_DB_PATH))
        blocks.append(memory_context.work_block(AGENT_DB_PATH))
    # Scoped to the project in hand, like the region below it. His memories were global — the
    # same core set and eight most-recent on every turn — so a chat in one project carried
    # another's detail and none of its own. `recall` still reaches anything; this is only what
    # arrives without asking.
    present = memory_context.context_block(AGENT_DB_PATH, project_id=int(project["id"]) if project else None)
    if present:
        blocks.append(f"[Your memory right now]\n{present}")
    # What he knows about the project he is in. Injected rather than fetched, deliberately:
    # a file he has to remember to open is a file he will not open, which is the shape of
    # nearly every failure this codebase has a comment about. That reasoning now covers the
    # whole project — its plan, its columns, its folder's standing against the remote — and not
    # just `memory.md`, for exactly the same reason.
    about_project = project_context.block(AGENT_DB_PATH, project)
    blocks.append(about_project)
    # Reported out rather than measured by the caller, because only this function knows which
    # of the blocks it joined was the project one. The ledger reads it: inside "Where he is
    # right now" the project region is indistinguishable from the clock, and it is now the
    # largest thing in there by an order of magnitude. Same reasoning that gave the live block
    # its own line rather than leaving it inside "System prompt".
    if _sizes is not None:
        _sizes["project"] = len(about_project)
    # Last, so it is the closest thing to what was just asked. Everything above is about him or
    # about the project; this is the only part that is about the conversation, and it is the part
    # that stops him opening a file he has already read — measured at 54% of every read he makes.
    blocks.append(touched.manifest(AGENT_DB_PATH, conversation_id))
    return "\n\n".join(block for block in blocks if block).strip()
