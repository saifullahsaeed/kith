"""How Kith searches the web.

A separate type from ``Connection`` because it is a separate decision with a separate
failure mode — but not an independent one: OpenRouter's ``web`` plugin bills to the
same key he thinks with, so that option only exists when he thinks through OpenRouter.
Encoding that dependency here, once, is what keeps the UI from offering an option that
cannot work.

Pure: no network, no database, no config. `as_kind` is here because it is a constructor for
the enum above it and touches nothing else; the *names* those values are stored under are in
`settings.py`, with the two environment variables they mirror — which is where every other
config-store key in this tree lives, beside the thing that owns the setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kith.domain.connection import Connection


class SearchKind(StrEnum):
    """The genuinely different ways to search, not variations on one."""

    #: A SearXNG instance — yours or someone else's. Keyless and unmetered.
    SEARXNG = "searxng"
    #: OpenRouter's `web` plugin, billed to the key that already powers his thinking.
    OPENROUTER = "openrouter"
    #: No search. He can still read a page you hand him.
    NONE = "none"


def as_kind(raw: object) -> SearchKind | None:
    """Read a stored or env value as a kind, tolerating the older `searx` spelling.

    Returns None for blank or "auto", which is a real answer rather than a failure: it means
    "try them in order", and is what an unset preference looks like.
    """
    text = str(raw or "").strip().lower()
    if not text or text == "auto":
        return None
    if text == "searx":
        return SearchKind.SEARXNG
    try:
        return SearchKind(text)
    except ValueError:
        return None


#: Where a SearXNG instance usually lives. Part of what the option *means*, rather
#: than something an operator tunes, so it lives with the type.
DEFAULT_SEARX_URL = "http://127.0.0.1:8888"


@dataclass(frozen=True)
class SearchSetup:
    """A choice of search provider, and the address it needs."""

    kind: SearchKind
    #: Only meaningful for SearXNG. Blank means the default address.
    searx_url: str = ""

    @classmethod
    def searxng(cls, url: str = "") -> SearchSetup:
        return cls(kind=SearchKind.SEARXNG, searx_url=url)

    @classmethod
    def openrouter(cls) -> SearchSetup:
        return cls(kind=SearchKind.OPENROUTER)

    @classmethod
    def none(cls) -> SearchSetup:
        return cls(kind=SearchKind.NONE)

    @property
    def endpoint(self) -> str:
        """The SearXNG address to actually call, with the default filled in."""
        if self.kind is not SearchKind.SEARXNG:
            return ""
        return (self.searx_url or DEFAULT_SEARX_URL).rstrip("/")

    @property
    def enabled(self) -> bool:
        return self.kind is not SearchKind.NONE

    @property
    def costs_money(self) -> bool:
        """Worth saying plainly: one of these is metered and the other is not."""
        return self.kind is SearchKind.OPENROUTER

    def available_with(self, connection: Connection) -> bool:
        """Can this option work at all, given where he thinks?

        The plugin is an OpenRouter extension. Other OpenAI-compatible hosts don't
        ignore the ``plugins`` key — they reject the whole request — so offering it
        alongside a different provider would be offering something broken.
        """
        if self.kind is SearchKind.OPENROUTER:
            return connection.supports_web_plugin
        return True

    def problems(self, connection: Connection) -> list[str]:
        """What is structurally wrong with this choice, in words worth showing."""
        if not self.available_with(connection):
            return ["OpenRouter's search only works when he thinks through OpenRouter."]
        if self.kind is SearchKind.SEARXNG and not self.endpoint.startswith("http"):
            return ["A SearXNG address needs to start with http:// or https://."]
        return []

    def public(self) -> dict:
        return {
            "kind": str(self.kind),
            "searxUrl": self.searx_url or None,
            "endpoint": self.endpoint or None,
            "enabled": self.enabled,
            "costsMoney": self.costs_money,
        }
