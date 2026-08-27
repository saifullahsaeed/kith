"""Whether there is a newer Kith than the one running.

A downloaded copy has no way of finding out. The releases are on GitHub and the app never looks,
so someone who installed 0.3.0 in August is still on it — not because they decided to be, but
because nothing ever said otherwise.

**Told, not installed.** macOS auto-update goes through Squirrel, which verifies that the
signature on the downloaded app matches the one running, and Kith is ad-hoc signed — see
`identity: null` in the desktop build and `CSC_IDENTITY_AUTO_DISCOVERY: false` in the release
workflow. An ad-hoc signature has no stable identity, so that check cannot pass. Swapping the app
in place needs a paid Developer ID, and until there is one the honest thing is to say a new
version exists and hand over the download.

**The version comes from the shell, not from here.** `desktop/package.json` is what the release
workflow tags and what names the dmg, so it is the only version that means anything; the server
is handed it as `KITH_APP_VERSION` when the shell spawns it. Run from a checkout there is no
such thing as "the version you installed", so the answer is that there is nothing to check —
which is different from "you are up to date" and is reported differently.

**Nothing here is allowed to matter.** No network call at import, a short timeout, six hours
between checks, and every failure returns a shape the interface can render. An update notice that
breaks a screen is worse than no update notice.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import UTC, datetime

import requests

from kith import settings

#: The public repo the releases live in. Public, so this needs no token and no rate-limit
#: budget: unauthenticated GitHub allows 60 an hour per address and this asks for one a day.
RELEASES = "https://api.github.com/repos/saifullahsaeed/kith/releases/latest"

#: GitHub rejects a request with no User-Agent, with a 403 that reads like a rate limit.
_AGENT = "kith-desktop"

#: Long enough that a machine left running for a week asks seven times.
_EVERY = 6 * 60 * 60

_TIMEOUT = 6.0

_lock = threading.Lock()
_cached: dict | None = None
_checked_at = 0.0


def current_version() -> str:
    """What is running.

    The shell sets `KITH_APP_VERSION` when it spawns the server, which is the only version that
    means anything for an installed copy. From a checkout nothing sets it — `./run` starts the
    server itself — so this falls back to reading `desktop/package.json`, which is the file the
    release workflow tags and the one the shell would have reported anyway.

    Knowing the version is a separate question from being installed: see `is_packaged`. A
    checkout has a version and should say so — "running from source" told you where it came from
    and not which one it was, which is the wrong half when someone is writing a bug report.
    """
    named = (os.environ.get("KITH_APP_VERSION") or "").strip().lstrip("v")
    if named:
        return named
    try:
        manifest = settings.SERVER_ROOT.parent / "desktop" / "package.json"
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError):
        # A frozen bundle has no `desktop/` beside it, and there the environment variable is
        # always set — so this is only ever reached by a checkout someone has rearranged.
        return ""


def is_packaged() -> bool:
    """Whether this is an installed Kith rather than a checkout.

    What decides if a download is offered. A developer running from source has a version and
    should be told it; handing them a dmg would be answering a question they did not ask.
    """
    return bool((os.environ.get("KITH_APP_VERSION") or "").strip())


def _parts(version: str) -> tuple:
    """A comparable key for a version string.

    Numbers compared as numbers, and a pre-release sorts *below* the release it leads to, so
    `0.2.0-beta` is older than `0.2.0` rather than newer — which a plain string compare gets
    backwards, and which this repo has actually shipped (`v0.2.0-beta`).
    """
    core, _, pre = version.partition("-")
    numbers = tuple(int(n) for n in re.findall(r"\d+", core)) or (0,)
    # Pad so (0, 5) and (0, 5, 0) compare equal rather than by length.
    numbers = numbers + (0,) * (4 - len(numbers)) if len(numbers) < 4 else numbers
    return (numbers, 0 if pre else 1, pre)


def is_newer(latest: str, current: str) -> bool:
    """Is `latest` a version worth telling someone about?"""
    if not latest or not current:
        return False
    return _parts(latest.lstrip("v")) > _parts(current.lstrip("v"))


def _fetch() -> dict:
    """`requests`, like every other outbound call here, and for a reason worth writing down.

    Hand-rolled `urllib` verified against the system keychain, which the Python this ships with
    does not use — every call failed with `CERTIFICATE_VERIFY_FAILED` while `curl` to the same
    URL was fine. `requests` carries `certifi`, which is already in the frozen bundle because
    the model client depends on it.
    """
    response = requests.get(RELEASES, headers={"User-Agent": _AGENT}, timeout=_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _describe(payload: dict) -> dict:
    latest = str(payload.get("tag_name") or "").lstrip("v")
    # The dmg if there is one, the release page if there is not. Never an empty button.
    assets = [one for one in payload.get("assets") or [] if str(one.get("name", "")).endswith(".dmg")]
    return {
        "latest": latest,
        "page": str(payload.get("html_url") or ""),
        "download": str(assets[0].get("browser_download_url") or "") if assets else "",
        "notes": str(payload.get("body") or ""),
        "publishedAt": str(payload.get("published_at") or ""),
    }


def check(force: bool = False) -> dict:
    """The state of things, from cache unless it is stale or someone asked.

    One request at a time: the tray and the interface both read this, and on a cold start they
    ask within milliseconds of each other. Two calls would be two requests for one answer.
    """
    global _cached, _checked_at
    with _lock:
        fresh = _cached is not None and (time.monotonic() - _checked_at) < _EVERY
        if fresh and not force:
            return dict(_cached or {})

        current = current_version()
        if not is_packaged():
            # A checkout. Saying "up to date" would be a claim about something that has no
            # version at all, and offering a dmg to someone running from source is worse.
            _cached = {
                "current": current,
                "latest": "",
                "newer": False,
                "packaged": False,
                "checkedAt": datetime.now(UTC).isoformat(),
                "error": "",
                "page": "",
                "download": "",
                "notes": "",
                "publishedAt": "",
            }
            _checked_at = time.monotonic()
            return dict(_cached)

        state = {
            "current": current,
            "latest": "",
            "newer": False,
            "packaged": True,
            "checkedAt": datetime.now(UTC).isoformat(),
            "error": "",
            "page": "",
            "download": "",
            "notes": "",
            "publishedAt": "",
        }
        try:
            state.update(_describe(_fetch()))
            state["newer"] = is_newer(state["latest"], current)
        except (requests.RequestException, ValueError, KeyError, OSError) as exc:
            # Kept and shown rather than swallowed: "could not reach GitHub" is a different
            # thing from "you are up to date", and a screen that cannot tell them apart will
            # eventually tell someone they are current when nobody has looked in a month.
            state["error"] = f"{type(exc).__name__}: {exc}"[:200]
            # A previous good answer is better than none — the network coming back should not
            # be what it takes to know an update exists.
            if _cached and _cached.get("latest"):
                state.update({k: _cached[k] for k in ("latest", "page", "download", "notes", "publishedAt")})
                state["newer"] = is_newer(state["latest"], current)

        _cached = state
        _checked_at = time.monotonic()
        return dict(state)
