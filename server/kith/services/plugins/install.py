"""Installing a plugin, and the order that makes every abort leave something inert.

The transaction is three steps and the order is the specification:

1. **The grant**, if the plugin bundles a program. A grant for a plugin with no row is provably
   inert — nothing reads a signature whose plugin does not load — and `sweep()` collects it.
2. **One `os.rename`.** The copy goes to a dot-prefixed staging folder *under the plugins root*,
   so the commit is a single syscall on one filesystem. The window in which the folder is
   half-there is that syscall wide.
3. **The row, last**, because the row is the existence test. A crash before it leaves a folder
   nothing reads and a grant nothing can use.

`inspect()` is the other half of the rule `services/mcp/` states as **trying is not saving**: it
parses, validates and prices a folder and writes nothing at all, so a review screen can describe
a plugin without the act of reviewing having installed it.
"""

from __future__ import annotations

import secrets
import shutil
import time
from pathlib import Path

from kith.domain.plugins import MANIFEST, Plugin, PluginError, parse
from kith.infra import confinement, permissions
from kith.kernel import changes
from kith.services.plugins import registry

#: What every installed plugin may add to each request, together.
#:
#: A per-plugin ceiling that ten plugins each satisfy while jointly doubling the prefix is not a
#: ceiling. Set against the measured prefix this design sits beside — roughly 8,700 tokens of
#: persona and skill index — so the installed set may add about a quarter of it before the
#: refusal names which plugins are the expensive ones.
MAX_INSTALLED_PROMPT_CHARS = 8_000


def inspect(source: Path, config_db: Path) -> dict:
    """Read a folder as a plugin and say what installing it would mean. Writes nothing.

    Every string a plugin contributes comes back as *data* — id, name, version, command, args,
    env names, reach paths — and none of it is prose this app will speak. There is deliberately
    no `why` or `description` field on the review screen's sentences: `permissions._refuse` is
    explicit that a justification is "only ever supplied in code, never from anything a model
    composed", and a third party's sentence on the screen where someone grants disk access is
    that same mistake one layer out.
    """
    directory = Path(source).expanduser()
    if not directory.is_dir():
        raise PluginError(f"{directory} is not a folder.")
    if not (directory / MANIFEST).is_file():
        raise PluginError(f"There is no {MANIFEST} in {directory.name}.")

    plugin = parse(directory)
    faults = list(plugin.problems())
    faults += _collision_faults(plugin, config_db)
    if plugin.server is not None:
        try:
            confinement.resolve(plugin.server.reach, plugin.id)
        except confinement.ConfinementError as refused:
            faults.append(str(refused))
    if plugin.surfaces:
        from kith.services.plugins import documents

        # Checked here rather than at serve time. A surface whose stylesheet lives on a CDN
        # renders unstyled with nothing on screen saying why — the seal has no network — and the
        # honest moment to say so is while somebody is deciding, once, with the href quoted.
        faults += documents.asset_faults(plugin)

    already = registry.row(config_db, plugin.id)
    together = registry.installed_prompt_chars(config_db) + plugin.prompt_chars()
    if already is None and together > MAX_INSTALLED_PROMPT_CHARS:
        expensive = sorted(registry.enabled(config_db), key=lambda p: -p.prompt_chars())[:2]
        naming = ", ".join(f"{p.name} (~{p.prompt_tokens():,})" for p in expensive)
        faults.append(
            f"Installed plugins would add about {together:,} characters to every request, over "
            f"the {MAX_INSTALLED_PROMPT_CHARS:,} limit. The most expensive are {naming}."
        )

    return {
        "plugin": plugin.public(),
        "faults": faults,
        "installable": not faults,
        "replacing": already or None,
        "signature": registry.spawn_signature(plugin),
        "promptChars": plugin.prompt_chars(),
        "promptTokens": plugin.prompt_tokens(),
        "installedPromptChars": together,
    }


def _collision_faults(plugin: Plugin, config_db: Path) -> list[str]:
    """What this plugin would shadow. Named with its owner, never resolved silently."""
    faults: list[str] = []
    for other in registry.installed(config_db):
        if other.id == plugin.id:
            continue
        if plugin.server and other.server and other.id == plugin.id:
            faults.append(f"{other.name} already contributes a server called {plugin.id!r}.")
    from kith.services import skills as skills_service

    if plugin.skills:
        mine = {name for name in plugin.skills}
        for skill in skills_service.installed():
            # The person's own folder wins a collision, so this is a refusal at the door rather
            # than a plugin that installs and then silently contributes nothing.
            if skill.name in mine and not getattr(skill, "owner", ""):
                faults.append(
                    f"You already have a skill called {skill.name!r}. Rename the plugin's copy "
                    f"or remove yours — a plugin cannot shadow a skill you wrote."
                )
    return faults


