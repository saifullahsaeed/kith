"""What every provider must be able to do, and how it describes itself.

Three providers, one interface. The interface is small on purpose — onboarding only
ever needs two things from a provider: *can I reach you*, and *what do you offer* —
and keeping it that small is what lets a fourth be added without touching the UI.

Each subclass also carries its own presentation (label, blurb, what it needs), so the
choice cards are generated from the providers themselves. A new provider appears in
the UI by existing, not by someone remembering to add a card.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import requests

from kith.domain.connection import Connection, ModelInfo, ProviderKind

#: Long enough for a cold provider, short enough to fail while the user is still
#: looking at the field they just typed in.
LIST_TIMEOUT = (5, 12)


class ProviderError(RuntimeError):
    """A provider that answered unhelpfully.

    Carries text meant for a person, not a stack trace: the provider's own words
    ("invalid api key", "insufficient credits") are almost always more useful than
    anything we could write about them.
    """


class Provider(ABC):
    """One way of reaching a model."""

    kind: ClassVar[ProviderKind]
    #: Shown on the choice card.
    label: ClassVar[str]
    #: One line: what this is.
    blurb: ClassVar[str]
    #: What the user has to supply or install.
    requires: ClassVar[str]
    #: Why someone would pick this one — the honest trade, not marketing.
    tradeoff: ClassVar[str]
    #: Where to get a key, when that applies.
    signup_url: ClassVar[str] = ""
    #: True when models can be listed before a credential exists, which lets the
    #: picker be populated while the user is still deciding.
    lists_without_key: ClassVar[bool] = False

    @abstractmethod
    def list_models(self, connection: Connection) -> list[ModelInfo]:
        """Everything this provider offers. Raises ProviderError if it cannot say."""

    def verify_key(self, connection: Connection) -> str:
        """Check the credential, returning something reassuring to show.

        Separate from ``list_models`` because on some providers the two are not the
        same question. OpenRouter's catalogue is public: it answers 200 to a key made
        of nonsense, so a listing that succeeds proves nothing about the credential —
        and onboarding would cheerfully save a key that fails on his first message.

        The default is right wherever listing is authenticated, which is most places:
        if the list arrived, the key worked. Raise ``ProviderError`` to reject one.
        """
        return ""

    def card(self) -> dict:
        """The choice card, generated rather than hand-written in the UI."""
        return {
            "kind": str(self.kind),
            "label": self.label,
            "blurb": self.blurb,
            "requires": self.requires,
            "tradeoff": self.tradeoff,
            "signupUrl": self.signup_url,
            "listsWithoutKey": self.lists_without_key,
            "needsBaseUrl": self.kind is ProviderKind.OPENAI_COMPATIBLE,
            "needsKey": self.kind is not ProviderKind.OLLAMA,
        }

    # -- shared plumbing ---------------------------------------------------- #

    def _get_json(self, url: str, api_key: str = "") -> dict:
        """GET some JSON, turning every failure into a sentence worth reading.

        Uses ``requests`` like the rest of the codebase rather than urllib — on macOS
        a python.org interpreter has no system trust store, so urllib fails every
        HTTPS call with CERTIFICATE_VERIFY_FAILED while requests bundles certifi.
        """
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            response = requests.get(url, headers=headers, timeout=LIST_TIMEOUT)
        except requests.exceptions.SSLError as exc:
            raise ProviderError(f"Couldn't verify the certificate for {url}. {exc}") from exc
        except requests.exceptions.ConnectionError:
            raise ProviderError(f"Nothing answered at {url}. Is the address right?") from None
        except requests.exceptions.Timeout:
            raise ProviderError(f"{url} didn't answer in time.") from None
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"Couldn't reach {url}. {exc}") from exc

        if response.status_code != 200:
            raise ProviderError(self._explain(response))
        try:
            return response.json()
        except ValueError:
            raise ProviderError(f"{url} answered, but not with JSON — is it really an API?") from None

    @staticmethod
    def _explain(response: requests.Response) -> str:
        """Say what the provider said, prefixed with what it probably means."""
        detail = response.text[:300]
        try:
            body = response.json()
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    detail = error.get("message") or detail
                elif isinstance(error, str):
                    detail = error
        except ValueError:
            pass
        detail = detail.strip()
        if response.status_code in (401, 403):
            return f"That key was rejected. {detail}".strip()
        if response.status_code == 404:
            return f"No model list at that address — check the base URL. {detail}".strip()
        if response.status_code == 429:
            return f"Rate limited by the provider. {detail}".strip()
        return f"The provider returned {response.status_code}. {detail}".strip()
