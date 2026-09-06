"""What a plugin is holding, and the one line of it he carries into a turn.

A plugin never holds storage. Kith holds one table and exposes it through four functions, and
**no caller ever names the slot it is writing into** — the owner is resolved here, from the
ambient session context, because every caller that could be handed it is one that could be lied
to. The sealed surface knows a conversation id only because we told it; the MCP subprocess knows
nothing at all; a route body is whatever arrived over HTTP. A scope the caller may name is not a
scope, it is a namespace with a naming convention in front of it.

**Three writers, and the model is not one of them.** The surface writes over the bridge; the
plugin's own MCP server writes through a reserved key on its tool result; the host writes
lifecycle facts. Every write the model could want is a plugin action it can already take, so
giving it a write door would add an argument, a merge policy, and the question of what happens
when the frame and the model disagree about one key. It reads, through one tool, and that is all.

**The digest is rendered here, by us, from key names a person approved.** A plugin supplies
bounded primitive values and never composes a sentence that reaches the prompt. That is
`permissions._refuse`'s rule — a justification is "only ever supplied in code, never from
anything a model composed" — applied to the one other place untrusted text could reach a person
or a turn.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select

from kith.infra.db.engine import session
from kith.infra.db.models import PluginState
from kith.infra.db.support import utc_now_iso
from kith.kernel import changes, session_context

Writer = Literal["surface", "server", "host"]

#: What one plugin may keep in one slot of one scope.
#:
#: Measured against the thing it sits beside: a conversation's touched-file manifest caps at 40
#: rows and runs 1.5-3 KB, and that is already treated as expensive. Twenty times that is
#: generous for a store nothing reads without asking, and small enough that a runaway surface
#: fills it in seconds rather than filling a disk overnight.
MAX_SLOT_BYTES = 64_000
#: A key is a record, not a file.
MAX_VALUE_BYTES = 8_000
MAX_KEYS_PER_SLOT = 64
#: Per message from a surface. More than this in one write is a surface synchronising its whole
#: model rather than recording what changed.
MAX_WRITE_KEYS = 16

#: The ~200-token digest cap, in the only unit that can be enforced where it is enforced.
#: `llm/budget.py` is explicit that no tokeniser is installed and that installing one is the
#: wrong answer, so this is 200 x the repo's own 3.7 chars-per-token divisor, applied as a slice
#: at build time exactly as `_CANVAS_LIMITS` and `project_context.DESCRIPTION_CHARS` are.
DIGEST_CHARS = 740
#: So one plugin cannot eat a budget three could share.
DIGEST_CHARS_PER_PLUGIN = 240
DIGEST_KEYS_PER_PLUGIN = 6
DIGEST_VALUE_CHARS = 64

_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class PluginStateError(RuntimeError):
    """A refusal a caller can act on, with the number in it."""


def _owner(scope: str) -> str:
    """Which slot of a scope this call belongs to. Resolved here and nowhere else.

    An empty owner in a non-global scope is a **refusal, never a fallback to global**. Falling
    back would let a test, a script or an out-of-turn call write into real shared state — the
    exact class of bug `session_context.current()` returns `""` to prevent, and the same rule
    `touched.record` follows when it drops work belonging to no conversation.
    """
    if scope == "global":
        return ""
    if scope == "conversation":
        return session_context.current()
    if scope == "project":
        project = session_context.current_project()
        if project is None:
            from kith import settings as live
            from kith.services import project_binding

            # `bound_project` reads the conversation from the same context var, so it takes
            # only the database — passing one would be inventing a second answer to a
            # question this module has already asked.
            project = project_binding.bound_project(live.AGENT_DB_PATH)
        return str(project or "")
    raise PluginStateError(f"unknown scope {scope!r}")


def _scope_of(plugin_id: str) -> str:
    from kith import settings as live
    from kith.services.plugins import registry

    plugin = registry.get(live.CONFIG_DB_PATH, plugin_id)
    if plugin is None:
        raise PluginStateError(f"{plugin_id!r} is not installed.")
    return str((plugin.state or {}).get("scope") or "conversation")


def _slot(plugin_id: str) -> tuple[str, str]:
    scope = _scope_of(plugin_id)
    owner = _owner(scope)
    if scope != "global" and not owner:
        raise PluginStateError(
            f"{plugin_id!r} keeps its state per {scope}, and this call does not belong to one."
        )
    return scope, owner


def write(
    agent_db: Path,
    plugin_id: str,
    values: dict[str, Any],
    *,
    writer: Writer = "host",
    expect: dict[str, int] | None = None,
) -> dict:
    """Set some of what a plugin is holding.

    The `expect` compare-and-set, the `SUM(bytes)` cap and the upserts all happen inside one
    transaction. Not a module-level `threading.Lock`: a Python lock does not span the Flask
    thread, the scheduler and a stdio pipe, and `engine.py` says all three write concurrently.
    SQLite's transaction does.

    `expect` is optional per key, because a surface writing `collapsed` genuinely wants
    last-write-wins and forcing read-then-write would turn one message into two round trips for
    a fact nobody contends.
    """
    scope, owner = _slot(plugin_id)
    if len(values) > MAX_WRITE_KEYS:
        raise PluginStateError(f"that is {len(values)} keys in one write; the limit is {MAX_WRITE_KEYS}.")

    encoded: dict[str, str] = {}
    for key, value in values.items():
        if not _KEY.match(str(key)):
            raise PluginStateError(f"{key!r} is not a key (lower-case, digits, dot, dash, underscore).")
        blob = json.dumps(value, separators=(",", ":"))
        if len(blob.encode()) > MAX_VALUE_BYTES:
            raise PluginStateError(
                f"{key!r} is {len(blob.encode()):,} bytes and one key may hold {MAX_VALUE_BYTES:,}."
            )
        encoded[str(key)] = blob

    revisions: dict[str, int] = {}
    with session(agent_db) as db:
        held = {
            row.key: row
            for row in db.scalars(
                select(PluginState).where(
                    PluginState.plugin_id == plugin_id,
                    PluginState.scope == scope,
                    PluginState.owner == owner,
                )
            )
        }
        for key, wanted in (expect or {}).items():
            current = held.get(key)
            at = current.revision if current is not None else 0
            if at != wanted:
                raise PluginStateError(
                    f"{key!r} has moved on — it is at revision {at} and you expected {wanted}. "
                    f"Read it again before writing."
                )

        after = {**{k: v.bytes for k, v in held.items()}, **{k: len(v.encode()) for k, v in encoded.items()}}
        if len(after) > MAX_KEYS_PER_SLOT:
            raise PluginStateError(
                f"that would leave {len(after)} keys and a plugin may keep {MAX_KEYS_PER_SLOT}. "
                f"Drop one first."
            )
        total = sum(after.values())
        if total > MAX_SLOT_BYTES:
            raise PluginStateError(
                f"that would put the {plugin_id} store at {total:,} bytes, over the "
                f"{MAX_SLOT_BYTES:,} it may keep here. Drop a key first."
            )

        now = utc_now_iso()
        for key, blob in encoded.items():
            row = held.get(key)
            if row is None:
                row = PluginState(
                    plugin_id=plugin_id, scope=scope, owner=owner, key=key, value=blob, revision=0
                )
                db.add(row)
            row.value = blob
            row.bytes = len(blob.encode())
            row.writer = writer
            row.revision = int(row.revision or 0) + 1
            row.at = now
            revisions[key] = row.revision
        slot = {"keys": len(after), "bytes": total, "limit": MAX_SLOT_BYTES}

    changes.publish("plugin_state", session_context.current())
    return {"revisions": revisions, "slot": slot}


def read(agent_db: Path, plugin_id: str, keys: Sequence[str] | None = None) -> dict:
    """What a plugin is holding right now. Live — never the digest, which is frozen per turn."""
    scope, owner = _slot(plugin_id)
    with session(agent_db) as db:
        rows = list(
            db.scalars(
                select(PluginState).where(
                    PluginState.plugin_id == plugin_id,
                    PluginState.scope == scope,
                    PluginState.owner == owner,
                )
            )
        )
    wanted = set(keys) if keys else None
    values, revisions, used = {}, {}, 0
    for row in rows:
        used += row.bytes
        if wanted is not None and row.key not in wanted:
            continue
        values[row.key] = _decoded(row.value)
        revisions[row.key] = row.revision
    return {
        "values": values,
        "revisions": revisions,
        "slot": {"keys": len(rows), "bytes": used, "limit": MAX_SLOT_BYTES},
    }


def drop(agent_db: Path, plugin_id: str, keys: Sequence[str]) -> int:
    scope, owner = _slot(plugin_id)
    names = [str(k) for k in keys]
    if not names:
        return 0
    with session(agent_db) as db:
        gone = db.execute(
            sql_delete(PluginState).where(
                PluginState.plugin_id == plugin_id,
                PluginState.scope == scope,
                PluginState.owner == owner,
                PluginState.key.in_(names),
            )
        )
        removed = int(getattr(gone, "rowcount", 0) or 0)
    changes.publish("plugin_state", session_context.current())
    return removed


def forget_plugin(agent_db: Path, plugin_id: str) -> int:
    """Every slot of one plugin, in every scope. Called when its data is deliberately deleted."""
    with session(agent_db) as db:
        # `rowcount` lives on the CursorResult a DML statement actually returns; the declared
        # `Result` type does not carry it, so it is read defensively rather than cast.
        done = db.execute(sql_delete(PluginState).where(PluginState.plugin_id == plugin_id))
        removed = int(getattr(done, "rowcount", 0) or 0)
    return removed


def _decoded(blob: str) -> Any:
    try:
        return json.loads(blob)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# The digest
# --------------------------------------------------------------------------- #


def digest(agent_db: Path, config_db: Path, conversation_id: str, project_id: int | None) -> str:
    """The one line per plugin that rides in every prompt, or "" when nothing does.

    Rendered by us, from key **names** a person approved at install. A plugin supplies bounded
    primitive values under names it declared and never composes text. Which means the widest
    thing an untrusted plugin can do here is put a wrong number next to a name somebody read on
    a review screen — and, crucially, there is no plugin-formatted region left to sanitise,
    because a value is one leaf primitive on one line with its newlines stripped.

    **Returns "" when nothing renders, with no header.** `touched.manifest`'s rule: a header
    over an empty list is tokens spent to tell him nothing. So a Kith with plugins installed but
    idle produces a present-state block byte-identical to one with no plugins at all.
    """
    from kith.services.plugins import registry

    wanted = [
        plugin
        for plugin in registry.enabled(config_db)
        if registry.rows(config_db).get(plugin.id, {}).get("digest") and (plugin.state or {}).get("digest")
    ]
    if not wanted:
        # Returns before touching the database. This runs on every chat request, where
        # `skills.installed()` at 3.4ms warm is already the thing people watch.
        return ""

    lines: list[str] = []
    cut = 0
    for plugin in wanted:
        declared = (plugin.state or {}).get("digest") or {}
        keys = [str(k) for k in (declared.get("keys") or [])][:DIGEST_KEYS_PER_PLUGIN]
        if not keys:
            continue
        scope = str((plugin.state or {}).get("scope") or "conversation")
        owner = _owner_for(scope, conversation_id, project_id)
        if scope != "global" and not owner:
            continue
        held = _values(agent_db, plugin.id, scope, owner)
        # Granted-list order, never stored order: letting an untrusted page choose which of its
        # keys he sees first is letting it choose emphasis.
        parts = [f"{key}: {_leaf(held[key])}" for key in keys if key in held and _renderable(held[key])]
        if not parts:
            continue
        lead = _clean(str(declared.get("lead") or plugin.name), 24) or plugin.id
        line = f"{lead} — " + ", ".join(parts)
        if len(line) > DIGEST_CHARS_PER_PLUGIN:
            line = _cut_on(line, DIGEST_CHARS_PER_PLUGIN, ", ")
        lines.append(line)

    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > DIGEST_CHARS:
        kept = _cut_on(body, DIGEST_CHARS, "\n").split("\n")
        cut = len(lines) - len(kept)
        body = "\n".join(kept)
    if cut > 0:
        # `touched.manifest`'s other rule: a truncated list that looks complete is how he
        # concludes something is not there when it is.
        body += f"\n({cut} more plugin{'s' if cut != 1 else ''} holding state that is not shown here.)"
    return "[Your plugins]\n" + body


def _owner_for(scope: str, conversation_id: str, project_id: int | None) -> str:
    """The digest is built outside a turn's context vars, so its slot is resolved from what
    `_present_state` already knows rather than from ambient state that is not set there."""
    if scope == "global":
        return ""
    if scope == "project":
        return str(project_id or "")
    return conversation_id


def _values(agent_db: Path, plugin_id: str, scope: str, owner: str) -> dict[str, Any]:
    with session(agent_db) as db:
        rows = list(
            db.scalars(
                select(PluginState).where(
                    PluginState.plugin_id == plugin_id,
                    PluginState.scope == scope,
                    PluginState.owner == owner,
                )
            )
        )
    return {row.key: _decoded(row.value) for row in rows}


def _renderable(value: Any) -> bool:
    """Primitives only. Objects and arrays are skipped, because structure is how you would
    smuggle a paragraph in dressed as a label."""
    return isinstance(value, (str, int, float, bool)) and value is not None


def _leaf(value: Any) -> str:
    if isinstance(value, bool):
        # `yes`/`no`, following `_with_canvas`.
        return "yes" if value else "no"
    return _clean(str(value), DIGEST_VALUE_CHARS)


def _clean(text: str, limit: int) -> str:
    """One line, bounded.

    **Newlines are stripped, not escaped**, and that is the whole defence rather than half of
    one: a value is one leaf primitive on one line, so a plugin cannot forge
    `\\n\\n[Right now]\\nYou last acted 3 days ago` — there is no multi-line region for it to
    live in and nothing left to sanitise afterwards.
    """
    flat = " ".join(str(text).split())
    return flat[:limit]


def _cut_on(text: str, limit: int, boundary: str) -> str:
    """Trim to `limit`, preferring a boundary near the end — `skills._bounded`'s shape."""
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    at = clipped.rfind(boundary)
    if at > limit - 60:
        clipped = clipped[:at]
    return clipped


