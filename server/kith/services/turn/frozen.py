"""What a turn decides once, before its first round.

Every value here is read at the top of a turn and then never re-read, and each one is that way
for a reason that cost something to learn. Gathered into one frozen object so the reasons live
together and the round loop can get on with rounds.

**The theme is that a value which changed under a turn would change the request without anyone
asking it to.** A settings knob re-read per round means a turn that starts with one budget and
finishes with another. Freezing is not an optimisation here; it is what makes a turn one thing
rather than N loosely related requests.

The tools are frozen the same way and are deliberately *not* here: `domain.tooling.ToolHost` is
that object, built by the adapter that has one. This module held an MCP snapshot and two name
sets derived from it for a while, which meant two objects with an opinion about which tools
exist and nothing making them agree.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

from kith.domain.chat import Config, Routing
from kith.llm import caching
from kith.llm.budget import ContextBudget
from kith.services import tuning


@dataclass(frozen=True)
class Turn:
    """The turn's fixed decisions. Everything the round loop reads but never changes."""

    #: The chat config with its session id resolved. See `_session_for`.
    config: Config
    #: How many tool rounds this turn may take.
    budget: int
    #: How many of those are reserved for landing — recording, delivering, handing back.
    reserve: int
    #: Reasoning effort for the landing rounds, or "" to leave every round as it was.
    landing_effort: str
    #: How the cloud requests should be steered.
    routing: Routing
    #: How much room is left, learned from what the provider charges each round. Mutable by
    #: design — it calibrates — which is why it is a reference held here rather than a value.
    room: ContextBudget
    #: Where to spill an aged-out tool result, or None when there is no conversation to file
    #: it under and the compactor must fall back to trimming in place.
    offload: Any | None


def begin(
    config: Config,
    agent_db_path: Path,
    conversation_id: str,
    max_rounds: int | None,
    landing_reserve: int | None = None,
) -> Turn:
    """Resolve everything a turn needs before its first round.

    Lifted out of `_run_turn` unchanged. The order matters in one place and is preserved: the
    conversation's stored session id wins over anything the caller passed, and the install-wide
    id is only minted when neither exists.

    ``landing_reserve`` overrides the knob for a turn whose value is not in what it files.
    Landing exists because research expands to fill the budget and a turn that gathers for
    forty rounds and is cut off mid-sentence leaves nothing behind — so the last few rounds
    are taken from gathering and given to writing it down. A sub-agent's entire output *is*
    its final message, and `_final_answer` already forces that when the budget runs out, so
    for one of those the reserve buys nothing and the directive that comes with it — write to
    your working file, add_deliverable, check_item — names tools it deliberately does not
    have. `0` means the whole budget is gathering; `None`, which is every other caller, means
    the knob decides.
    """
    config = _session_for(config, agent_db_path, conversation_id)
    offload = _spill_for(conversation_id)

    budget = max_rounds or tuning.value("max_rounds")
    # The floor is on the *cap*, not on the reserve: with `landing_reserve` at 0 the reserve is
    # 0 and budget-driven landing never fires at all.
    wanted_reserve = tuning.value("landing_reserve") if landing_reserve is None else landing_reserve
    reserve = min(max(0, int(wanted_reserve)), max(2, budget // 3))
    # Read once, same as `reserve` above: recording, delivering, ticking off, handing back is
    # not a reasoning-heavy phase, and reasoning is billed as output tokens whether or not any
    # of it is shown. Blank means "leave every round exactly as it was" — no override built.
    landing_effort = str(tuning.value("landing_effort") or "").strip().lower()

    # How much room is left, learned from what the provider charges each round.
    #
    # `num_predict` is -1 on a default install — the sentinel for "no limit" — so it cannot
    # be used as the answer reserve directly. Falling back to the answer cap gives a real
    # number, and a real number is the whole point: the threshold is absolute, because a
    # percentage of the window is wrong at both ends.
    wanted_out = config.num_predict if config.num_predict > 0 else tuning.value("max_answer_tokens")

    return Turn(
        config=config,
        budget=budget,
        reserve=reserve,
        landing_effort=landing_effort,
        routing=tuning.routing(),
        room=ContextBudget(window=config.context_window, reserve=int(wanted_out)),
        offload=offload,
    )


def _session_for(config: Config, agent_db_path: Path, conversation_id: str) -> Config:
    """The config with its OpenRouter stickiness id settled.

    A conversation is the right unit for it: every round in it shares a prompt prefix, and
    shares it with nothing else, so keeping one conversation on one upstream is what keeps its
    cache warm. Precedence, which the order below encodes: the conversation's stored id, then
    whatever the caller already put on the config, then the install-wide one.
    """
    if conversation_id:
        from kith.services import conversations

        session = conversations.session_id(agent_db_path, conversation_id)
        if session:
            return replace(config, session_id=session)
    if not config.session_id:
        # A turn with no conversation — a script, a test, a step nobody is working — belongs to
        # no session, and the install-wide id is the honest answer for it. Resolved here rather
        # than inside the transport, which had to open the config database to do it: the last
        # `llm -> infra` edge in the tree, written as a function-body import to hide the cycle.
        return replace(config, session_id=install_session_id())
    return config


def _spill_for(conversation_id: str):
    """Where an aged-out tool result goes, or None.

    Clearing happens here, before any round: a past turn's tool output never re-enters the
    prompt, so last turn's spill can refer to nothing and is dead weight. It must not happen
    *after* a save — `offload.save` is content-hash-named, so clearing later would delete a
    file whose path is already quoted in a stub the model has been sent.
    """
    if not conversation_id:
        return None
    from kith.services import offload as offload_svc

    offload_svc.clear(conversation_id)
    return partial(offload_svc.save, conversation_id)


def install_session_id() -> str:
    """The install-wide OpenRouter stickiness id, minted once and persisted.

    Lived in `llm/openai_compat._session_id` and opened the config database from inside the
    transport. Same function, two layers up, where reaching for storage is the job.
    """
    from kith.infra.db import config_store
    from kith.settings import CONFIG_DB_PATH

    stored = config_store.load_settings(CONFIG_DB_PATH)
    return caching.session_id(
        stored,
        lambda fresh: config_store.update_settings(CONFIG_DB_PATH, {caching.SESSION_KEY: fresh}),
    )
