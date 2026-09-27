"""A second opinion from a scoring model, when one is reachable — never a dependency.

Kith grades his own work. `services/tasks._verify_done` is good at catching a close he *knows*
is incomplete and structurally blind to one he *believes* is done and is not — he wrote the
brief, chose the requirements, supplied the evidence, then graded it. Measured 2026-09-26: five
tasks titled "… through the API" closed with every requirement met and no screen able to reach
any of them.

An outside scorer breaks that loop. TypeSafe's **Jev** (a "System One" model on OpenRouter,
`POST <base_url>/systemone`) reads a piece of text and returns, per plain-language question, a
0–1 probability — no prose to parse, ~0.7s, a fraction of a cent. Measured here on 23 hand-
labelled closes: the question in `WOULD_A_PERSON_AGREE` scored the five real failures 0.10–0.34
and the genuine ones 0.44–0.57, so a 0.4 line separates them 22 times out of 23.

**Three things this is not.** It is not a dependency: every path here returns ``None`` on any
failure, and the caller must treat ``None`` as "no opinion, carry on as before". It is not a
judge of code — it reads only the text it is handed, so it catches Kith reinterpreting the ask
(a screen becoming an API), not Kith claiming work he never did; the file ledger is the guard
for that. And it is not the main model: it runs only when the person's endpoint is OpenRouter,
on the key already configured, so it adds no new secret and no new provider to trust.
"""

from __future__ import annotations

import json

import requests

from kith.domain import connection

#: The Jev model id. `jev-latest` resolves to the current dated build; NOT `typesafe/jev-latest`,
#: which the endpoint rejects.
MODEL = "jev-latest"

#: Below this, a `noul` answer is treated as "no". The 0.4 line that split 22 of 23 labelled
#: closes: the real failures sat at 0.10–0.34, the genuine ones at 0.44 and up.
DOUBT_BELOW = 0.4

#: The done-gate's question, kept here with its measurement so the wording is not "improved"
#: without re-labelling a set — the phrasing decides the answer (a reworded version passed the
#: exact case this one failed). "the way they would actually do it (on a screen, not only via an
#: API or a test)" is the clause that catches the backend-only close.
WOULD_A_PERSON_AGREE = (
    "Does the evidence show the person can now do what they asked for, the way they would "
    "actually do it — for example on a screen, not only through an API or a test?"
)


def available(config) -> bool:
    """Whether a second opinion can be asked at all: an OpenRouter key is configured.

    Jev lives on OpenRouter. On a local model or another OpenAI-compatible host the endpoint
    is absent or answers a different shape, so the honest thing is to not ask and let the
    caller fall back — which it must do anyway, since this can fail for a dozen other reasons.
    """
    return bool(config.api_key and connection.is_openrouter(config.base_url))


def noul(state: str, question: str, *, config, timeout: int = 20) -> float | None:
    """One 0–1 score for ``question`` about ``state``, or ``None`` when no opinion is available.

    ``None`` is returned for every reason a score cannot be trusted — not configured, network
    down, a non-200, a shape we did not expect — because the caller's fallback (grade it the way
    Kith already does) is always safe, and a wrong number here would be worse than no number.
    """
    if not available(config):
        return None
    url = f"{config.base_url}/systemone"
    body = {"model": MODEL, "state": state, "questions": {"q": {"type": "noul", "instructions": question}}}
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        **connection.attribution(config.base_url),
    }
    try:
        response = requests.post(url, json=body, headers=headers, timeout=timeout)
        if response.status_code != 200:
            return None
        answer = (response.json().get("answers") or {}).get("q") or {}
        score = answer.get("noul")
        return float(score) if isinstance(score, (int, float)) else None
    except (requests.exceptions.RequestException, ValueError, json.JSONDecodeError, TypeError):
        return None


def doubts_the_close(their_words: str, brief: str, evidence: str, *, config) -> tuple[bool, float] | None:
    """Ask whether a person would agree this task is done. ``(doubts, score)`` or ``None``.

    ``None`` means no opinion was available and the caller should decide the way it did before
    this existed. ``(True, score)`` means the scorer is below :data:`DOUBT_BELOW` — hand it back.
    The state is the same shape the labelled measurement used: their ask, then the evidence.
    """
    state = (
        f"What the person asked for, in their words:\n{their_words or '(not recorded)'}\n\n"
        f"The task the agent set itself: {brief}\n\n"
        f"The agent's evidence that it is done:\n{evidence}"
    )
    score = noul(state, WOULD_A_PERSON_AGREE, config=config)
    if score is None:
        return None
    return (score < DOUBT_BELOW, score)
