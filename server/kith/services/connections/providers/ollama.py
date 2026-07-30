"""Local models through Ollama: free, private, nothing leaves the machine."""

from __future__ import annotations

import re
from typing import ClassVar

from kith.domain.connection import Connection, ModelInfo, ProviderKind
from kith.services.connections.providers.base import Provider, ProviderError

#: Below this many billion parameters, a model handles multi-step tool use badly. A
#: warning, never a block — it is the user's machine and their call.
SMALL_MODEL_BILLIONS = 5.0

#: Parameter counts out of an Ollama tag: ``qwen3:4b``, ``deepseek-r1:1.5b``,
#: ``qwen3:30b-a3b``, ``gemma3:270m``. The lookbehind is the whole point — matching
#: ``"4b"`` as a substring makes ``qwen3:14b`` look like a 4B model, and warning
#: someone that the large model they just chose is too small is worse than saying
#: nothing at all.
_PARAM_SIZE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)([bm])\b", re.IGNORECASE)


class OllamaProvider(Provider):
    kind: ClassVar[ProviderKind] = ProviderKind.OLLAMA
    label: ClassVar[str] = "Local"
    blurb: ClassVar[str] = "Runs on your machine through Ollama. Free and private."
    requires: ClassVar[str] = "Ollama running, with a model pulled"
    tradeoff: ClassVar[str] = (
        "Nothing leaves your computer and it costs nothing. Small local models are "
        "noticeably worse at long, multi-step work — he'll lose track more often."
    )
    signup_url: ClassVar[str] = "https://ollama.com/download"
    #: There is no key to wait for; listing is the reachability check.
    lists_without_key: ClassVar[bool] = True

    def list_models(self, connection: Connection) -> list[ModelInfo]:
        """What is pulled locally, via Ollama's own API rather than its OpenAI shim.

        Embedding models are filtered out: they cannot hold a conversation, and
        offering ``nomic-embed-text`` as a brain would be an easy, confusing mistake
        — it is almost always present, because semantic recall needs it.
        """
        payload = self._get_json(f"{connection.endpoint}/api/tags")
        models = []
        for entry in payload.get("models") or []:
            name = entry.get("name")
            if not name or _is_embedding_model(name):
                continue
            models.append(
                ModelInfo(
                    id=name,
                    name=name,
                    prompt_per_mtok=0.0,
                    completion_per_mtok=0.0,
                    # Ollama reports neither context nor tool support; size is the
                    # honest proxy, surfaced as a warning by the manager.
                    supports_tools=None,
                )
            )
        if not models:
            raise ProviderError("Ollama is running but has no chat models pulled. Try: ollama pull qwen3:8b")
        return models

    def pulled_tags(self, connection: Connection) -> list[str]:
        """Every tag Ollama has, including the embedding models ``list_models`` hides.

        Exists because semantic recall depends on precisely those hidden entries, and
        the readiness check would otherwise have to make its own HTTP call — putting
        a second, slightly different idea of "where Ollama lives" in the codebase.
        """
        payload = self._get_json(f"{connection.endpoint}/api/tags")
        return [entry["name"] for entry in payload.get("models") or [] if entry.get("name")]

    def has_model(self, connection: Connection, model: str) -> bool:
        """Is a model pulled? Matches by prefix, since tags carry a ``:latest`` suffix."""
        return any(tag == model or tag.startswith(f"{model}:") for tag in self.pulled_tags(connection))


def _is_embedding_model(name: str) -> bool:
    return "embed" in name.lower()


def is_small(model_id: str) -> bool:
    """Is this model too small to be trusted with multi-step work?

    A tag can carry two sizes — ``qwen3:30b-a3b`` is 30B total with 3B active per
    token — and the larger one is what tracks how well it reasons, so that is the one
    judged. An unsized tag is never called small: guessing would put a warning in
    front of someone who chose well.
    """
    sizes = [
        float(count) if unit.lower() == "b" else float(count) / 1000
        for count, unit in _PARAM_SIZE.findall(model_id)
    ]
    return bool(sizes) and max(sizes) < SMALL_MODEL_BILLIONS


def is_cloud(model_id: str) -> bool:
    """Ollama's hosted models, which appear in ``/api/tags`` beside the local ones.

    They work, but they are not local: they need an Ollama account and the request
    leaves the machine. Since this provider's whole promise is "nothing leaves your
    computer", they must never be what onboarding suggests first.
    """
    return model_id.lower().endswith(":cloud")
