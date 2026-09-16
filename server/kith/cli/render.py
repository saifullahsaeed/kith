"""Turning a turn into something a terminal — or another program — can read.

**Prose goes to stdout. Everything else goes to stderr.** That one rule is what makes the
command composable, and it is worth being exact about why. ``kith send "summarise this" |
pbcopy`` should put the answer on the clipboard, not the answer wrapped in reasoning, tool
lines and a token count. ``TEXT=$(kith send ...)`` should capture the reply. Neither works if
the commentary shares a stream with the content, and the alternative — a ``--quiet`` flag
people must remember — fails in the direction where you only notice after pasting.

So reasoning, tool calls, the conversation id and the closing statistics are all stderr. They
stay visible when you are watching, and they vanish the moment you redirect. Nothing has to be
switched off.

**``--json`` is a passthrough, not a re-serialisation.** The event shapes are documented on
``/api/chat`` itself, and that documentation is the contract another agent reads. Writing the
server's own bytes back out means a field added there arrives here without this file being
touched — and, more to the point, means there is no second description of the format to drift
out of step with the first.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from typing import Any, TextIO


#: Set once, from whether stderr is a terminal. Colour is commentary, and commentary is on
#: stderr, so it is stderr's tty-ness that decides — not stdout's. Getting this backwards
#: means escape codes in a log file whenever someone redirects the answer.
def _styling(stream: TextIO) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)()) and "NO_COLOR" not in os.environ


class Style:
    def __init__(self, on: bool) -> None:
        self.on = on

    def bold(self, text: str) -> str:
        return f"\033[1m{text}\033[0m" if self.on else text

    def dim(self, text: str) -> str:
        return f"\033[2m{text}\033[0m" if self.on else text

    def red(self, text: str) -> str:
        return f"\033[31m{text}\033[0m" if self.on else text


def _summarise(arguments: Any) -> str:
    """One line describing a tool call, short enough to sit in a margin.

    Tool arguments are routinely a file's entire new contents. Printing them is not a
    verbosity preference, it is scrolling the answer off the screen — so the longest value is
    shown truncated and the rest by name only.
    """
    if not isinstance(arguments, dict) or not arguments:
        return ""
    interesting = ("path", "file", "objective", "query", "command", "name", "url", "ask")
    for key in interesting:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            flat = " ".join(value.split())
            return flat if len(flat) <= 72 else flat[:69] + "…"
    return ", ".join(sorted(arguments)[:3])


def _aside(kind: str, event: dict) -> str:
    """One line for an event that is about the turn rather than in it."""
    if kind == "compacting":
        used, window = event.get("used") or 0, event.get("window") or 0
        return f"folding context ({used:,} of {window:,})" if window else "folding context"
    if kind == "retrying":
        why = " ".join(str(event.get("message") or "").split())[:60]
        return f"retrying (attempt {event.get('attempt')}){': ' + why if why else ''}"
    if kind == "steered":
        return f"steered: {' '.join(str(event.get('text') or '').split())[:70]}"
    if kind == "directive":
        return f"· {' '.join(str(event.get('text') or '').split())[:70]}"
    if kind == "waiting_on_errands":
        count = int(event.get("count") or 0)
        return f"waiting on {count} errand{'s' if count != 1 else ''}"
    if kind == "errand_back":
        return f"errand reported ({len(str(event.get('text') or ''))} chars)"
    return kind


def _render_question(arguments: Any, note, style: Style, conversation: str) -> None:
    """The question he is waiting on, with the command that answers it.

    The command is printed with the conversation already filled in, because the session that
    has to answer is usually not the one looking at this: he asks in a chat opened this
    morning and you read it this evening, in another directory. A reader who has to work out
    the id first is a reader who closes the terminal.
    """
    note(style.bold("  ? he is waiting on you"))
    for question in arguments.get("questions") or [] if isinstance(arguments, dict) else []:
        note("    " + str(question.get("question") or ""))
        for index, option in enumerate(question.get("options") or [], start=1):
            blurb = str(option.get("description") or "")
            note(
                style.dim(f"      {index}. {option.get('label')}")
                + (style.dim("  — " + blurb) if blurb else "")
            )
    where = f" -c {conversation}" if conversation else ""
    note(style.dim(f"    kith answer{where} <number|text>   ·   kith answer{where} --skip"))


def passthrough(events: Iterator[tuple[str, dict]], out: TextIO | None = None) -> tuple[int, str]:
    """Write the server's bytes, unchanged, one line at a time.

    Flushed per line rather than per buffer: the caller is a program waiting on a pipe, and a
    4KB buffer turns a live stream into a series of lumps arriving at buffer boundaries — which
    is the exact failure ``X-Accel-Buffering`` exists to prevent one layer up. Undoing it here
    would be a peculiar way to lose an argument the server already won.
    """
    stream: TextIO = out or sys.stdout
    conversation = ""
    code = 0
    finished = False
    for raw, event in events:
        stream.write(raw)
        stream.flush()
        kind = event.get("type")
        if kind == "conversation":
            conversation = str(event.get("id") or "")
        elif kind == "error":
            code = 1
        elif kind == "done":
            finished = True
    if not finished and code == 0:
        # Same rule as `human`, and it matters more here: the caller is a program, the bytes
        # it got are valid JSON either way, and the exit code is the only thing that can tell
        # it the turn was cut off.
        code = 1
    return code, conversation


def human(
    events: Iterator[tuple[str, dict]],
    out: TextIO | None = None,
    err: TextIO | None = None,
    *,
    quiet: bool = False,
) -> tuple[int, str]:
    """Render a turn for a person watching it happen.

    Returns the exit code and the conversation id, because both are decided by what came over
    the stream and the caller needs each — one to exit with, one to remember.
    """
    answer: TextIO = out or sys.stdout
    aside: TextIO = err or sys.stderr
    style = Style(_styling(aside))

    conversation = ""
    code = 0
    wrote_prose = False
    finished = False
    in_reasoning = False
    tokens_in = tokens_out = 0
    tools = 0
    context_note = ""

    def note(text: str) -> None:
        aside.write(text + "\n")
        aside.flush()

    for _raw, event in events:
        kind = event.get("type")

        if kind == "conversation":
            conversation = str(event.get("id") or "")
            note(style.dim(f"→ {conversation}"))

        elif kind == "delta":
            text = str(event.get("text") or "")
            if not text:
                continue
            if event.get("role") == "reasoning":
                if quiet:
                    continue
                # Reasoning is shown but never mixed into the answer. Kept on stderr and
                # dimmed, so watching a long turn still tells you it is thinking about the
                # right thing, and redirecting still gives you only what he said.
                if not in_reasoning:
                    aside.write(style.dim("  "))
                    in_reasoning = True
                aside.write(style.dim(text))
                aside.flush()
            else:
                if in_reasoning:
                    aside.write("\n")
                    aside.flush()
                    in_reasoning = False
                answer.write(text)
                answer.flush()
                wrote_prose = True

        elif kind == "tool_call":
            tools += 1
            if str(event.get("name") or "") == "ask":
                # Shown in full, and shown even under --quiet, because it is the one tool call
                # that stops being information and becomes a thing you have to do. It parks the
                # turn for fifteen minutes, and from a terminal it used to be a `· ask` line
                # followed by silence — indistinguishable from a turn that had simply gone slow.
                if in_reasoning:
                    aside.write("\n")
                    in_reasoning = False
                if wrote_prose:
                    # Prose is on stdout and this is on stderr, so a terminal showing both
                    # interleaves them with no break: a real transcript read "…which I'll ask
                    # about:  ? he is waiting on you". Only the visible stream needs the break.
                    answer.write("\n")
                    answer.flush()
                _render_question(event.get("arguments"), note, style, conversation)
                continue
            if quiet:
                continue
            if in_reasoning:
                aside.write("\n")
                in_reasoning = False
            detail = _summarise(event.get("arguments"))
            name = str(event.get("name") or "?")
            note(style.dim(f"  · {name}{'  ' + detail if detail else ''}"))

        elif kind == "tool_result":
            # Only the failures. A successful result is routinely a whole file or a thousand
            # lines of search output — it is the *input* to the next round, not something the
            # person watching needs — but a tool that refused is why the next thing he says
            # may be strange, and silently swallowing it makes the turn unreadable.
            result = event.get("result")
            problem = result.get("error") if isinstance(result, dict) else None
            if problem and not quiet:
                if in_reasoning:
                    aside.write("\n")
                    in_reasoning = False
                note(
                    style.dim(f"    ↳ {event.get('name')}: ") + style.red(" ".join(str(problem).split())[:96])
                )

        elif kind in ("compacting", "retrying", "steered", "directive", "waiting_on_errands", "errand_back"):
            # The turn changing shape. Every one of these is the answer to "why has nothing
            # happened for thirty seconds" — a fold, a retry after a provider hiccup, a pause
            # for sub-agents — and a client that drops them shows a turn that appears to hang.
            # On stderr with everything else, so they never contaminate the answer.
            if quiet:
                continue
            if in_reasoning:
                aside.write("\n")
                in_reasoning = False
            note(style.dim("  " + _aside(kind, event)))

        elif kind == "context":
            # Fired once, before the first token, carrying what is already in the window. Kept
            # for the closing line rather than printed on arrival: at the top of a turn it is
            # a number nobody has a use for yet, and at the bottom it is the answer to "how
            # much room is left".
            window = event.get("context") or {}
            used, size = int(window.get("used") or 0), int(window.get("window") or 0)
            if size:
                context_note = f"context {used:,}/{size:,} ({used / size:.0%})"

        elif kind == "stats":
            stats = event.get("stats") or {}
            # `promptTokens`/`responseTokens` are the transport's own names — see
            # `llm.ollama._stats_from_done`, which the cloud transport mirrors key for key.
            # Summed rather than replaced: the event fires once per *model request*, and a
            # turn that calls tools makes several, so reading only the last one reports the
            # cost of the final round as the cost of the turn.
            tokens_in += int(stats.get("promptTokens") or 0)
            tokens_out += int(stats.get("responseTokens") or 0)

        elif kind == "error":
            if in_reasoning:
                aside.write("\n")
                in_reasoning = False
            note(style.red(f"  ! {event.get('message') or 'the turn failed'}"))
            code = 1

        elif kind == "done":
            finished = True
            break

    if in_reasoning:
        aside.write("\n")
    if not finished and code == 0:
        # The stream ended without saying it was done.
        #
        # Not the same as a turn that failed: nothing errored, and whatever arrived is real.
        # It is the shape a *killed* turn has — the dev server's reloader restarting under a
        # streaming response, a crash, a dropped connection. Found exactly that way, with the
        # agent editing his own source while a turn was streaming: two turns stopped mid-word
        # and this function returned 0, so nothing could tell a truncated answer from a
        # complete one. For a command another agent reads the exit code of, reporting a
        # half-answer as success is the worst failure available.
        note(style.red("  ! the stream ended mid-turn — this answer is incomplete"))
        note(style.dim("    the turn may still be running: kith attach"))
        code = 1
    if wrote_prose:
        # The answer rarely ends in a newline and a shell prompt landing mid-sentence reads
        # as truncated output. Added here rather than by the server, which is streaming text
        # and should not be inventing whitespace for one of its clients.
        answer.write("\n")
        answer.flush()
    if not quiet and (tools or tokens_in or tokens_out or context_note):
        parts = []
        if tools:
            parts.append(f"{tools} tool{'s' if tools != 1 else ''}")
        if tokens_in or tokens_out:
            parts.append(f"{tokens_in:,} in · {tokens_out:,} out")
        if context_note:
            parts.append(context_note)
        note(style.dim("  " + " · ".join(parts)))

    return code, conversation


# --------------------------------------------------------------------------- #
# Tables and JSON, for the commands that are not a turn
# --------------------------------------------------------------------------- #


def emit_json(value: Any, out: TextIO | None = None) -> None:
    stream: TextIO = out or sys.stdout
    json.dump(value, stream, indent=2, ensure_ascii=False)
    stream.write("\n")
    stream.flush()


def table(rows: list[list[str]], headers: list[str] | None = None, out: TextIO | None = None) -> None:
    """A plain aligned table. No box drawing, because this gets grepped and cut."""
    stream: TextIO = out or sys.stdout
    if not rows:
        return
    body = ([headers] if headers else []) + rows
    widths = [max(len(str(row[column])) for row in body) for column in range(len(body[0]))]
    style = Style(_styling(stream))
    if headers:
        stream.write(
            style.dim("  ".join(head.ljust(widths[i]) for i, head in enumerate(headers)).rstrip()) + "\n"
        )
    for row in rows:
        stream.write("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip() + "\n")
    stream.flush()