def install(
    config_db: Path, source: Path, *, env: dict[str, str] | None = None, standing: bool = True
) -> Plugin:
    """Copy a plugin in and record the decision. See the module docstring for the order."""
    report = inspect(Path(source), config_db)
    if not report["installable"]:
        raise PluginError(report["faults"][0])

    place = registry.root()
    staging = place / registry.STAGING / secrets.token_hex(8)
    staging.parent.mkdir(parents=True, exist_ok=True)
    # `copy` rather than `copy2`: metadata is dropped exactly as `skills.root()` drops it when
    # seeding, so an archive's ownership and times do not travel into the install.
    shutil.copytree(Path(source).expanduser(), staging, copy_function=shutil.copy)

    try:
        plugin = parse(staging)
        if plugin.problems():
            raise PluginError(plugin.problems()[0])

        # Two grants, and they are independent — which they were not for one commit, and the bug
        # is worth the comment. The spawn grant was written only when the plugin bundled a
        # server, and the *command* grant was written inside the same branch. So a plugin with a
        # surface and no subprocess installed with nothing granted, and the very first command
        # the model called stopped the turn with a dialog — for a plugin that runs no program at
        # all and therefore has nothing a dialog could usefully be about.
        if plugin.server is not None:
            # Built before the grant, so a boundary that will not compile is a refused install
            # rather than a granted plugin that starts unconfined. `write_profile` proves it by
            # running it over `/usr/bin/true` — with the person still here, rather than at first
            # start where it would look like the server being broken.
            confinement.write_profile(
                plugin.id,
                confinement.resolve(plugin.server.reach, plugin.id),
                # The command it will actually run, so the profile can let the interpreter read
                # itself. Without it a plugin whose server is a virtualenv python dies before
                # any of its own code runs — see `confinement.runtime_root`.
                runtime=_runtime_of(plugin.server.command),
            )

        signature = registry.spawn_signature(plugin)
        if signature:
            permissions.grant_now(signature, standing=standing)
        if plugin.commands:
            # Covers every declared command through `granted()`'s segment-wise containment.
            # Written here, at the moment a person approved the install, so the per-call gate
            # never prompts in normal operation — it exists for a revocation landing mid-turn,
            # and to carry the `permission` envelope when it does.
            permissions.grant_now(f"plugin:{plugin.id}:*", standing=standing)

        destination = place / plugin.id
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    held = registry.rows(config_db)
    previous = held.get(plugin.id, {})
    held[plugin.id] = {
        "version": plugin.version,
        # An upgrade is not a chance to overwrite a decision: the enable flag, the digest switch
        # and the environment values are all things a person chose, and they survive.
        "enabled": previous.get("enabled", True),
        "digest": previous.get("digest", False),
        "env": {**(previous.get("env") or {}), **(env or {})},
        "installedAt": previous.get("installedAt") or _now(),
        "updatedAt": _now(),
        "source": str(source),
    }
    held[plugin.id].pop("retiredAt", None)
    registry.write_rows(config_db, held)
    changes.publish("plugin")
    _reconnect(config_db)
    return parse(place / plugin.id)


def set_enabled(config_db: Path, plugin_id: str, on: bool) -> dict:
    record = registry.patch_row(config_db, plugin_id, {"enabled": bool(on)})
    changes.publish("plugin")
    _reconnect(config_db)
    return record


def set_digest(config_db: Path, plugin_id: str, on: bool) -> dict:
    """Turning a digest on is a person's act and can only happen here.

    A manifest may say it *has* one to offer; it can never switch it on. This is the only thing
    in the design that spends tokens on every turn forever, so the decision belongs in the same
    place as every other decision — a row, not a file the publisher wrote.
    """
    record = registry.patch_row(config_db, plugin_id, {"digest": bool(on)})
    changes.publish("plugin")
    return record


def set_env(config_db: Path, plugin_id: str, env: dict[str, str]) -> dict:
    """Merged, and an empty value removes — the same rule `mcp.manager.save` follows, for the
    same reason: a screen that never receives a value cannot send one back."""
    record = registry.row(config_db, plugin_id) or {}
    merged = {**(record.get("env") or {}), **{str(k): str(v) for k, v in env.items()}}
    updated = registry.patch_row(config_db, plugin_id, {"env": {k: v for k, v in merged.items() if v != ""}})
    changes.publish("plugin")
    _reconnect(config_db)
    return updated


