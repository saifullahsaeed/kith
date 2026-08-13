"""Every import points down, and the exceptions are written down rather than discovered.

This codebase had 19 top-level import cycles and 128 imports written *inside function bodies*
to dodge them. A deferred import is not a style choice — it is the marker left at the exact
spot where the layering broke, because the module genuinely cannot be imported at the top of
the file without Python refusing to load it.

The problem was never that anyone chose badly. It is that nothing said which direction an
import was allowed to go, so the only feedback was an `ImportError` at startup — which a
local import silences. The layering was therefore enforced by whoever remembered it.

**The rule.** Each top-level package has a rank. An import may go down or sideways, never up:

    0  kernel, settings   the runtime primitives — clock, tuning, turn context, permissions,
                          events. These import nothing else in kith, which is what makes them
                          safe for every layer above to reach for.
    1  domain             pure types and rules. No I/O, no storage.
    2  infra, llm         storage, the workspace, providers. Peers: neither may import the
                          other, because nothing needs it and the cycle would be immediate.
    3  services           orchestration.
    4  api, tools         adapters. Both do the same job — validate, delegate, serialise —
                          for two different callers, HTTP and the model. Nothing may import
                          an adapter, which is the rule `services -> tools` breaks today.

`tools/` sitting at rank 4 rather than below `services/` is the single largest correction
here. 44 of the 65 upward edges this test was written for were `tools/` importing
`services/`, and every one of them was a tool handler calling the thing it delegates to —
which is exactly what a route module already does, one rank up, uncontroversially.

**Function-local imports count.** They are the ones that matter most: a top-level violation
fails loudly at import, so it cannot survive. A local one runs fine forever and is invisible
until someone maps the graph. `_edges` therefore walks the whole AST rather than reading the
module header.

**The allow-list shrinks, and only shrinks.** Fixing a violation without touching this file
is meant to pass — the assertion is a subset check, not equality. Adding one fails. Each
tranche of the layering work deletes entries from `ALLOWED`, and when it is empty this test
becomes the plain statement that the rule holds.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

#: Where a package sits. Lower may not import higher.
#:
#: Root modules are ranked too, by what they actually do rather than by living at the top of
#: the tree: `settings.py` reads the environment and nothing else, so it is a kernel citizen,
#: while `config.py` builds a chat request out of the persona and the skill index, which is
#: orchestration wearing a configuration name.
#:
#: `app` is ranked above every adapter because it is the composition root — it wires the
#: layers together, so reaching into all of them is its job. It is ranked rather than left
#: out: an unranked package is skipped as importer *and* as target, so omitting it would
#: hand `kith/app.py` the same silent exemption `kith/__init__.py` earns by being empty,
#: except that `app.py` is not empty and would stop being checked at all.
RANK = {
    "kernel": 0,
    "settings": 0,
    "domain": 1,
    "infra": 2,
    "llm": 2,
    "services": 3,
    "config": 3,
    "schemas": 3,
    "api": 4,
    "tools": 4,
    "app": 5,
}

#: Packages that share a rank and still may not import each other.
#:
#: Equal rank means "neither is above the other", which the downward rule cannot express: it
#: only ever fires on `RANK[there] > RANK[here]`, so a sideways import is invisible to it.
#: For most peers that is right — two modules in `services/` calling each other is ordinary.
#: For these it is not, and the pairing has to be stated separately or the docstring above is
#: describing a rule nothing enforces.
#:
#: `infra` and `llm` are peers by construction: storage and the provider transport. Neither
#: is built on the other, and an edge either way is the beginning of a cycle that the rank
#: rule would never report.
PEERS: tuple[tuple[str, str], ...] = (("infra", "llm"),)

SOURCE = Path(__file__).resolve().parent.parent / "kith"


#: Violations that exist today, as `(importer, imported)` with the count at the time of
#: writing. Present so this test can land *before* the refactor that empties it, and so the
#: work has a number that goes down.
#:
#: Each line is a tranche's worth of work, in the order they are being done:
#:
#: * `infra -> services` and `llm -> services` are `session_context`, `permissions`,
#:   `changes` and `tuning` — runtime primitives filed as orchestration.
#: * `services -> tools` is the five edges that have to invert before `tools/` can be an
#:   adapter: the loop takes a tool host rather than importing the registry, and
#:   `brain/kinds.py` stops reaching into a tool module for two private functions.
#: * `domain -> infra` is `clock` reading a stored timezone.
#: * `services -> api` is `scheduler` importing `_build_messages`, `_Recorder` and `_turn` —
#:   three *private* functions — out of `api/routes/chat.py`. Not a slip: a reminder firing
#:   runs the same turn a typed message does, so that machinery was never route-shaped. It
#:   retires when the turn moves out of the route, not before.
#: * `infra <-> llm` are the two peer edges, and they were invisible until `PEERS` existed:
#:   the rank rule only ever fires downward-to-upward, so two packages declared equal could
#:   import each other freely while the docstring said they must not. `websearch` reaches for
#:   the OpenRouter transport to run a search through it; `openai_compat` reaches for
#:   `config_store` to read a stickiness id. Each is one edge, and each is the beginning of
#:   the cycle the pairing exists to prevent.
#:
#: Struck off so far, 38 -> 27:
#:
#: * `settings -> services` (1), which was `describe()` fetching the tunables for the
#:   startup log. The caller joins the two halves now.
#: * `infra -> config` (7) and `llm -> config` (3), which were three separate things wearing
#:   one name. Six sites wanted `AGENT_DB_PATH` or `CONFIG_DB_PATH`, now in `settings.py`
#:   where the folder they hang off already lived. Three wanted the `Config` dataclass, now
#:   `domain/chat.py` — a frozen dataclass was costing three import cycles. One, `websearch`,
#:   called `default_config()`, and every other function in that file already took the config
#:   as a parameter; now `search` does too and its adapter resolves it.
ALLOWED: dict[tuple[str, str], int] = {
    ("infra", "services"): 16,
    ("services", "tools"): 5,
    ("llm", "services"): 2,
    ("domain", "services"): 2,
    ("domain", "infra"): 1,
    ("services", "api"): 1,
    ("infra", "llm"): 1,
    ("llm", "infra"): 1,
}


def _package(module: str) -> str:
    """The top-level package a dotted `kith.…` name belongs to.

    `kith.services.mcp.manager` is `services`; the bare `kith` package itself is `__init__`,
    which is unranked and therefore never a party to a violation — it is the composition
    root and is allowed to import anything, because wiring the layers together is its job.
    """
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else "__init__"


def _targets(node: ast.AST) -> list[str]:
    """Every `kith.…` module one import statement refers to.

    `from kith import tools` needs its own branch: the module is `kith` and the package being
    imported is in `names`, so reading `node.module` alone would score it as touching the
    composition root and miss it entirely. That form is how the agent loop imports the tool
    registry — one of the five edges this file exists to retire.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names if alias.name.startswith("kith")]
    if isinstance(node, ast.ImportFrom) and node.module == "kith":
        return [f"kith.{alias.name}" for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kith"):
        return [node.module]
    return []


def _edges() -> dict[tuple[str, str], list[str]]:
    """Every cross-package import in the tree, wherever in the file it is written.

    The whole AST, not the module header. A local import is a real dependency — Python only
    tolerates it because the module is already loaded by the time the function runs — and it
    is the form every existing violation takes, precisely because the top-level form does not
    survive being written.
    """
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    for path in sorted(SOURCE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = ".".join(path.relative_to(SOURCE.parent).with_suffix("").parts)
        here = _package(module.removesuffix(".__init__"))
        if here not in RANK:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for target in _targets(node):
                there = _package(target)
                if there == here or there not in RANK:
                    continue
                banned = RANK[there] > RANK[here] or (here, there) in _FORBIDDEN_PEERS
                if banned:
                    found[(here, there)].append(f"{path.relative_to(SOURCE.parent)}:{node.lineno}")
    return dict(found)


#: Both directions of every pair in `PEERS`, since neither may import the other.
_FORBIDDEN_PEERS = frozenset(pair for a, b in PEERS for pair in ((a, b), (b, a)))


def test_no_import_points_up_a_layer():
    """The rule, minus what is already known to break it."""
    edges = _edges()
    unexpected = {pair: sorted(where) for pair, where in edges.items() if len(where) > ALLOWED.get(pair, 0)}
    assert not unexpected, "new upward imports:\n" + "\n".join(
        f"  {a}/ -> {b}/ ({len(where)} > {ALLOWED.get((a, b), 0)} allowed)\n"
        + "\n".join(f"      {one}" for one in where)
        for (a, b), where in sorted(unexpected.items())
    )


def test_the_allow_list_has_nothing_stale_in_it():
    """A fixed violation must be struck off, not left as a budget for the next one.

    Without this the list only ever grows a ceiling: someone retires `domain -> services`,
    the entry stays, and a year later a different upward import lands under the old
    allowance and passes. The count is meant to be the truth about today, so it is asserted
    in both directions.
    """
    edges = {pair: len(where) for pair, where in _edges().items()}
    stale = {
        pair: (allowed, edges.get(pair, 0))
        for pair, allowed in ALLOWED.items()
        if edges.get(pair, 0) < allowed
    }
    assert not stale, "these are fixed — lower or delete them in ALLOWED:\n" + "\n".join(
        f"  {a}/ -> {b}/  allows {allowed}, actual {actual}"
        for (a, b), (allowed, actual) in sorted(stale.items())
    )


def test_the_kernel_imports_nothing_from_kith():
    """What makes the kernel usable from every layer is that it needs none of them.

    Asserted separately from the rank rule because it is a stronger claim than "does not
    import upward": rank 0 has nothing below it, so any `kith.` import at all from inside
    `kernel/` is a mistake, including a sideways one to `settings`. The moment that stops
    being true, the packages beneath it start needing local imports again and the whole
    exercise unwinds.
    """
    kernel = SOURCE / "kernel"
    if not kernel.is_dir():
        return  # not extracted yet — the rank rule above is the only guard until it is
    offences = []
    for path in sorted(kernel.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for target in _targets(node):
                if _package(target) != "kernel":
                    offences.append(f"{path.relative_to(SOURCE.parent)}:{node.lineno} -> {target}")
    assert not offences, "the kernel reaches out of itself:\n" + "\n".join(f"  {one}" for one in offences)
