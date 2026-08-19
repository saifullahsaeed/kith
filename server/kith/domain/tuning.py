"""Every knob that changes how Kith behaves, declared once.

``settings.py`` holds what an operator sets *before launch* — paths, the UI directory,
where Ollama listens. This holds what anyone should be able to change *while he runs*:
how long a turn may take, how often he pauses to reflect, how patient the stall
detector is. Those were previously either environment variables (unreachable from a
packaged desktop app — there is no shell to export them in) or constants inlined in the
module that happened to own them (unreachable from anywhere).

Each entry carries its own documentation, bounds, and default, so the settings UI is
generated from this list rather than duplicating it. Adding a knob means adding one
entry; nothing else has to know.

The help text says what the knob does **and what goes wrong if you get it wrong**.
These are sharp: too few rounds and he never finishes a job, too many and one message
costs real money. A number with no consequence attached is not configurable, it is
a dare.

Pure: no environment, no database. Resolution against those lives in
``services.tuning``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Tunable:
    """One setting: where it's stored, what it means, and what it may be."""

    key: str
    #: The environment variable that overrides it, kept for anyone running from source.
    env: str
    label: str
    #: What it does, then what breaks at the extremes.
    help: str
    default: int | float | str | bool
    group: str
    kind: Literal["int", "float", "text", "bool"] = "int"
    minimum: float | None = None
    maximum: float | None = None
    #: A unit for the UI to render after the field ("rounds", "seconds", "steps").
    unit: str = ""
    #: The only values this setting accepts, when there are a handful. A free-text box for a
    #: knob with three legal answers is a spelling test: `prefer_provider_by` takes "price",
    #: "throughput" or "latency", and anything else is silently ignored by the router — the
    #: setting reads as saved and does nothing. Empty means genuinely free text (a hostname,
    #: a model id). The blank option, where blank is meaningful, must be listed explicitly.
    choices: tuple[str, ...] = ()

    def coerce(self, raw: object) -> int | float | str | bool:
        """Read a value of this knob's type, or raise ValueError saying why not.

        Bounds are clamped rather than rejected: a slider that refuses is annoying,
        and every bound here is a real limit (zero rounds means he cannot act at all),
        so the nearest legal value is what the person meant.
        """
        if self.kind == "text":
            text = str(raw).strip()
            # Refused, not clamped. A number out of range has a nearest legal value that is
            # obviously what the person meant; a misspelt provider-sort does not — "pirce" is
            # not nearer "price" than "latency" in any sense worth guessing. And the failure was
            # silent: the router ignores an unknown value, so the setting saved, displayed back
            # exactly as typed, and did nothing at all.
            if self.choices and text not in self.choices:
                legal = ", ".join(repr(one) if one else "blank" for one in self.choices)
                raise ValueError(f"{self.label} must be one of: {legal}. Got {text!r}.")
            return text
        if self.kind == "bool":
            # Tolerant on the way in: a checkbox sends a real boolean, but the
            # environment can only ever send a string.
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        try:
            value = float(raw) if self.kind == "float" else int(float(raw))
        except (TypeError, ValueError):
            raise ValueError(f"{self.label} must be a number.") from None
        if self.minimum is not None:
            value = max(value, self.minimum)
        if self.maximum is not None:
            value = min(value, self.maximum)
        return value if self.kind == "float" else int(value)

    def public(self) -> dict:
        return {
            "key": self.key,
            "env": self.env,
            "label": self.label,
            "help": self.help,
            "default": self.default,
            "group": self.group,
            "kind": self.kind,
            "min": self.minimum,
            "max": self.maximum,
            "unit": self.unit,
            "choices": list(self.choices),
        }


@dataclass(frozen=True)
class Group:
    """A heading in the settings UI, with the reason these belong together."""

    key: str
    label: str
    blurb: str


