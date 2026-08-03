"""Folding the old part of a long conversation into a running brief.

History is replayed in full on every turn — only the prose (``conversations.messages``
drops the tool middle), but all of it — so a conversation that runs for days grows a prompt
without bound. This folds everything but the most recent turns into a short brief and keeps
the recent ones verbatim, which is how the field bounds the same axis (Claude's compaction,
Cursor's self-summarisation).

**Rolling, not per-turn.** Summarising a growing history on every turn would cost a model
call each time and defeat the point. So a brief, once made, covers the first *N* turns and is
reused as long as only a handful of turns have accrued since; only when enough new turns pile
up is it regenerated, folding the previous brief forward.

**Never fatal.** The summariser is a model call and model calls fail. If it returns nothing,
the full history is replayed exactly as before — a large prompt is a worse turn, a broken
summariser must not be a broken one.

Pure but for the ``summarize`` callback, so the policy is testable without a real model. The
caller resolves the thresholds from settings, does the model call, and persists the returned
brief; see ``services.history_context`` / ``routes.chat``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

#: Marks the folded block so a reader — and the model — can tell a summary of the past from
#: something that was actually said in it.
_SUMMARY_HEADER = "[Summary of the earlier part of this conversation]"

#: Output budget for a brief. A fold is worthwhile only when the summary is far smaller than
#: what it replaces; a couple of thousand tokens is plenty for "what was decided and where we
#: are", and capping it stops a talkative model turning the summary into another transcript.
_SUMMARY_MAX_TOKENS = 1_600

#: How the model is asked to fold. Notes, not prose; keep the load-bearing facts, drop the
#: chatter; invent nothing — a summary that adds things is worse than a prompt that is merely
#: large.
_INSTRUCTION = (
    "You are compressing the earlier part of a conversation so it can be carried forward in "
    "less space. Write a compact brief — notes, not prose — that preserves: decisions made, "
    "facts and constraints established, files and identifiers referred to, unfinished threads, "
    "and what is currently being worked on. Leave out small talk and anything already "
    "superseded. Do not add, guess, or infer anything that is not in the text. Be concise."
)


def fold(
    history: list[dict[str, Any]], config, conversation_id: str, host: str = ""
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Compact ``history`` using the settings' thresholds and the conversation's stored brief.

    The impure companion to :func:`compact`: it reads the knobs, reads the last brief off the
    transcript, and supplies a real (fail-safe) summariser. Returns the same
    ``(messages, new_brief_or_None)`` — the caller persists a non-None brief.
    """
    from kith.services import conversations, tuning

    max_chars = int(tuning.value("history_max_chars"))
    keep_recent = int(tuning.value("history_keep_recent"))
    prior = conversations.latest_summary(conversation_id) if conversation_id else {}
    return compact(
        history,
        lambda text: _summarize(text, config, host),
        prior,
        max_chars=max_chars,
        keep_recent=keep_recent,
    )


def _summarize(text: str, config, host: str) -> str:
    """Ask the model for a brief. Returns "" on any failure — a fold must never break a turn.

    Reasoning off and a hard output cap: a summary does not need to think, and it must be much
    smaller than what it replaces or the fold is pointless. Uses the same transport the turn
    itself would (cloud when a key and endpoint are set, else local Ollama).
    """
    from kith.llm import ollama, openai_compat

    prompt = [{"role": "system", "content": _INSTRUCTION}, {"role": "user", "content": text}]
    slim = replace(config, think=False, effort="", num_predict=_SUMMARY_MAX_TOKENS)
    try:
        if slim.api_key and slim.base_url:
            stream = openai_compat.stream_once(prompt, slim, host, tools=None)
        else:
            stream = ollama.stream_once(prompt, slim, host, tools=None)
        parts: list[str] = []
        for event in stream:
            if event.get("type") == "error":
                return ""
            if event.get("type") == "delta" and event.get("role") == "text":
                parts.append(event.get("text") or "")
        return "".join(parts).strip()
    except Exception:
        return ""


def compact(
    history: list[dict[str, Any]],
    summarize: Callable[[str], str],
    prior: dict[str, Any] | None = None,
    *,
    max_chars: int,
    keep_recent: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Fold old turns into a brief. Returns ``(messages, new_brief_or_None)``.

    ``new_brief`` is ``{"through": n, "text": ...}`` when a fresh summary was made and should
    be persisted, or ``None`` when the history was left as-is or an existing brief was reused.
    """
    prior = prior or {}
    covered = int(prior.get("through") or 0)
    brief = str(prior.get("text") or "")
    count = len(history)

    prose = sum(len(m["content"]) for m in history if isinstance(m.get("content"), str))
    # Short, and never folded before — leave it byte-for-byte as it came in.
    if prose <= max_chars and not brief:
        return history, None

    # A brief already covers all but the last few turns: reuse it, no model call.
    if brief and 0 <= covered <= count and (count - covered) <= keep_recent:
        return [_summary_message(brief), *history[covered:]], None

    # (Re)fold everything but the most recent `keep_recent` turns.
    cut = max(0, count - keep_recent)
    text = _render(history[:cut])
    if brief:
        # Carry the previous brief forward rather than dropping what it captured.
        text = f"[Summary so far]\n{brief}\n\n[Continued]\n{text}"
    fresh = (summarize(text) or "").strip()
    if not fresh:
        return history, None  # summariser failed — a big prompt beats a broken turn
    return [_summary_message(fresh), *history[cut:]], {"through": cut, "text": fresh}


def _summary_message(brief: str) -> dict[str, Any]:
    return {"role": "system", "content": f"{_SUMMARY_HEADER}\n{brief}"}


def _render(messages: list[dict[str, Any]]) -> str:
    lines = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            lines.append(f"{message.get('role', '?')}: {content}")
    return "\n\n".join(lines)
