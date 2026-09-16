"""Reading and changing how Kith is set up.

Two stores, kept apart by the server for a reason worth preserving here rather than flattening
into one list. ``/config`` holds the chat parameters a conversation depends on — model, context
size, the API key. ``/tuning`` holds the operational knobs — how long a turn may run, how often
he acts on his own, how quickly a loop is caught. Different audience, different blast radius,
and independently resettable.

``kith settings`` shows both and says which store each key is in, because the one question this
command has to answer unambiguously is *where does this value live* — a key set in the wrong
store silently does nothing.

The tuning half is generated, not enumerated. ``/api/tuning`` returns every knob with its own
label, help, bounds, default and whether an environment variable is overriding it, precisely so
a client does not keep a second copy of all that. A CLI with a hard-coded list of knobs would
be stale the first time one is added, and stale in the direction where ``kith settings set``
refuses something the app allows.
"""

from __future__ import annotations

from kith.cli import render
from kith.cli.client import Client
from kith.cli.errors import FAILED, Failure

#: The chat settings, and what each is. `/config` has no self-description the way `/tuning`
#: does, so this is the one hard-coded list — kept short and checked by a test against the
#: route's own key map rather than trusted.
CONFIG_KEYS = {
    "model": "which model answers",
    "numCtx": "context window, in tokens",
    "numPredict": "maximum answer length, in tokens",
    "think": "whether reasoning is on",
    "effort": "share of the budget spent thinking",
    "baseUrl": "where the provider is",
    "apiKey": "the provider key (never printed)",
}

#: Never shown, whatever is asked. A settings dump that prints a key is a settings dump that
#: ends up pasted into an issue.
SECRET = {"apiKey"}


def add(subparsers) -> None:
    settings = subparsers.add_parser("settings", help="how Kith is set up")
    inner = settings.add_subparsers(dest="what")

    settings.add_argument("--json", action="store_true")
    settings.set_defaults(run=_show, what="")

    get = inner.add_parser("get", help="one value")
    get.add_argument("key")
    get.set_defaults(run=_get)

    put = inner.add_parser("set", help="change one value")
    put.add_argument("key")
    put.add_argument("value")
    put.set_defaults(run=_set)

    reset = inner.add_parser("reset", help="restore the tuning defaults")
    reset.set_defaults(run=_reset)


def _tuning(client: Client) -> dict:
    return client.get("/tuning") or {}


def _knobs(tuning: dict) -> dict[str, dict]:
    return {
        str(knob.get("key")): knob
        for group in tuning.get("groups") or []
        for knob in group.get("settings") or []
    }


def _display(key: str, value) -> str:
    if key in SECRET:
        return "set" if value else "unset"
    return "" if value is None else str(value)


def _show(client: Client, args) -> int:
    config = client.get("/config") or {}
    tuning = _tuning(client)
    if args.json:
        # The key is redacted rather than removed: a caller diffing two machines needs to know
        # whether one has a key at all, and needs never to learn what it is.
        safe = {key: ("set" if value else "") if key in SECRET else value for key, value in config.items()}
        render.emit_json({"config": safe, "tuning": tuning})
        return 0

    print("chat  (kith settings set <key> <value>)")
    rows = [[key, _display(key, config.get(key)), blurb] for key, blurb in CONFIG_KEYS.items()]
    render.table(rows)
    for group in tuning.get("groups") or []:
        print()
        print(f"{group.get('label')}  — {group.get('blurb')}")
        rows = []
        for knob in group.get("settings") or []:
            mark = "env" if knob.get("fromEnv") else ("" if knob.get("isDefault") else "·")
            rows.append(
                [
                    str(knob.get("key")),
                    str(knob.get("value")),
                    str(knob.get("unit") or ""),
                    mark,
                    str(knob.get("label") or ""),
                ]
            )
        render.table(rows)
    paths = tuning.get("paths") or {}
    if paths:
        print()
        print("paths  (read-only)")
        render.table([[key, str(value)] for key, value in paths.items()])
    print()
    print("  · changed from default   env overridden by an environment variable")
    return 0


def _locate(client: Client, key: str) -> tuple[str, dict]:
    """Which store owns this key. Fails with the near misses rather than a bare 'unknown'."""
    if key in CONFIG_KEYS:
        return "config", {}
    tuning = _tuning(client)
    knobs = _knobs(tuning)
    if key in knobs:
        return "tuning", knobs[key]
    known = sorted([*CONFIG_KEYS, *knobs])
    close = [candidate for candidate in known if key.lower() in candidate.lower()]
    raise Failure(
        f"no setting {key!r}",
        FAILED,
        ("did you mean " + ", ".join(close[:5])) if close else "list them with: kith settings",
    )


def _get(client: Client, args) -> int:
    store, knob = _locate(client, args.key)
    if store == "config":
        value = (client.get("/config") or {}).get(args.key)
        print(_display(args.key, value))
    else:
        print(knob.get("value"))
    return 0


def _coerce(text: str, kind: str):
    """Turn the argument into what the store expects.

    Everything arrives from a shell as a string, and both endpoints are typed. `/tuning` clamps
    out-of-range numbers rather than rejecting them, but it does not guess at `"12"` when it
    wanted `12` — so the conversion happens here, driven by the `kind` the server itself
    declared for that knob.
    """
    if kind in ("int", "integer"):
        try:
            return int(text)
        except ValueError:
            raise Failure(f"{text!r} is not a whole number", FAILED) from None
    if kind in ("float", "number"):
        try:
            return float(text)
        except ValueError:
            raise Failure(f"{text!r} is not a number", FAILED) from None
    if kind in ("bool", "boolean"):
        lowered = text.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise Failure(f"{text!r} is not true or false", FAILED)
    return text


def _set(client: Client, args) -> int:
    store, knob = _locate(client, args.key)
    if store == "config":
        # `think` is the one boolean in the chat store; everything else is a string or a number
        # the schema coerces. Sent as typed otherwise, and the schema refuses what it must.
        value: object = args.value
        if args.key == "think":
            value = _coerce(args.value, "bool")
        elif args.key in ("numCtx", "numPredict"):
            value = _coerce(args.value, "int")
        client.patch("/config", {args.key: value})
        print(f"{args.key} = {_display(args.key, value)}")
        return 0

    applied = client.patch("/tuning", {args.key: _coerce(args.value, str(knob.get("kind") or ""))}) or {}
    effective = (applied.get("applied") or {}).get(args.key, args.value)
    if str(effective) != args.value.strip():
        # Clamped rather than rejected — the server's choice, and silently accepting the
        # command while storing a different number would be the one unhelpful way to report it.
        print(f"{args.key} = {effective}  (clamped from {args.value})")
    else:
        print(f"{args.key} = {effective}")
    return 0


def _reset(client: Client, args) -> int:
    client.post("/tuning/reset")
    print("tuning restored to defaults")
    return 0