# The order here is the order the settings page shows, top to bottom. Split so the two things a
# person actually distinguishes — "when I'm talking to him" and "when he's working on his own" —
# are separate sections rather than interleaved inside buckets named after mechanics. The old
# groups mixed knobs for different callers under "A turn", and the
# conversation-history knobs with the tool-stubbing knobs under "What he holds", so nobody could
# tell which lever touched which mode.
GROUPS: tuple[Group, ...] = (
    Group(
        "chat",
        "When you're talking to him",
        "How a back-and-forth in the chat behaves — how far he gets on one message, and how much "
        "of the conversation he keeps in front of him.",
    ),
    Group(
        "stuck",
        "Recognising work you already have",
        "How alike two tasks have to be before he treats them as the same one. Too loose and he "
        "refuses to file something genuinely new; too strict and the board fills with four "
        "wordings of one job.",
    ),
    Group(
        "context",
        "What stays in front of him",
        "The model re-reads its whole context every round, so this is what he still has about "
        "page three by the time he reaches page nine.",
    ),
    Group(
        "limits",
        "Spending limits",
        "The ceilings that stop a long turn costing more than you meant. This is the section to "
        "set if you are worried about the bill.",
    ),
    Group(
        "connections",
        "Connections & models",
        "Which model, which providers, and where the pieces around him live.",
    ),
    Group(
        "machine",
        "On this computer",
        "The parts of him that are not in the cloud: what time it is where you are, and the local "
        "programs he reaches for. Nothing here leaves the machine.",
    ),
    Group(
        "mcp",
        "Other programs' tools",
        "MCP servers are separate programs he talks to over a pipe. These are how long he "
        "waits on one before deciding it is not coming back.",
    ),
)


