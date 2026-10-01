"""The Sources screen shows how many passages he keeps a source as — the unit recall works in.

The panel's list was metadata only (title, origin, size), so the reader could number passages
but the list could not say how many there were without fetching every source in full.
"""

from __future__ import annotations

from kith.infra.db import repositories as repo
from kith.services import sources


def test_the_list_counts_each_sources_passages(db, monkeypatch):
    from kith.services import embeddings

    monkeypatch.setattr(embeddings, "embed", lambda text: None)
    made = sources.ingest_text(db, "first paragraph.\n\n" * 400, "long notes")
    short = sources.ingest_text(db, "one line", "short")

    rows = {row["id"]: row for row in repo.sources.list_sources(db)}

    assert rows[made["id"]]["passages"] > 1
    assert rows[short["id"]]["passages"] == 1
