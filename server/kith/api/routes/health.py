"""Liveness."""

from __future__ import annotations

from kith import settings
from kith.api.blueprint import api
from kith.config import ollama_host
from kith.llm.ollama import ollama_reachable
from kith.schemas import (
    HealthSchema,
)


@api.get("/health")
@api.output(HealthSchema)
@api.doc(
    summary="Liveness check",
    description=(
        "Reports that the backend is up, whether Ollama responds, and which data directory "
        "this server is using — the last so a CLI can find the API token belonging to *this* "
        "server rather than to another install on the same machine."
    ),
)
def health():
    return {
        "ok": True,
        "ollamaReachable": ollama_reachable(ollama_host()),
        "dataDir": str(settings.DATA_DIR),
    }