TUNABLES: tuple[Tunable, ...] = (
    # -- chat: one message ---------------------------------------------------- #
    Tunable(
        key="max_rounds",
        env="KITH_MAX_ROUNDS",
        label="Tool rounds per message",
        help="How many times he may stop and use tools before he must answer. Too low "
        "and he abandons real research half-finished; too high and a single message "
        "can run for many minutes and cost accordingly.",
        default=40,
        group="chat",
        minimum=2,
        maximum=200,
        unit="rounds",
    ),
    Tunable(
        key="landing_reserve",
        env="KITH_LANDING_RESERVE",
        label="Rounds saved for finishing",
        help="Rounds held back at the end so he can write down what he found. Without "
        "a reserve, research expands to fill the whole budget and the turn produces "
        "nothing durable — this was a real bug, not a precaution.",
        default=4,
        group="context",
        minimum=0,
        maximum=20,
        unit="rounds",
    ),
    Tunable(
        key="max_parallel",
        env="KITH_MAX_PARALLEL",
        label="Searches at once",
        help="Read-only calls he may run together. Higher is faster on research-heavy "
        "work; too high and a rate-limited search provider starts refusing him.",
        default=6,
        group="context",
        minimum=1,
        maximum=16,
        unit="calls",
    ),
    Tunable(
        key="subtask_rounds",
        env="KITH_SUBTASK_ROUNDS",
        label="Rounds a sub-agent gets",
        help="How long an isolated sub-agent may look before it has to report back. Small on "
        "purpose: the whole value of one is that the searching happens somewhere the main "
        "turn never has to read, and a scout that wanders for twenty rounds costs more than "
        "the answer is worth. If they keep coming back with nothing, the objectives are too "
        "broad, not the budget too small.",
        default=6,
        group="context",
        minimum=2,
        maximum=20,
        unit="rounds",
    ),
    # `stop_after_delegating` stood here and is gone with the mechanism it drove — see the long
    # note in `services/agent_loop` where the `delegated` latch used to be. Short version: it
    # dated from when a conversation was an intake desk, it contradicted the chat directive
    # outright once that changed, and it was the largest single cause of a turn announcing a
    # plan and doing none of it.
    Tunable(
        key="milestone_task_cap",
        env="KITH_MILESTONE_TASK_CAP",
        label="Tasks one milestone may hold at once",
        help="The most open tasks he may file under a single milestone before he has to finish "
        "or drop some. Keeps a breakdown shallow — one milestone's next handful of steps, not "
        "the whole roadmap at once — which is how nine overlapping tasks piled under one "
        "milestone in the run this fixes.",
        default=6,
        group="chat",
        minimum=2,
        maximum=20,
        unit="tasks",
    ),
    # -- memory ------------------------------------------------------------- #
    Tunable(
        key="live_tool_chars",
        env="KITH_LIVE_TOOL_CHARS",
        label="Tool output kept in full",
        help="Total characters of tool results carried forward intact. Should match the "
        "model's context: a 1M-token model can hold a whole turn's research, and "
        "trimming it makes him fetch the same pages again.",
        default=80_000,
        group="context",
        minimum=4_000,
        maximum=4_000_000,
        unit="characters",
    ),
    Tunable(
        key="mcp_connect_timeout",
        env="KITH_MCP_CONNECT_TIMEOUT",
        label="Waiting for an MCP server to start",
        help="How long a server gets to answer its first handshake. Servers installed by "
        "npx or uvx download on first run, so a short wait here reads as a broken server "
        "the first time and a working one after.",
        default=20.0,
        group="mcp",
        minimum=1.0,
        maximum=120.0,
        unit="seconds",
    ),
    Tunable(
        key="mcp_call_timeout",
        env="KITH_MCP_CALL_TIMEOUT",
        label="Waiting for an MCP tool",
        help="How long one call to another program's tool may take before he gives up on it "
        "and carries on. Too long and a hung server holds a whole turn; too short and a "
        "genuinely slow query looks like a failure.",
        default=30.0,
        group="mcp",
        minimum=1.0,
        maximum=300.0,
        unit="seconds",
    ),
    Tunable(
        key="keep_images",
        env="KITH_KEEP_IMAGES",
        label="Pictures kept in view",
        help="How many of the most recent images stay in the conversation. A picture costs "
        "far more than the sentence he writes about it, and once he has looked and said what "
        "he saw the pixels are dead weight that re-sends on every round.",
        default=2,
        group="context",
        minimum=0,
        maximum=20,
        unit="images",
    ),
    Tunable(
        key="keep_full_tool_results",
        env="KITH_KEEP_FULL_TOOL_RESULTS",
        label="Recent results always kept whole",
        help="A floor, so a few enormous pages can't squeeze out what he just read. "
        "Below about three he loses the thread between consecutive steps.",
        default=6,
        group="context",
        minimum=1,
        maximum=50,
        unit="results",
    ),
    Tunable(
        key="keep_tool_turns",
        env="KITH_KEEP_TOOL_TURNS",
        label="Turns of tool output kept whole",
        help="How many recent turns keep their tool results in full. Older ones are trimmed to a "
        "stub naming what produced them, so he re-reads a file rather than trusting a copy of it "
        "from an hour ago. 0 keeps everything, which is what the conversation used to do.",
        # Two, from measuring. One real conversation held 4.42M characters of tool output across
        # 124 turns and sat at 59% of a million-token window — 222k tokens of tool results plus
        # 184k of file reads, about 39% of the window, none of it ever trimmed because the only
        # things that trim are the fold at 80% and a character budget reached only *after* the
        # fold gives up. Keeping the last two turns whole is 135k characters: 3% of it. Five is
        # 205k, so the curve is flat past two and the extra buys very little.
        #
        # The other half of the argument is correctness, which `full_messages` already admits to:
        # "a tool result from hours ago is replayed as though it were still true, which it might
        # not be."
        default=2,
        group="context",
        minimum=0,
        maximum=100,
        unit="turns",
    ),
    Tunable(
        key="tool_stub_chars",
        env="KITH_TOOL_STUB_CHARS",
        label="Kept from a trimmed result",
        help="How much of an older result survives — enough to remember what it was "
        "about. Too small and he can't tell whether he already looked at something.",
        default=1_200,
        group="context",
        minimum=100,
        maximum=20_000,
        unit="characters",
    ),
    Tunable(
        key="history_max_chars",
        env="KITH_HISTORY_MAX_CHARS",
        label="Conversation kept in full (no known window)",
        help="Only used when his model's context window is unrecorded — a local model, or one "
        "adopted before its window was known. With a known window he folds at 80% of it "
        "automatically, scaled to that model, and this is ignored. High enough here and "
        "nothing is ever summarised on an unknown-window model (the prompt just grows); too "
        "low and he loses the detail of what was said earlier in the same chat.",
        default=120_000,
        group="chat",
        minimum=8_000,
        maximum=4_000_000,
        unit="characters",
    ),
    Tunable(
        key="history_keep_recent",
        env="KITH_HISTORY_KEEP_RECENT",
        label="Recent messages kept verbatim",
        help="How many of your most recent messages stay word-for-word — the tool calls "
        "each one made included, not just what was said — when older ones are folded into "
        "a summary. Too few and he forgets what he just read or ran; too many and the fold "
        "barely shrinks anything, since one message's worth of tool calls can be far "
        "bigger than a plain reply.",
        default=4,
        group="chat",
        minimum=1,
        maximum=20,
        unit="messages",
    ),
    # -- rhythm ------------------------------------------------------------- #
    Tunable(
        key="max_answer_tokens",
        env="KITH_MAX_ANSWER_TOKENS",
        label="Longest single answer",
        help="Output tokens one round may produce, and the room a turn reserves for its "
        "answer when the model states no limit of its own. It bounds what is written, not "
        "what a session costs — that ceiling is 'session_cost_cents'.",
        # 2,000 was a gag on the half of the work that produces something. It is *per response*, so
        # any single round that needed to write a file or a multi-hunk edit was truncated — and
        # giving a step fifty rounds while capping each one at 2,000 tokens would have left it just
        # as unable to finish, in more rounds.
        #
        # Cheap to lift, measured: output is 4.2% of the billable tokens on this install (0.34M out
        # against 7.8M uncached in). The thing being rationed was costing almost nothing.
        default=8_000,
        group="chat",
        minimum=200,
        maximum=32_000,
        unit="tokens",
    ),
    Tunable(
        key="session_cost_cents",
        env="KITH_SESSION_COST_CENTS",
        label="What one working session may spend",
        help="Money a single 'keep working' session may run through before it stops itself "
        "and tells you — in cents, as the provider bills it. This is the real ceiling. The "
        "token cap below is only a fallback for providers that report no cost (a local model "
        "costs nothing, so there is nothing for this to measure).",
        default=300,
        group="limits",
        minimum=10,
        maximum=100_000,
        unit="cents",
    ),
    Tunable(
        key="session_token_cap",
        env="KITH_SESSION_TOKEN_CAP",
        label="Tokens one session may spend (fallback)",
        help="Only used when the provider reports no cost. Counts tokens actually read — the "
        "uncached slice — not the whole prompt: at a 78% cache hit the two differ by more "
        "than fourfold, which is how a session that had spent 26 cents was stopped for "
        "running through 6.5 million 'tokens'.",
        default=20_000_000,
        group="limits",
        minimum=100_000,
        maximum=500_000_000,
        unit="tokens",
    ),
    # -- stalls ------------------------------------------------------------- #
    Tunable(
        key="prose_match",
        env="KITH_PROSE_MATCH",
        label="Similar wording threshold",
        help="How alike two steps' wording must be to count as a repeat, 0 to 1. Lower "
        "catches loops sooner and interrupts genuine work more often.",
        default=0.6,
        group="stuck",
        kind="float",
        minimum=0.1,
        maximum=1.0,
    ),
    Tunable(
        key="shape_match",
        env="KITH_SHAPE_MATCH",
        label="Similar actions threshold",
        help="The same, for which tools he used rather than what he said. This is the "
        "one that works: a real loop rewords itself freely while doing the identical "
        "thing, which is why matching prose alone missed it.",
        default=0.7,
        group="stuck",
        kind="float",
        minimum=0.1,
        maximum=1.0,
    ),
    Tunable(
        key="shape_min_tools",
        env="KITH_SHAPE_MIN_TOOLS",
        label="Calls before shape counts",
        help="Below this many tool calls, two steps looking alike means nothing — "
        "there aren't enough actions for the resemblance to be evidence.",
        default=3,
        group="stuck",
        minimum=1,
        maximum=20,
        unit="calls",
    ),
    # -- infrastructure ----------------------------------------------------- #
    Tunable(
        key="timezone",
        env="KITH_TZ",
        label="His clock",
        help="Reminders and schedules are interpreted in this zone. An IANA name, like Asia/Riyadh.",
        default="UTC",
        group="machine",
        kind="text",
    ),
    Tunable(
        key="ollama_host",
        env="OLLAMA_HOST",
        label="Ollama address",
        help="Where Ollama listens. Used for local models and — whatever he thinks "
        "with — for the embeddings behind memory search.",
        default="http://127.0.0.1:11434",
        group="machine",
        kind="text",
    ),
    Tunable(
        key="embed_model",
        env="KITH_EMBED_MODEL",
        label="Embedding model",
        help="Turns his memories into vectors so recall works by meaning. Runs locally "
        "even when he thinks in the cloud. Changing it invalidates existing vectors.",
        default="nomic-embed-text",
        group="machine",
        kind="text",
    ),
    Tunable(
        key="openrouter_provider",
        env="KITH_OR_PROVIDER",
        label="Pin OpenRouter to one upstream",
        help="OpenRouter spreads requests across many hosts, so rounds can miss each "
        "other's prompt cache. Blank is now the right answer for almost everyone: he "
        "sends a session id, which asks for the same host without giving up the "
        "fallbacks that keep him answering. Name a provider only to force one.",
        default="",
        group="connections",
        kind="text",
    ),
    Tunable(
        key="fallback_model",
        env="KITH_FALLBACK_MODEL",
        label="Fallback model",
        help="A second model to try if the main one errors, rate-limits or goes down. Blank "
        "means no fallback — then one provider outage ends a turn mid-task. "
        "Point it at a different-family model so an outage that takes one out doesn't take both.",
        default="",
        group="connections",
        kind="text",
    ),
    Tunable(
        key="require_provider_parameters",
        env="KITH_REQUIRE_PARAMS",
        label="Only route to fully-capable providers",
        help="Restrict OpenRouter to upstreams that support everything a request sends — tools, "
        "reasoning, prompt caching. On, a round never silently lands on a host that drops "
        "caching and pays full price; too strict and a model with one fussy provider goes dark.",
        # On, after measuring what off cost. Prompt caching is ~90% of the economics of a long
        # turn, and with this off a round can be served by a host that simply does not do it:
        # four recorded rounds on 2026-08-03 came back with `cachedTokens: 0` on prompts of
        # 24k-56k tokens, every one billed fresh, at a unit price ~8x the usual host as well.
        # The "goes dark" risk is real but it is loud and recoverable; paying full price for a
        # cache we asked for and did not get is silent and was running for days.
        default=True,
        group="connections",
        kind="bool",
    ),
    Tunable(
        key="prefer_provider_by",
        env="KITH_PREFER_PROVIDER_BY",
        label="Prefer providers by",
        help="How to order OpenRouter's hosts for a model when none is pinned: 'price' for the "
        "cheapest first, 'throughput' for the fastest, 'latency' for the quickest to start. "
        "Blank uses OpenRouter's own balancing, which is what let one session sit on a host "
        "charging fifty times the going rate for its whole life. Not a lock — fallbacks still "
        "apply if the preferred host is down.",
        default="price",
        group="connections",
        kind="text",
        # Blank is listed on purpose: it is a real answer, meaning "leave it to OpenRouter's own
        # balancing", and leaving it out of the list would make the default-restoring value
        # unreachable from the interface.
        choices=("price", "throughput", "latency", ""),
    ),
    Tunable(
        key="max_prompt_price",
        env="KITH_MAX_PROMPT_PRICE",
        label="Most to pay per million prompt tokens",
        help="A hard ceiling, in dollars per million prompt tokens. Covers the case ordering by "
        "price cannot: every cheap host busy and the fallback is the expensive one. 0 is off. "
        "Set it above the rate you normally see, not at it — at it, a small price move takes "
        "the model off the air.",
        default=0.0,
        group="limits",
        # `float`, and it has to be stated: `kind` defaults to "int", which silently coerced a
        # $0.50 ceiling to 0 — and 0 means *off*. A person setting a limit would have been told
        # nothing and gone on paying, which is worse than having no ceiling at all. Every real
        # value for this knob is below $1 on the model measured here.
        kind="float",
        minimum=0.0,
        maximum=1_000.0,
        unit="$/Mtok",
    ),
    Tunable(
        key="landing_effort",
        env="KITH_LANDING_EFFORT",
        label="Reasoning effort while landing",
        help="How hard he thinks on the rounds held back by 'rounds saved for finishing' — "
        "recording, delivering, ticking off, handing back, not gathering or deciding what to "
        "do next. That is not a reasoning-heavy phase, and reasoning is charged as output "
        "tokens regardless of whether any of it is shown. Blank leaves it to the model's own "
        "default for every round, landing included.",
        default="low",
        group="connections",
        kind="text",
        choices=("", "none", "low", "medium", "high", "xhigh", "max"),
    ),
    Tunable(
        key="zero_data_retention",
        env="KITH_ZERO_DATA_RETENTION",
        label="Zero data retention",
        help="Route only to providers that don't log or train on requests. On keeps the 'runs "
        "on your machine' promise when he reaches the cloud; it also shrinks the provider pool, "
        "so a model served only by logging hosts becomes unavailable rather than merely pricier.",
        default=False,
        group="connections",
        kind="bool",
    ),
    Tunable(
        key="search_engine",
        env="KITH_SEARCH_ENGINE",
        label="OpenRouter search engine",
        help="Which backend the web plugin uses. Exa costs a flat amount per search; "
        "`native` varies by model and prices by context size.",
        default="exa",
        group="connections",
        kind="text",
        choices=("exa", "native"),
    ),
    Tunable(
        key="search_model",
        env="KITH_SEARCH_MODEL",
        label="Search carrier model",
        help="The plugin feeds results back through a model as prompt tokens, billed at "
        "that model's rate. Blank uses whatever he thinks with — point this at "
        "something cheap if he's on an expensive model.",
        default="",
        group="connections",
        kind="text",
    ),
)

BY_KEY: dict[str, Tunable] = {knob.key: knob for knob in TUNABLES}


def for_key(key: str) -> Tunable:
    """The knob with this key, or a ValueError that says what's valid."""
    try:
        return BY_KEY[key]
    except KeyError:
        raise ValueError(f"unknown setting {key!r}") from None
