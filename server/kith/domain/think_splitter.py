"""Separate a model's reasoning from its answer.

Ollama returns reasoning in a dedicated ``thinking`` field when thinking is on,
so the answer stream is normally clean. This is the safety net for the rare case
where reasoning still leaks inline as ``<think>...</think>`` tags: it routes that
text to the reasoning channel so the answer stays clean. It copes with an
*implicit* leading ``</think>`` (reasoning with no opening tag) and with tags
split across streaming chunks.
"""

from __future__ import annotations

# (channel, text) where channel is "reasoning" or "text".
Piece = tuple[str, str]

_OPEN = "<think>"
_CLOSE = "</think>"


class ThinkSplitter:
    def __init__(self) -> None:
        self._in_think = False
        self._carry = ""

    def push(self, text: str) -> list[Piece]:
        """Feed a chunk of content; return any resolved (channel, text) pieces."""
        self._carry += text
        out: list[Piece] = []

        while self._carry:
            if self._in_think:
                idx = self._carry.find(_CLOSE)
                if idx == -1:
                    safe = self._safe_prefix(self._carry, [_CLOSE])
                    if safe:
                        out.append(("reasoning", safe))
                    self._carry = self._carry[len(safe) :]
                    break
                if idx > 0:
                    out.append(("reasoning", self._carry[:idx]))
                self._carry = self._carry[idx + len(_CLOSE) :]
                self._in_think = False
                continue

            open_idx = self._carry.find(_OPEN)
            close_idx = self._carry.find(_CLOSE)

            if open_idx == -1 and close_idx == -1:
                safe = self._safe_prefix(self._carry, [_OPEN, _CLOSE])
                if safe:
                    out.append(("text", safe))
                self._carry = self._carry[len(safe) :]
                break

            # A close before any open means reasoning started implicitly, so the
            # preceding text is reasoning, not answer.
            take_close = open_idx == -1 or (close_idx != -1 and close_idx < open_idx)
            if take_close:
                if close_idx > 0:
                    out.append(("reasoning", self._carry[:close_idx]))
                self._carry = self._carry[close_idx + len(_CLOSE) :]
            else:
                if open_idx > 0:
                    out.append(("text", self._carry[:open_idx]))
                self._carry = self._carry[open_idx + len(_OPEN) :]
                self._in_think = True

        return out

    def flush(self) -> Piece | None:
        """Emit any buffered remainder at end of stream."""
        if not self._carry:
            return None
        piece: Piece = ("reasoning" if self._in_think else "text", self._carry)
        self._carry = ""
        return piece

    @staticmethod
    def _safe_prefix(text: str, tags: list[str]) -> str:
        """Longest prefix safe to emit, holding back a suffix that could be the
        partial start of any tag straddling a chunk boundary."""
        cut = len(text)
        for tag in tags:
            # `len(text)`, not `cut`: a shorter cut taken for an earlier tag must not limit how
            # far back this one is allowed to look for its own partial suffix.
            for n in range(min(len(text), len(tag) - 1), 0, -1):
                if tag.startswith(text[len(text) - n :]):
                    cut = min(cut, len(text) - n)
                    break
        return text[:cut]
