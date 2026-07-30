"""Knowledge his person feeds him."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.config import AGENT_DB_PATH
from kith.infra.db import repositories as repo
from kith.tools import sources


@api.post("/sources")
@api.doc(
    summary="Feed Kith knowledge",
    description="Ingest a link (url) or pasted text so he can recall it. Body: {url} or {text}, optional {title}.",
)
def ingest_source():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    text = (body.get("text") or "").strip()
    title = body.get("title")
    try:
        if url:
            return jsonify(sources.ingest_url(AGENT_DB_PATH, url, title))
        if text:
            return jsonify(sources.ingest_text(AGENT_DB_PATH, text, title))
        return jsonify({"error": "give a url or text"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@api.get("/sources/<int:source_id>")
@api.doc(summary="Read a source", description="The full stored text of one ingested source.")
def read_source(source_id):
    return jsonify(repo.sources.get_source(AGENT_DB_PATH, source_id) or {})
