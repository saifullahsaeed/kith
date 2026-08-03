"""Streaming client for Ollama's native chat API.

Owns the wire format: it POSTs to ``/api/chat`` with streaming enabled, parses
the newline-delimited JSON, splits reasoning from answer, and yields a flat
sequence of typed events for the HTTP layer to forward. All failure modes are
surfaced as ``error`` events rather than raised, so callers can just iterate.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import requests

from kith.config import Config
from kith.domain.think_splitter import ThinkSplitter

_NS_PER_SECOND = 1_000_000_000


def ollama_reachable(host: str) -> bool:
    """True if Ollama answers at ``host`` (used by the health check)."""
    try:
        return requests.get(f"{host}/api/tags", timeout=2).ok
    except requests.exceptions.RequestException:
        return False


def embed(host: str, text: str, model: str) -> list[float] | None:
    """Embed a piece of text into a vector, or None if Ollama can't be reached.

    Uses the native ``/api/embeddings`` endpoint. Callers treat a None result as
    "no vector available" and fall back to keyword search, so a missing embedding
    model never breaks recall.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        response = requests.post(
            f"{host}/api/embeddings", json={"model": model, "prompt": text}, timeout=(5, 60)
        )
        response.raise_for_status()
        vector = response.json().get("embedding")
    except (requests.exceptions.RequestException, ValueError):
        return None
    return vector if isinstance(vector, list) and vector else None


def stream_once(
    messages: list[dict[str, Any]],
    config: Config,
    host: str,
    tools: list[dict] | None = None,
) -> Iterator[dict]:
    """Stream a single chat turn, yielding events:

    - ``{"type": "delta", "role": "reasoning"|"text", "text": str}`` — live tokens
    - ``{"type": "turn", "content": str, "tool_calls": list, "stats": {...}}`` —
      once, at the end: the finished answer text, any tool calls the model made,
      and timing. The caller (the agent loop) decides whether to run the tools
      and go again.
    - ``{"type": "error", "message": str}`` on any failure
    """
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "think": config.think,
        "options": {"num_ctx": config.num_ctx, "num_predict": config.num_predict},
    }
    if tools:
        payload["tools"] = tools

    try:
        # (connect timeout, read timeout) — no read timeout while streaming.
        response = requests.post(
            # read timeout = max gap between streamed chunks, so a stalled stream
            # aborts (and roaming recovers) instead of hanging forever.
            f"{host}/api/chat",
            json=payload,
            stream=True,
            timeout=(5, 120),
        )
    except requests.exceptions.RequestException:
        yield {
            "type": "error",
            "message": f"Could not reach Ollama at {host}. Is it running? Try: ollama serve",
        }
        return

    if response.status_code != 200:
        detail = ""
        try:
            detail = response.text[:300]
        except requests.exceptions.RequestException:
            pass
        response.close()
        yield {
            "type": "error",
            "message": f"Ollama returned {response.status_code}" + (f": {detail}" if detail else ""),
        }
        return

    # Never let requests guess the charset of the stream — its fallback is
    # latin-1, which mangles every non-ASCII character it decodes.
    response.encoding = "utf-8"

    splitter = ThinkSplitter()
    tool_calls: list[dict] = []
    answer = ""
    try:
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue

            if chunk.get("error"):
                yield {"type": "error", "message": str(chunk["error"])}
                return

            message = chunk.get("message") or {}
            thinking = message.get("thinking")
            if thinking:
                yield {"type": "delta", "role": "reasoning", "text": thinking}
            content = message.get("content")
            if content:
                for channel, text in splitter.push(content):
                    if channel == "text":
                        answer += text
                    yield {"type": "delta", "role": channel, "text": text}
            if message.get("tool_calls"):
                tool_calls.extend(message["tool_calls"])

            if chunk.get("done"):
                tail = splitter.flush()
                if tail:
                    if tail[0] == "text":
                        answer += tail[1]
                    yield {"type": "delta", "role": tail[0], "text": tail[1]}
                yield {
                    "type": "turn",
                    "content": answer,
                    "tool_calls": tool_calls,
                    "stats": _stats_from_done(chunk, config.model),
                }
                return
    except requests.exceptions.RequestException as exc:
        response.close()
        yield {"type": "error", "message": f"stream interrupted: {exc}"}
        return
    finally:
        response.close()


def _stats_from_done(chunk: dict, model: str = "") -> dict[str, float]:
    """Convert Ollama's nanosecond durations and counts into friendly stats."""
    eval_count = chunk.get("eval_count") or 0
    eval_duration = chunk.get("eval_duration") or 0
    tokens_per_second = eval_count / (eval_duration / _NS_PER_SECOND) if eval_duration else 0.0
    return {
        # Which model produced this row, so a transcript stays attributable across a
        # model switch — the same reason the cloud transport records it.
        "model": model or chunk.get("model") or "",
        "promptTokens": chunk.get("prompt_eval_count") or 0,
        "responseTokens": eval_count,
        "tokensPerSecond": round(tokens_per_second, 1),
        "totalSeconds": round((chunk.get("total_duration") or 0) / _NS_PER_SECOND, 2),
        "loadSeconds": round((chunk.get("load_duration") or 0) / _NS_PER_SECOND, 2),
    }