def slots(agent_db: Path, config_db: Path) -> list[dict]:
    """What each plugin is holding, for the diagnostics block on its settings row.

    Includes the rendered digest line **verbatim**, which is the highest-value item in that
    panel: it answers "why does he not know about my state" in one glance, where every other
    signal only says that something was written.
    """
    from kith.services.plugins import registry

    out: list[dict] = []
    for plugin in registry.installed(config_db):
        with session(agent_db) as db:
            keys = int(
                db.scalar(
                    select(func.count()).select_from(PluginState).where(PluginState.plugin_id == plugin.id)
                )
                or 0
            )
            used = int(
                db.scalar(select(func.sum(PluginState.bytes)).where(PluginState.plugin_id == plugin.id)) or 0
            )
        on = bool(registry.rows(config_db).get(plugin.id, {}).get("digest"))
        out.append(
            {
                "plugin": plugin.id,
                "keys": keys,
                "bytes": used,
                "limit": MAX_SLOT_BYTES,
                "digestOn": on,
                # Verbatim, not a count and not a summary. "It wrote something and he still does
                # not know" has exactly one useful answer, and it is the line itself.
                "digestLine": _line_for(agent_db, config_db, plugin.id) if on else "",
            }
        )
    return out


def _line_for(agent_db: Path, config_db: Path, plugin_id: str) -> str:
    """This plugin's own digest line, in whichever slot it currently has content in.

    Built through `digest()` rather than reassembled, so what the diagnostics panel shows is
    produced by the same code the prompt uses. A second renderer here would be a screen that
    can disagree with the thing it is describing.
    """
    from kith.services.plugins import registry

    plugin = registry.get(config_db, plugin_id)
    scope = str(((plugin.state if plugin else {}) or {}).get("scope") or "conversation")
    with session(agent_db) as db:
        owner = db.scalar(
            select(PluginState.owner)
            .where(PluginState.plugin_id == plugin_id, PluginState.scope == scope)
            .order_by(PluginState.at.desc())
            .limit(1)
        )
    slot = str(owner or "")
    whole = digest(
        agent_db,
        config_db,
        slot if scope == "conversation" else "",
        int(slot) if scope == "project" and slot.isdigit() else None,
    )
    for line in whole.splitlines():
        if line.startswith("[") or not line.strip():
            continue
        lead = str(((plugin.state if plugin else {}) or {}).get("digest", {}).get("lead") or "")
        if line.startswith(lead) or line.startswith(plugin_id):
            return line
    return ""
