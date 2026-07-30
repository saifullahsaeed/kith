"""Liveness."""

from __future__ import annotations

from kith.api.blueprint import api
from kith.config import ollama_host
from kith.llm.ollama import ollama_reachable
from kith.schemas import (
    HealthSchema,
)


@api.get("/health")
@api.output(HealthSchema)
@api.doc(summary="Liveness check", description="Reports that the backend is up and whether Ollama responds.")
def health():
    return {"ok": True, "ollamaReachable": ollama_reachable(ollama_host())}