def uninstall(config_db: Path, plugin_id: str, *, delete_state: bool = False) -> None:
    """Remove a plugin, revoke what let it run, and **mark its state rather than deleting it.**

    `skills.remove()` trashes rather than destroys, saying in as many words that it is "a folder
    of their writing, not a cache". A plugin's stored state is the same bytes — and `disable`
    already keeps them — so destroying them here would be one set of bytes treated two ways with
    no reason anyone could state. Marked, swept after `RETIRED_DAYS`, and a reinstall inside
    that window gets them back.
    """
    plugin = registry.get(config_db, plugin_id)
    signature = registry.spawn_signature(plugin) if plugin else ""

    held = registry.rows(config_db)
    if plugin_id not in held:
        raise PluginError(f"{plugin_id!r} is not installed.")
    if delete_state:
        held.pop(plugin_id, None)
    else:
        held[plugin_id] = {**held[plugin_id], "enabled": False, "retiredAt": _now()}
    registry.write_rows(config_db, held)

    # Revoked before the folder goes, because both signatures are derived from the manifest —
    # computing them afterwards would revoke nothing and leave a grant that a reinstall under
    # the same id would silently inherit.
    if signature:
        permissions.revoke(signature)
    permissions.revoke(f"plugin:{plugin_id}:*")
    # The profile goes with the grant. Its *storage* does not — that is the person's data, and
    # it follows the same thirty-day rule as the plugin's state.
    confinement.forget(plugin_id)

    directory = registry.root() / plugin_id
    if directory.is_dir():
        from kith.infra import workspace

        try:
            workspace.trash_path(directory)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)

    if delete_state:
        _forget_state(plugin_id)
    changes.publish("plugin")
    _reconnect(config_db)


def sweep(config_db: Path) -> list[dict]:
    """Tidy at app start: abandoned staging folders, and state past its retirement.

    Orphan grants are deliberately *not* swept. A grant whose plugin does not load is inert —
    nothing reads it — and revoking it would silently discard consent for a plugin that is
    merely broken today, which is the one direction where being wrong costs a person a prompt
    they already answered.
    """
    done: list[dict] = []
    staging = registry.root() / registry.STAGING
    if staging.is_dir():
        for leftover in staging.iterdir():
            shutil.rmtree(leftover, ignore_errors=True)
            done.append({"kind": "staging", "id": leftover.name})

    cutoff = time.time() - (registry.RETIRED_DAYS * 86_400)
    held = registry.rows(config_db)
    expired = [
        plugin_id
        for plugin_id, record in held.items()
        if record.get("retiredAt") and _parsed(record["retiredAt"]) < cutoff
    ]
    for plugin_id in expired:
        held.pop(plugin_id, None)
        _forget_state(plugin_id)
        done.append({"kind": "retired", "id": plugin_id})
    if expired:
        registry.write_rows(config_db, held)
    return done


def _forget_state(plugin_id: str) -> None:
    """Drop everything a plugin was holding. Only ever reached by a deliberate delete or by the
    thirty-day sweep — never by a plain uninstall, which marks instead."""
    from kith import settings as live
    from kith.services.plugins import state

    try:
        state.forget_plugin(live.AGENT_DB_PATH, plugin_id)
    except Exception as exc:  # pragma: no cover - a tidy-up must not fail the removal
        print(f"[kith] plugins: could not clear {plugin_id}'s state ({exc})")


def _reconnect(config_db: Path) -> None:
    """Bring the MCP set back in line with what is now configured.

    `mcp.manager.connect` is a reconciler, so this one call covers install, uninstall, enable
    and disable — including the retire, which is what stops a disabled plugin's process rather
    than merely marking it. Failures are swallowed: a server that will not start is a row on a
    settings screen, not a reason for the install to have failed.
    """
    try:
        from kith.services import tuning
        from kith.services.mcp import manager

        manager.connect(
            config_db,
            float(tuning.value("mcp_connect_timeout")),
            float(tuning.value("mcp_call_timeout")),
        )
    except Exception as exc:  # pragma: no cover - a settings-screen concern, not an install one
        print(f"[kith] plugins: could not reconcile servers ({exc})")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parsed(stamp: str) -> float:
    try:
        return time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0.0


def _runtime_of(command: str) -> str:
    """The absolute path of a command, resolved the way the shell would resolve it."""
    import shutil

    return shutil.which(command) or command
