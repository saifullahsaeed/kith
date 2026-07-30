"""Knowledge his person feeds him.

Ingest a link or a chunk of text: fetch it, pull out the readable text, embed
it, and keep it so Kith can recall it by meaning later. Deliberately simple —
server-side fetch + a light HTML strip; no headless browser.
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

import requests

from kith.infra.db import repositories as repo
from kith.services import embeddings

_MAX_CONTENT = 20_000
_DROP_RE = re.compile(r"<(script|style|noscript|template)[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANKS_RE = re.compile(r"\n\s*\n\s*\n+")


def ingest_url(path: Path, url: str, title: str | None = None) -> dict:
    response = requests.get(url, timeout=(5, 30), headers={"User-Agent": "Kith/1.0 (+local)"})
    response.raise_for_status()
    # For text/* with no charset header requests falls back to latin-1, which
    # mangles every accent and dash. Trust what the page itself looks like.
    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or "utf-8"
    raw = response.text
    content = _extract(raw)
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        title = _html.unescape(match.group(1)).strip() if match else url
    return _store(path, title[:140] or url, url, content)


def ingest_text(path: Path, text: str, title: str | None = None) -> dict:
    content = (text or "").strip()[:_MAX_CONTENT]
    if not content:
        raise ValueError("nothing to ingest")
    heading = (title or content.splitlines()[0] or "Untitled note").strip()[:140]
    return _store(path, heading, "pasted", content)


def search(path: Path, query: str, limit: int = 5) -> list[dict]:
    vector = embeddings.embed(query)
    if not vector:
        return []
    # Retrieve the most relevant passages (chunks); fall back to whole-source match.
    chunks = repo.sources.search_source_chunks(path, vector, limit)
    return chunks or repo.sources.search_sources(path, vector, limit)


_CHUNK_SIZE = 1200
_CHUNK_OVERLAP = 150


def _store(path: Path, title: str, origin: str, content: str) -> dict:
    content = content[:_MAX_CONTENT]
    # A whole-source embedding (title + head) for coarse matching…
    source = repo.sources.add_source(
        path, title, origin, content, embeddings.embed(f"{title}\n\n{content[:4000]}")
    )
    # …plus per-chunk embeddings so long docs are retrievable passage-by-passage.
    for i, chunk in enumerate(_chunks(content)):
        repo.sources.add_source_chunk(path, source["id"], i, chunk, embeddings.embed(f"{title}: {chunk}"))
    return source


def _chunks(text: str) -> list[str]:
    """Split text into overlapping windows, preferring paragraph boundaries."""
    text = text.strip()
    if len(text) <= _CHUNK_SIZE:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = start + _CHUNK_SIZE
        window = text[start:end]
        # try to end on a paragraph/sentence boundary within the tail of the window
        if end < len(text):
            cut = max(window.rfind("\n\n"), window.rfind(". "))
            if cut > _CHUNK_SIZE // 2:
                window = window[: cut + 1]
                end = start + len(window)
        out.append(window.strip())
        start = end - _CHUNK_OVERLAP
    return [c for c in out if c]


def _extract(raw: str) -> str:
    text = _DROP_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f]+", " ", text)
    text = _BLANKS_RE.sub("\n\n", text)
    return text.strip()[:_MAX_CONTENT]
