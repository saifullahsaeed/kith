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
    #: A unit for the UI to render after the field ("rounds", "seconds", "ticks").
    unit: str = ""

    def coerce(self, raw: object) -> int | float | str | bool:
        """Read a value of this knob's type, or raise ValueError saying why not.

        Bounds are clamped rather than rejected: a slider that refuses is annoying,
        and every bound here is a real limit (zero rounds means he cannot act at all),
        so the nearest legal value is what the person meant.
        """
        if self.kind == "text":
            return str(raw).strip()
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
        }


@dataclass(frozen=True)
class Group:
    """A heading in the settings UI, with the reason these belong together."""

    key: str
    label: str
    blurb: str


GROUPS: tuple[Group, ...] = (
    Group(
        "turn",
        "A turn",
        "How far he gets on one message before he has to stop and answer.",
    ),
    Group(
        "memory",
        "What he holds while working",
        "Tool output is most of his context. These decide what he still remembers "
        "about page three by the time he reads page nine.",
    ),
    Group(
        "rhythm",
        "Working on his own",
        "When nobody is talking to him, this is his pace and what he does with it.",
    ),
    Group(
        "stalls",
        "Noticing he's stuck",
        "He can loop — the same two searches, forever. This is how quickly that gets "
        "caught, traded against interrupting work that was actually progressing.",
    ),
    Group(
        "infrastructure",
        "Addresses and models",
        "Where the pieces around him live.",
    ),
    Group(
        "mcp",
        "Other programs' tools",
        "MCP servers are separate programs he talks to over a pipe. These are how long he "
        "waits on one before deciding it is not coming back.",
    ),
)


