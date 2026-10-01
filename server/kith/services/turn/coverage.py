"""Which lines of a file read the turn is already holding, unchanged — so they go in once.

Measured Sep 13-27 across the transcripts in ``data/conversations``: 27% of `read_file` path-reads
(533 of 1,982) repeated a path already read in the same turn, and one of them was byte-identical,
which is the only kind the insert-time dedupe in `history._already_in` can see. 60% of reads ask
for a window (`offset`/`limit`/`symbol`), and a window of a file already opened is a different
result even where it overlaps.

**Most of those repeats are not duplicates, and this is the part that is.** Replayed through this
module, line against line: 144 of the 533 returned no lines (an error, a picture, a missing file);
204 were a new slice sharing no line with anything read before — paging through the file, with
nothing in them to leave out; 42 overlapped only lines that had changed since. 26 were entirely
lines already held and 110 partly. This leaves out 25 of the 26 whole and part of 32 of the 110:
104,000 of the 119,000 characters that were genuinely there twice, 1.7% of all read output. A
repeated path costs a round far more often than it costs a duplicate, and no insert-time rule can
recover a round.

**The unit that repeats is the line, not the result.** `read_file` numbers every line it returns
(``f"{n:6d}\\t{text}"``, from `infra/workspace/files.read_file`), so an earlier result is a set of
(file, line number, text) facts, and a new result can be checked against them one line at a time.
A line whose number *and* text are already in the turn carries nothing new, and a run of them is
replaced by a note saying where they are. Lines the turn does not hold go in as they came.

**Text, not paths or mtimes, is what makes it safe.** The comparison is of the rendered line
exactly as it is about to be sent, so a file that changed cannot be mistaken for one that did not:
an edited line differs, and an edit that moves everything below it moves every number too. What
survives an edit is precisely the part of the file the edit did not touch, and that part really is
unchanged. There is no stamp to trust and nothing that can drift out of date — the claim "these
lines are above" is checked against the lines that are above.

**Every copy has to agree.** A line is only left out when every whole copy of it in the turn says
the same thing. If the file changed between two earlier reads, the turn holds two versions of that
line, and a note saying "as you read it earlier" would not say which — so that line goes in again.
That is also the part of a file he changed himself, which is the last thing to collapse.

**Only what is still whole counts.** A result that has been stubbed (`_compact_tool_history`) no longer
holds its lines: the stub is cut mid-JSON and does not parse, so it is skipped by construction rather than by a flag someone has to remember to check. Something
that is whole now can stop being whole later in the turn — see `history._settle_pointers`, which
is why the decision here has to be a pure function of the messages in front of it.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from typing import Any

from kith.domain.tooling import many

#: The one tool whose output is a file, line by line.
READ = "read_file"

#: One line as `workspace.read_file` renders it: the number right-aligned in six columns, a tab,
#: then the text. Matched from the start of a segment, so a tab inside the text cannot confuse it.
_NUMBERED = re.compile(r" *(\d+)\t")

#: Below this a duplicate is not worth replacing — the pointer is not free either, and a short
#: result carries its own answer. For a run of lines the same holds: sending him to another
#: message for two short lines is a worse trade than the two lines. One figure for both this and
#: `history._already_in`, so the two ways of noticing a repeat agree about what is too small.
MIN_CHARS = 400

#: Where the copies of a line disagree — the file changed between two earlier reads. `None`, so it
#: reads exactly like a line the turn does not hold at all: either way, the line goes in. Tested as
#: `is None` rather than `is _DISAGREE`, which is the same test but one pyright cannot narrow on.
_DISAGREE = None


def reduced(convo: list[dict[str, Any]], name: str, payload: str) -> str | None:
    """``payload`` with every run of lines ``convo`` already holds replaced by a note, or None.

    None means "send it as it came": not a read, not a shape this understands, or nothing in it
    worth leaving out. Only ever the *result being added* is rewritten — ``convo`` is read and
    never touched, so the history stays append-only, which is the property the prompt cache needs
    (see `history._already_in`).

    ``convo`` is the conversation this result is about to be appended to. The call it answers is
    found in it positionally, the same way `openai_compat._to_openai` pairs a tool message to its
    call on the wire — so this and the provider cannot disagree about which call a result belongs
    to, and a result whose call cannot be found is left alone.
    """
    if name != READ:
        return None
    reads, pending = _reads(convo)
    paths = _paths_of(pending)
    envelope = _envelope(payload)
    if not paths or envelope is None:
        return None
    wanted = {_key(path) for path in paths}
    shown = _shown([(message, their) for message, their in reads if wanted & {_key(p) for p in their}])
    if not shown:
        return None

    out: list[str] = []
    run: list[tuple[int, str]] = []
    run_where = ("", "")  # (file, which earlier read) the current run is drawn from
    left_out = False

    def flush() -> None:
        nonlocal left_out
        if run and sum(len(segment) + 1 for _, segment in run) >= MIN_CHARS:
            out.append(_note(run[0][0], run[-1][0], run_where[1]))
            left_out = True
        else:
            out.extend(segment for _, segment in run)
        run.clear()

    for path, number, segment in _segments(envelope["result"], paths):
        held = shown.get(_key(path), {}).get(number) if path else _DISAGREE
        if held is None or held[0] != segment:
            flush()
            out.append(segment)
            continue
        where = (path, held[1])
        if run and (where != run_where or number != run[-1][0] + 1):
            flush()
        run_where = where
        run.append((number, segment))
    flush()

    if not left_out:
        return None
    return json.dumps({**envelope, "result": "\n".join(out)})


def _note(first: int, last: int, where: str) -> str:
    """What stands in for a run of lines. No counts that could change, nothing but the facts.

    Worded as the thing it is — a place to look, not a loss. A note that reads like something was
    taken away invites him to read the file again to get it back, which is how a 1,200-character
    stub once turned into fifteen full reads (`history._tool_result_message`).
    """
    lines = f"line {first}" if first == last else f"lines {first}-{last}"
    return (
        f"[{lines} unchanged since your earlier read_file of {where} — still above, in full. "
        "No need to read them again.]"
    )


def _reads(convo: list[dict[str, Any]]) -> tuple[list[tuple[dict, list[str]]], dict | None]:
    """Every whole `read_file` result in ``convo`` with the paths it was asked for, and the call
    the next tool message will answer.

    One forward walk, pairing each tool message to its call by position — the k-th tool message
    after an assistant message answers that message's k-th call. Anything between them that is not
    a tool message (the picture a read put in front of him, a steer) does not take a slot, because
    it does not take one on the wire either.
    """
    reads: list[tuple[dict, list[str]]] = []
    calls: list[dict] = []
    answered = 0
    for message in convo:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls, answered = list(message["tool_calls"]), 0
        elif role == "tool":
            call = calls[answered] if answered < len(calls) else None
            answered += 1
            if message.get("tool_name") == READ and not message.get("_stubbed") and call:
                paths = _paths_of(call)
                if paths:
                    reads.append((message, paths))
    pending = calls[answered] if answered < len(calls) else None
    return reads, pending


def _paths_of(call: dict | None) -> list[str]:
    """The files one `read_file` call asked for, read the way the tool itself reads them."""
    function = (call or {}).get("function") or {}
    if function.get("name") != READ:
        return []
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            return []
    return many(arguments, "paths", "path") if isinstance(arguments, dict) else []


def _envelope(payload: object) -> dict | None:
    """The result envelope, when it carries text — `run_tool`'s ``{"ok": true, "result": "..."}``.

    A dict ``result`` is a picture or a batch with pictures in it, and an error is an error; neither
    has lines to compare, so both are declined here rather than half-handled further down.
    """
    if not isinstance(payload, str):
        return None
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("ok") is not True or not isinstance(data.get("result"), str):
        return None
    return data


def _segments(text: str, paths: list[str]) -> Iterator[tuple[str, int, str]]:
    """Each line of a result as ``(file, line number, segment)``, with ``("", 0, segment)`` for
    anything that is not a numbered line of a file — a heading, the "showing lines" trailer, an
    error for one file of a batch.

    A read of one file is one section with no heading. A read of several is `_read_several`'s
    shape, each file under ``===== <path> =====`` in the order asked; the headings are matched
    against the paths *in that order*, so a line in some file that happens to look like one cannot
    start a section.
    """
    several = len(paths) > 1
    upcoming = list(paths) if several else []
    current = "" if several else paths[0]
    for segment in text.split("\n"):
        if upcoming and segment == f"===== {upcoming[0]} =====":
            current = upcoming.pop(0)
            yield "", 0, segment
            continue
        found = _NUMBERED.match(segment) if current else None
        if found:
            yield current, int(found[1]), segment
        else:
            yield "", 0, segment


def _shown(reads: list[tuple[dict, list[str]]]) -> dict[str, dict[int, tuple[str, str] | None]]:
    """What the turn is holding of each file: ``{file: {line: (segment, where) | _DISAGREE}}``.

    ``where`` names the newest read holding that line, by the window it covers — the thing he
    would recognise, "lines 1-120". Newest because it is the copy most likely to still be whole
    by the time he looks, and because with every copy agreeing, any of them is right.
    """
    shown: dict[str, dict[int, tuple[str, str] | None]] = {}
    for message, paths in reads:
        envelope = _envelope(message.get("content"))
        if envelope is None:
            continue
        by_file: dict[str, list[tuple[int, str]]] = {}
        for path, number, segment in _segments(envelope["result"], paths):
            if path:
                by_file.setdefault(path, []).append((number, segment))
        for path, lines in by_file.items():
            numbers = [number for number, _ in lines]
            low, high = min(numbers), max(numbers)
            where = f"line {low}" if low == high else f"lines {low}-{high}"
            held = shown.setdefault(_key(path), {})
            for number, segment in lines:
                before = held.get(number, (segment, where))
                agrees = before is not None and before[0] == segment
                held[number] = (segment, where) if agrees else _DISAGREE
    return shown


def _key(path: str) -> str:
    """The same file spelled the same way: ``./a/../b.py`` and ``b.py`` are one file.

    Not resolved against the workspace. That would need the session's base folder, and a pure
    function of the messages is what lets `history._settle_pointers` re-ask this question later
    and get the same answer. A file spelled once relative and once absolute is simply not matched,
    which costs a repeat and never a wrong answer.
    """
    return os.path.normpath(path)
