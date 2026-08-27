"""Removing tool calls that a model wrote as prose instead of calling.

A model part-way through a tool-using turn, told to stop, sometimes keeps producing
calls in whatever pseudo-markup it was trained on::

    <FUNCTION>web_search(query="Pakistani schools in Riyadh", max_results=10)</FUNCTION>
    <function=web_search> <parameter=query> Pakistani schools </parameter> </function>

Nothing executes it — it arrives in the content channel, not as a tool call — so it is
noise. Worse than noise: it is indistinguishable from an answer, so it reaches the
transcript and gets stored in his journal and deliverables as if he had written it.

The real fix is upstream: send the tool schemas and forbid them with
``tool_choice="none"`` rather than withholding the schemas, which is what provokes this.
This is the second line of defence, for a model that does it anyway.

Deliberately narrow. Kith writes code and prose about code, so a filter that ate angle
brackets in general would be worse than the problem — only these specific wrappers go.
"""

from __future__ import annotations

import re

#: Whole blocks, wrapper and contents together. The contents are a call someone meant
#: to make, not a sentence, so keeping them would leave `web_search(query="…")` sitting
#: in the middle of an answer.
_BLOCKS = re.compile(
    r"""
      <FUNCTION>.*?</FUNCTION>          # <FUNCTION>web_search(...)</FUNCTION>
    | <function=[^>]*>.*?</function>    # <function=web_search> … </function>
    | <tool_call>.*?</tool_call>
    """,
    re.VERBOSE | re.DOTALL | re.IGNORECASE,
)

#: Orphans, left when a model opens a wrapper and never closes it — or closes one it
#: never opened, which is what the observed output actually did.
_ORPHANS = re.compile(
    r"</?(?:FUNCTION|function=[^>]*|function|parameter=[^>]*|parameter|tool_call)>",
    re.IGNORECASE,
)

#: A wrapper opening. Seeing one means suppress until a closer arrives.
_OPENER = re.compile(r"<(?:FUNCTION|function=[^>]*|tool_call)>", re.IGNORECASE)

#: Any wrapper closing. Deliberately not matched to the opener that started the block:
#: a model producing this markup at all is being sloppy, and demanding well-formed
#: nesting from it would mean suppressing the rest of the answer when it isn't.
_CLOSER = re.compile(r"</(?:FUNCTION|function|tool_call)>", re.IGNORECASE)

#: Tags that only ever appear inside a block, dropped on their own in case one escapes.
_INNER = re.compile(r"</?parameter(?:=[^>]*)?>", re.IGNORECASE)


#: The longest wrapper opener, and therefore how much tail has to be held back while
#: streaming in case a tag is split across two deltas.
_MAX_TAG = 64


def strip_tool_markup(text: str) -> str:
    """Remove narrated tool calls from a complete piece of text."""
    cleaned = _BLOCKS.sub("", text)
    # An opener with no closer runs to the end — the model started a call and never
    # finished, so nothing after it is prose either. Done before the orphan pass, which
    # would otherwise strip the opener and leave the call's arguments looking like text.
    unclosed = _OPENER.search(cleaned)
    if unclosed:
        cleaned = cleaned[: unclosed.start()]
    cleaned = _ORPHANS.sub("", cleaned)
    # Collapse the blank space a removed block leaves behind, without reflowing prose.
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


class ToolMarkupFilter:
    """The same scrub over a stream, where a tag can straddle two chunks.

    A state machine rather than the regex above, because incremental regex gets this
    wrong in a way that is worse than not filtering: fed one character at a time, an
    opener becomes a complete tag — and gets removed as a stray — before its closer has
    arrived, so the call's *contents* then stream straight through as prose.

    So: suppress from an opener until a closer, and hold back any trailing fragment that
    could still turn out to be either.
    """

    def __init__(self) -> None:
        self._held = ""
        self._suppressing = False

    def feed(self, chunk: str) -> str:
        """Emit what can be decided now; keep the rest until more arrives."""
        # Cleared the moment it is consumed into the buffer: every path below either
        # sets it again or means there is nothing to hold, and leaving a stale value
        # here re-prepends markup that was already dealt with.
        buffer, self._held = self._held + chunk, ""
        out: list[str] = []

        while buffer:
            if self._suppressing:
                closer = _CLOSER.search(buffer)
                if not closer:
                    # Keep only enough to recognise a closer split across chunks.
                    self._held = buffer[-_MAX_TAG:]
                    return "".join(out)
                buffer = buffer[closer.end() :]
                self._suppressing = False
                continue

            opener = _OPENER.search(buffer)
            if opener:
                out.append(buffer[: opener.start()])
                buffer = buffer[opener.end() :]
                self._suppressing = True
                continue

            # No complete tag. Pass everything up to a trailing '<' that might grow
            # into one; anything earlier is settled.
            cut = buffer.rfind("<")
            if cut != -1 and len(buffer) - cut <= _MAX_TAG:
                out.append(buffer[:cut])
                self._held = buffer[cut:]
            else:
                out.append(buffer)
                self._held = ""
            break

        return _INNER.sub("", "".join(out))

    def flush(self) -> str:
        """Whatever is still held, scrubbed — call once the stream ends."""
        remaining, self._held = self._held, ""
        # Reset with the buffer. Leaving it set meant a filter fed again after a flush
        # suppressed everything from the first character, having been told the stream ended.
        was_suppressing, self._suppressing = self._suppressing, False
        if was_suppressing:
            # An unclosed block runs to the end of the answer; none of it is prose.
            return ""
        return strip_tool_markup(remaining)