TUNABLES: tuple[Tunable, ...] = (
    # -- a turn ------------------------------------------------------------- #
    Tunable(
        key="max_rounds",
        env="KITH_MAX_ROUNDS",
        label="Tool rounds per turn",
        help="How many times he may stop and use tools before he must answer. Too low "
        "and he abandons real research half-finished; too high and a single message "
        "can run for many minutes and cost accordingly.",
        default=40,
        group="turn",
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
        group="turn",
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
        group="turn",
        minimum=1,
        maximum=16,
        unit="calls",
    ),
    Tunable(
        key="stop_after_delegating",
        env="KITH_STOP_AFTER_DELEGATING",
        label="Stop working once he's delegated",
        help="When he files a task or starts a project, that's a decision the work "
        "happens later — so he stops researching it and tells you what he set up. Off, "
        "and he files a task and then immediately spends the rest of the turn on it, "
        "which is neither delegating nor finishing.",
        default=True,
        group="turn",
        kind="bool",
    ),
    Tunable(
        key="milestone_task_cap",
        env="KITH_MILESTONE_TASK_CAP",
        label="Tasks one milestone may hold at once",
        help="The most open tasks he may file under a single milestone before he has to finish "
        "or drop some. Keeps a breakdown shallow — one milestone's next handful of steps, not "
        "the whole roadmap at once — which is how nine overlapping tasks piled under one "
        "milestone in the run this fixes.",
        default=6,
        group="turn",
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
        group="memory",
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
        group="memory",
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
        group="memory",
        minimum=1,
        maximum=50,
        unit="results",
    ),
    Tunable(
        key="tool_stub_chars",
        env="KITH_TOOL_STUB_CHARS",
        label="Kept from a trimmed result",
        help="How much of an older result survives — enough to remember what it was "
        "about. Too small and he can't tell whether he already looked at something.",
        default=1_200,
        group="memory",
        minimum=100,
        maximum=20_000,
        unit="characters",
    ),
    Tunable(
        key="handoff_steps",
        env="KITH_HANDOFF_STEPS",
        label="Recent steps shown when resuming",
        help="How many of his own last steps a working tick is shown, so it can tell it has "
        "already tried this and change course instead of repeating. Too few and a long loop is "
        "invisible to him; too many and the resume prompt gets expensive.",
        default=8,
        group="memory",
        minimum=1,
        maximum=30,
        unit="steps",
    ),
    # -- rhythm ------------------------------------------------------------- #
    Tunable(
        key="min_gap",
        env="KITH_MIN_GAP",
        label="Shortest gap between ticks",
        help="A floor that survives everything else, so a bug or a very fast model "
        "can't turn his loop into a spend spiral.",
        default=3.0,
        group="rhythm",
        kind="float",
        minimum=0.5,
        maximum=120,
        unit="seconds",
    ),
    Tunable(
        key="tick_max_tokens",
        env="KITH_TICK_MAX_TOKENS",
        label="Longest self-directed step",
        help="Output tokens one unattended step may produce. It bounds a step's written "
        "output only; the real ceiling on what a session costs is 'session_token_cap'.",
        default=2_000,
        group="rhythm",
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
        group="rhythm",
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
        group="rhythm",
        minimum=100_000,
        maximum=500_000_000,
        unit="tokens",
    ),
    # -- stalls ------------------------------------------------------------- #
    Tunable(
        key="stall_break",
        env="KITH_STALL_BREAK",
        label="Repeats before a nudge",
        help="Consecutive near-identical ticks before he's told he's going in circles. "
        "Two is deliberately impatient — a third identical tick is money for nothing.",
        default=2,
        group="stalls",
        minimum=1,
        maximum=20,
        unit="ticks",
    ),
    Tunable(
        key="stall_giveup",
        env="KITH_STALL_GIVEUP",
        label="Repeats before dropping the task",
        help="When the nudge doesn't work either, he sets the task aside rather than "
        "grinding. Set high and a stuck task can absorb an entire budget.",
        default=4,
        group="stalls",
        minimum=2,
        maximum=50,
        unit="ticks",
    ),
    Tunable(
        key="focus_grind_limit",
        env="KITH_FOCUS_GRIND_LIMIT",
        label="No-progress ticks before setting a task aside",
        help="How many ticks the same task may be worked with nothing newly ticked off or "
        "delivered before he stops and hands it back. The backstop for a loop that rewords "
        "itself each tick so the prose and shape detectors miss it — measured on the task's "
        "own progress, not its wording.",
        default=6,
        group="stalls",
        minimum=2,
        maximum=50,
        unit="ticks",
    ),
    Tunable(
        key="prose_match",
        env="KITH_PROSE_MATCH",
        label="Similar wording threshold",
        help="How alike two ticks' wording must be to count as a repeat, 0 to 1. Lower "
        "catches loops sooner and interrupts genuine work more often.",
        default=0.6,
        group="stalls",
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
        group="stalls",
        kind="float",
        minimum=0.1,
        maximum=1.0,
    ),
    Tunable(
        key="shape_min_tools",
        env="KITH_SHAPE_MIN_TOOLS",
        label="Calls before shape counts",
        help="Below this many tool calls, two ticks looking alike means nothing — "
        "there aren't enough actions for the resemblance to be evidence.",
        default=3,
        group="stalls",
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
        group="infrastructure",
        kind="text",
    ),
    Tunable(
        key="ollama_host",
        env="OLLAMA_HOST",
        label="Ollama address",
        help="Where Ollama listens. Used for local models and — whatever he thinks "
        "with — for the embeddings behind memory search.",
        default="http://127.0.0.1:11434",
        group="infrastructure",
        kind="text",
    ),
    Tunable(
        key="embed_model",
        env="KITH_EMBED_MODEL",
        label="Embedding model",
        help="Turns his memories into vectors so recall works by meaning. Runs locally "
        "even when he thinks in the cloud. Changing it invalidates existing vectors.",
        default="nomic-embed-text",
        group="infrastructure",
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
        group="infrastructure",
        kind="text",
    ),
    Tunable(
        key="search_engine",
        env="KITH_SEARCH_ENGINE",
        label="OpenRouter search engine",
        help="Which backend the web plugin uses. Exa costs a flat amount per search; "
        "`native` varies by model and prices by context size.",
        default="exa",
        group="infrastructure",
        kind="text",
    ),
    Tunable(
        key="search_model",
        env="KITH_SEARCH_MODEL",
        label="Search carrier model",
        help="The plugin feeds results back through a model as prompt tokens, billed at "
        "that model's rate. Blank uses whatever he thinks with — point this at "
        "something cheap if he's on an expensive model.",
        default="",
        group="infrastructure",
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
