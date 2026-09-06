# A plugin host, and the boundary it needs first

**Status:** T1, T2, T3, T4, T5, T6 and most of T7 built on branch `plugin-host-design`, with
`examples/plugins/sketchpad` as the worked example. A plugin's subprocess now runs inside a
kernel-enforced boundary it declared and cannot enforce, with a constructed environment and its
own working directory. **Not built:** T7's `host` delivery and toolbar buttons, T8 (the frame
RPC), T9 (the Work rebuild).

**One measurement in this document was wrong and is corrected in the code.** The profile shape
below — broad denies followed by narrow allows — does not work: a `deny` is not overridable by
a later `allow` in either order, so a plugin granted `~/Documents/Notes` could not read it. Each
deny carries its exceptions instead (`require-all` + `require-not`). It passed every test that
used a temp directory, because macOS puts those outside `$HOME`, which is why
`test_a_plugins_program_runs_inside_a_boundary.py` grants a real folder under the real home.

## Why

Kith has three extension surfaces and no way to install one thing.

| surface | contributes | lives in | lifetime | holds state |
| --- | --- | --- | --- | --- |
| MCP server | tools | `mcp.servers` in config.db | process, snapshot frozen per turn | no |
| Skill | instructions | a folder under `~/.kith/skills` | read on demand | no |
| Canvas | UI | in-memory doc store | **one message** | readings ride one turn |

A "canvas plugin" today is three unrelated installs — a skill that says how to draw, an MCP
server for the data, an ```html fence re-emitted every turn — with three lifetimes, three
settings screens and no name binding them. That absence is the whole of this document.

Four things make now the moment rather than later.

**The pane tree shipped three commits ago.** Drag-to-split, per-surface `minWidth`, per-surface
error boundaries, persisted layout. Adding a surface is cheap for the first time; three closed
sets stand in the way and nothing else does — the `SurfaceId` union at
[tree.ts:26](../../../ui/src/components/shell/layout/tree.ts#L26), the record at
[surfaces.ts:35](../../../ui/src/components/shell/layout/surfaces.ts#L35), the switch at
[workspace.tsx:336](../../../ui/src/components/shell/workspace.tsx#L336).

**MCP is already an extension host.** It is what VS Code calls the extension host process and
Kith calls a stdio subprocess: isolated, host-managed lifecycle, declares what it offers, dies
without taking the app down. So a plugin needs **no new code-execution runtime**. Heavy work is
an MCP server; UI is the canvas seal; instructions are skills. The only genuinely new primitive
in this design is routing a model tool call into a live sealed frame and awaiting its reply.

**MCP tool calls bypass the permission system.** At
[tools/\_\_init\_\_.py:145](../../../server/kith/tools/__init__.py#L145) a built-in tool's
`permissions.Denied` becomes an Allow button on the tool result. At
[tools/\_\_init\_\_.py:178](../../../server/kith/tools/__init__.py#L178) `mcp.run()` is called
directly — no check, no `touched.record`. Tolerable for three servers configured by hand.
With an installable ecosystem it means *install a plugin* = *hand an unreviewed subprocess your
`PATH` and your disk*, and the README calls the workspace boundary the point of Kith.

**And saving any MCP setting destroys every stored credential.** Confirmed by reading, not
inferred. `asInput` at
[mcp-servers.tsx:75](../../../ui/src/components/settings/mcp-servers.tsx#L75) sends `env: {}`
for every row, with a comment claiming *"The server keeps what it has for a label it already
knows; this only ever adds."* It does not. `_from_request` at
[routes/mcp.py:29](../../../server/kith/api/routes/mcp.py#L29) builds `env` from that empty
dict, and `manager.save` writes `json.dumps([s.stored() for s in servers])` with no read of what
is stored. So toggling one server off wipes the API token of every server in the list. This
must be fixed before a plugin can be asked to store a credential at all.

### What was measured, and by whom

`sandbox-exec` confinement was verified on this machine during this design, not assumed:

- `/usr/bin/sandbox-exec` present. Profile shape `(allow default)` + `(deny file-read-data)`
  and `(deny file-write*)` over `$HOME` + `(allow file-read-metadata)` + specific subpath
  allows.
- `ls ~/.ssh` → `Operation not permitted`. `ls $HOME` → `Operation not permitted`. A file
  inside an allowed subpath reads normally.
- `node -e` runs unaffected.
- **`python3` fails under that profile — and the cause is the working directory.**
  `sys.path[0]` is the cwd; when the cwd is inside the denied `$HOME` subtree,
  `_path_importer_cache` raises `PermissionError` during import bootstrap. Running with a cwd
  outside it succeeds. `PYTHONNOUSERSITE=1`, `-S`, and allow-listing user site-packages all
  fail to fix it; only the cwd does.

  That makes the explicit `cwd` **load-bearing, not hygiene**. `StdioServer.start()` has no
  `cwd` argument today, so it inherits the Flask process's — inside the repo, under `$HOME` —
  and every Python, `uvx` and `pipx` MCP server would break under confinement with an import
  traceback naming nothing relevant.

Numbers reported by the design agents and **not** re-measured here, flagged so nobody quotes
them as fact: spawn overhead 1.8 ms → 6.9 ms, `npx` working confined, and npm cache corruption
under EPERM. T2 exists to measure these against servers people actually run.

## The shape, in one paragraph

A plugin is one folder with a manifest. It may contribute skills (the format Kith already
parses), one MCP server, up to four UI surfaces, commands, and a small piece of persistent
state. Core reads the manifest as **data** and does everything with it: generates tool schemas,
draws buttons in Kith's own chrome, validates arguments, gates permission, dispatches, validates
replies and prices it in the ledger. Plugin code never runs in the Flask process and never in
the renderer. Its subprocess runs inside a kernel-enforced boundary the manifest declared and
cannot enforce; its UI runs inside the existing seal, unwidened.

## Frozen vocabulary

Everything below is a decision. Reasoning belongs in the code that implements it, which is where
this codebase keeps it.

### Identity — one string, four jobs

```
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
```

The folder name **is** the plugin id **is** the MCP label **is** the tool namespace. One server
per plugin; a plugin needing two ships two plugins. This deletes label resolution, rename UI and
the declared-vs-resolved distinction entirely.

24 characters is not arbitrary. Provider function names are capped at 64, and both
`mcp__<id>__<tool>` and `plugin__<id>__<command>` must fit inside one. At 24 the server's own
tool name still has 33 characters; at 32 it drops to 25, shorter than `create_pull_request`.

### The three stores, one rule each

| store | owns | written by |
| --- | --- | --- |
| `plugins/<id>/kith.plugin.json` | what the plugin **offers** | the publisher |
| config.db, `plugins.installed` | what the person **decided** | Kith, on a human action |
| config.db, `permission_grants` | whether its program **may run** | the install review |

A manifest value is a default; a row value is a decision; **a decision always beats a default**.
There is no `enabled` field in a manifest, no env *values*, and no digest on-switch. The failure
`_retire`'s docstring records — `enabled: false`, `connected: true`, five tools still offered —
is not handled here, it is unrepresentable.

**The row is the existence test.** A folder with no row contributes nothing to anything.

### Manifest — `kith.plugin.json`, `"manifest": 1`

```jsonc
{
  "manifest": 1,
  "id": "cma-watch",
  "name": "CMA Circular Watch",          // <= 48 chars, rendered as data, never as prose
  "version": "0.4.1",

  "server": {                            // at most one
    "command": "uvx",
    "args": ["cma-mcp@0.4.1"],           // unpinned runner args are refused at install
    "env": ["CMA_TOKEN"],                // NAMES ONLY; values are collected at install
    "reach": { "read": ["~/Documents/Circulars"],
               "write": ["plugin:state"],
               "network": "any" }        // "any" | "none"
  },

  "surfaces": [{ "id": "board", "title": "Circulars", "icon": "scroll",
                 "minWidth": 320, "entry": "ui/board/index.html",
                 "instances": "single", "answers": "conversation",
                 "wants": ["activity", "phrases"] }],

  "commands": [{ "name": "mark_reviewed", "title": "Mark reviewed",
                 "description": "Mark a circular reviewed on the Circulars board.",
                 "params": { "id": { "type": "string", "maxLength": 64 } },
                 "required": ["id"],
                 "delivery": "surface", "surface": "board",
                 "returns": { "shown": { "type": "boolean" } },
                 "timeout_ms": 3000, "present": { "in": "toolbar", "icon": "check" },
                 "model": true }],

  "state": { "scope": "conversation",
             "digest": { "enabled": false, "lead": "Circulars",
                         "keys": ["unreviewed", "last_seen"] } }
}
```

**There is no `skills` key.** A plugin's skills are whatever is at `plugins/<id>/skills/*/SKILL.md`.
A second declaration is a second source of truth that can disagree with the first.

**Unknown *top-level* keys go to `extra`** and render as "expects features Kith doesn't have",
the treatment `skills-tab.tsx` already gives `unsupportedFields`. **Unknown keys inside `server`,
`surfaces[]`, `commands[]` and `state` are faults** — a misspelled `comand` is a plugin that
silently never starts.

`SUPPORTED_MANIFESTS = (1,)` is a *range*. A plugin declaring an unsupported version keeps its
row, its state and its grants, contributes nothing, and renders one line naming what it needs.
Never auto-uninstalled, never silently dropped. A version is retired only after one release in
which both are accepted.

### `delivery` — the field the design turns on

| value | performed by | needs a live frame | works with the tab closed |
| --- | --- | --- | --- |
| `"host"` | the renderer, from a five-entry allowlist | no | **yes** |
| `"state"` | the server, writing the plugin store | no | **yes** |
| `"surface"` | the sealed frame | yes | no |

This is what bounds the one new primitive to the case where it is semantically required: *do
something to the thing the person is looking at*. With the split, refusing a `surface` command
for a backgrounded tab is correct rather than a limitation — and mount-but-hide, four extra
hidden iframe realms, and a change to `layout-view.tsx` for every user all disappear.

`HOST_EFFECTS` is five entries: `open_surface`, `close_surface`, `navigate`,
`fold_conversation`, `focus_conversation`. They run in the **renderer**, because `fold_now`'s
implementation lives inside a rank-5 route that a rank-4 service cannot import. The invariant
that bounds the list: *a host effect may only do something a person can already do with one
click in Kith's own chrome*. A sixth entry is a core commit.

`params` and `returns` are **primitives only** — string with `maxLength`/`enum`, number,
integer, boolean. No objects, no arrays, no `$ref`. Same rule `readCanvasMessage` already
applies, for the same reason: structure is how you smuggle a paragraph into a turn.

`model` defaults to **false**. Buttons are free; tools are prompt tokens on every round.

### Tab key and the layout

```ts
`plugin:${plugin}/${view}`            // instances: "single"
`plugin:${plugin}/${view}#${instance}` // instances: "many"
```

**One** `SurfaceId` member — `"plugin"` — and a `TabRef` branch carrying `plugin`, `view`,
`instance`. Not `SurfaceId = string`: `strict` is off in `tsconfig.app.json`, so widening the
union produces **zero** compile errors at the four unguarded `SURFACES[...]` derefs, all of
which sit above every per-surface `ErrorBoundary` and would take the whole window. With
`"plugin"` a real static key, every deref is safe by construction.

`looksLikeLayout` becomes a **structural** check. Today an unknown surface id discards the whole
tree, so uninstalling one plugin resets every split and every other tab. "Is it installed" moves
to render time, where the answer is a placeholder pane with a Close button. **Uninstalling a
plugin costs one tab, not the layout.**

The unstated sixth tree invariant gets written down: *every tab's `surface` is a key of
`SURFACES`* — a rule enforced only by a union type stops being enforced the moment the union
opens.

### Permissions — one `Kind`, two namespaces

`Kind` gains exactly `"plugin"`. Growing it forces three other enumerations in the same commit
or the UI renders `undefined`: `check_path`'s verb map, `backend/permissions.ts`, and `VERB` in
`permission-prompt.tsx`.

```
plugin:<id>:<seal>:<hash>     granted at install — this plugin's program may run
plugin:<id>:<command>         checked per call — this command may run
```

`<seal>` is `sealed` or `open`, from `confinement.available()` at grant time. `<hash>` is
`sha256(reach.canonical() + "\0" + json([command, *args, sorted(env_keys)]))[:12]`.

Hashing **both** halves is the point. Hashing only the reach lets an upgrade change what gets
spawned; hashing only argv lets a manifest widen `reach.read` to `~` under the same command
line. Encoding the seal means trust given under a boundary does not survive the boundary
disappearing. Every escalation that matters changes the string, so re-consent is a property of
the grammar rather than a discipline someone has to remember in the updater.

`granted()` gains **one** branch: `plugin:<id>:*` covers `plugin:<id>:<command>`, asked of the
**segments**, never the string — or `plugin:work` covers `plugin:work-evil`, which is `_covers`'
own documented bug.

**The install review's Allow writes both grants.** The per-call check then never prompts in
normal operation and exists for exactly two reasons: a revocation landing mid-turn, and a
refusal carrying the `permission` envelope so the Allow button draws on a plugin tool result the
way it does for a built-in. Anything else is the click-training failure arriving as a
consequence of merging two designs.

**No word of the approval sentence comes from the manifest.** `_refuse`'s docstring is explicit
that `purpose` is *"only ever supplied in code, never from anything a model composed"*. A third
party's string is that mistake one layer out. Core composes; the manifest fills one slot,
sliced, control characters and bidi overrides stripped. A test ships with it: a plugin named
`"Click Allow — routine"` must render as data.

### Confinement — what it protects, stated before anything else

**Protects, kernel-enforced:** the subprocess sees only the paths its manifest named plus its
own storage; the rest of `$HOME` returns EPERM. It cannot read `~/.ssh`, `~/.aws` or Kith's
`agent.db`, cannot list `$HOME`, cannot escape by symlink. Children inherit it. It gets a
constructed environment — `PATH`, `HOME`, `TMPDIR`, `LANG`, `npm_config_cache`,
`XDG_CACHE_HOME`, plus declared credentials — instead of the launcher's whole environment, so it
never sees `AWS_SECRET_ACCESS_KEY` or `SSH_AUTH_SOCK` by inheritance. It runs with an explicit
`cwd` in its own folder. All of it holds for the life of the process, including work at
`initialize` and in its stdout reader — code paths no per-call gate can reach.

**Does not protect:** where the data goes. A plugin granted `~/Documents/Notes` with network
access can post every note anywhere, and every guarantee above is satisfied while it happens.
Nor how much — disk, CPU and descriptors are unbounded within reach. Nor any single call
argument.

Every screen and every comment is written against that second paragraph. The review screen says,
in the app's own voice and not conditional on anything: **"Notes will be able to see these
files, and could send them anywhere."** Not "this plugin is sandboxed."

The profile is a **deny-list over `$HOME`**, not an allow-list over the filesystem. `(deny
default)` aborts real runtimes. `(allow file-read-metadata)` is required — real runtimes stat
their home even when they never read it. The profile lives at `DATA_DIR/confinement/<id>.sb`,
outside the plugin's write set: a cage the prisoner can rewrite between restarts is not a cage.
`write_profile` compile-checks by running `sandbox-exec -f <profile> /usr/bin/true` at install,
so a malformed profile fails with the person present.

**Review screen rules.** Counts, not capabilities — `"~/Documents/Notes — 412 files, 38 MB"`,
walk-bounded at 20,000 entries or 2 s. The *sealed* side renders larger than the *seen* side, so
an over-broad request argues against itself: a plugin asking for `~` renders as *"will see your
entire home folder — more than 20,000 files. Nothing is sealed."* On update the screen is a
**diff**, and a strictly-narrowing update shows no screen at all — the screen's appearance is
itself information.

`"network": "none"` with a downloading runner (`npx`, `uvx`, `pipx`) fails install: *"This
plugin says it needs no network, but it is installed by downloading itself."*

### State

`plugin_state` in agent.db, **one row per `(plugin_id, scope, owner, key)`**, replace-not-append,
with `bytes`, `revision`, `writer` and `digest` columns. `bytes` is denormalised so the slot cap
is one `SELECT SUM(bytes)` in the write's own transaction, with no JSON parsing.

`MAX_SLOT_BYTES = 64_000`, `MAX_VALUE_BYTES = 8_000`, `MAX_KEYS_PER_SLOT = 64`. Every refusal
names the number.

Scope is `conversation | project | global`, one value for the whole plugin. **The owner is
resolved inside `write()` from `session_context`, never passed in** — every caller that could be
handed it could be lied to. An empty owner in a non-global scope is a refusal, never a fallback
to global.

**Three writers, not four.** The sealed surface (over the bridge). The plugin's MCP server —
through a reserved `_kith_state` key on its own tool result, stripped before the model sees it,
written on the line after the permission gate and beside the `touched.record` MCP has always
been missing. And the host. **The model reads and never writes**: every write it could want is a
plugin action it can already take, and deleting the write deletes an argument, a merge policy,
and the question of what happens when the frame and the model disagree about a key.

`expect` is an optional per-key compare-and-set. Optional because a surface writing `collapsed`
genuinely wants last-write-wins.

**Unmount does not clear.** A pane is unmounted by a window resize with no user action, so
clear-on-unmount would erase plugin state when someone narrowed their window.

### The digest

Host-rendered Python, from key **names** declared at install and snapshotted onto the grant.
A plugin supplies bounded values and never composes a sentence that reaches the prompt.

```
[Your plugins]
Circulars — unreviewed: 4, last_seen: 14:02
Notes — unfiled: 7
(2 more plugins are holding state that is not shown here.)
```

`DIGEST_CHARS = 740` across all plugins, `DIGEST_CHARS_PER_PLUGIN = 240`,
`DIGEST_KEYS_PER_PLUGIN = 6`, `DIGEST_VALUE_CHARS = 64`. Values must be primitives; objects and
arrays are skipped. **Newlines are stripped, not escaped** — because a value is one leaf
primitive on one line, that is a complete defence against a plugin forging `\n\n[Right now]`,
with no plugin-formatted region left to sanitise. Keys render in granted-list order, not stored
order: letting an untrusted page choose which of its keys the model sees first is letting it
choose emphasis.

**Default off, and the manifest cannot turn it on.** `digest.enabled: false` is the only legal
manifest value; switching it on is a person's act on the plugin's settings row, because it is
the only thing here that spends tokens on every turn forever. A manifest whose digest
declaration differs from the granted snapshot renders the *granted* one and shows a row saying
the plugin wants to change what it tells him.

**Placement: inside `_present_state`, beside the project region.** That is the one region
rewritten every turn, so everything ahead of it stays cached. The three wrong positions and
their price are worth stating because two look correct:

- *In `config.system` beside `skills.index()`* — `caching.stable_head` matches
  `text.startswith(persona)`, so a varying digest sits inside the stable block. One byte and
  breakpoint #1 misses, re-billing the persona **and** the tool schemas at write price. On
  Anthropic the tools block sits ahead of system in cache ordering.
- *Glued to the CHAT_DIRECTIVE* — the brief and all replayed history re-bill every turn, and
  `test_the_persona_message_is_byte_identical_across_prompts` fails.
- *Its own system message after the live block* — cache-neutral, accounting-broken:
  `ledger.take`'s bare `role == "system"` branch files it under "How you work".

**An installed but idle plugin costs zero characters.** `_present_state` with an idle plugin must
be byte-identical to `_present_state` with none installed. That is a test, not an aspiration.

### The frame bridge

**One module, one union, one `window.kith`** with `render`, `on`, `state`, `report`. Three
messages each way:

```
host  → frame   theme | render{rev, state, host} | command{call, name, args}
frame → host    ready{protocol, handles} | result{call, ok, value} | size{px}
```

The invariant, restated precisely: **the frame has no verb that reaches anything outside the
plugin's own store.** No `fetch`, no `navigate`, no `invoke`. Data arrives because the manifest
declared `wants` and core pushes it. Actions happen because the manifest declared a command and
core drew the button. That is what keeps every privileged effect behind core-drawn chrome — and
it is why `origin == "person"` may skip the permission gate.

`render` is the only inbound data path; it already carries `state`, so a separate restore
message is redundant and does not exist. `state.read` survives only as an explicit refresh after
a write.

`event.source` is checked **first**, before the type switch — `event.origin` is the string
`"null"` for every sandboxed frame, so it distinguishes nothing. Today `canvas-bridge.ts` checks
type first; harmless with one inbound type, not harmless with three.

Replies are validated against the command's declared `returns` **twice** — in the renderer and
again server-side, the deliberate duplication `prompt.py` insists on, because the first check
runs on the far side of an HTTP request anything local can make. This is what closes the
free-text hole: an RPC reply cannot become a prose channel into the prompt, because there is no
field for prose unless the manifest declared one and core capped it.

**The `ready` protocol number is load-bearing.** A mismatch mounts a placeholder — *"Circulars
was built for a different version of Kith"* — rather than being dropped silently. The host
accepts protocol N and N−1, so a plugin has one release to catch up. Both must exist before the
first third-party plugin ships, not after.

`wants` is a closed allowlist of core-owned projections: `activity`, `focus`, `chat_usage`,
`processes`, `schedules`, `working_on`, `phrases`. `chat_usage` is why the mechanism exists —
it is renderer-only zustand that no endpoint and no MCP server could ever supply.

### Serving the document — the seal does not change

Nothing fetches. `documents.sealed()` builds the document server-side and **inlines** every
relative script, stylesheet and image that resolves inside the plugin folder; images and fonts
become `data:` URIs, which the existing policy already permits. An absolute or remote href is
**refused at install with the href quoted** — the seal has no network, and the honest moment to
say so is when a person is deciding, not as a blank pane later.

So: no new sandbox token, no new CSP directive, no relaxation of `default-src 'none'`, and
`canvas.test.ts`'s describe block — the one that exists to fail when the seal is widened for
convenience — passes **unmodified**. That is checkable rather than argued, which is the whole
reason this approach wins.

`POLICY` moves to `domain/seal.py` (rank 1) with a test asserting the two copies agree. They have
already drifted: the route's carries `form-action 'none'` and `base-uri 'none'` and
`lib/canvas.ts`'s carries neither, and nothing catches it.

Mounting is a two-step ticket — an authenticated POST returning 24 random bytes, then a GET on
`/api/plugins/frame/<ticket>`. A stable plugin path must never be added to `OPEN_GET_PREFIXES`:
the unguessable id is what that exemption rests on. The ticket table **is** the liveness table,
which is why there is no heartbeat — a stale ticket means a call parks and fails at its deadline,
and a heartbeat would buy a faster wrong answer, not a different one. Tickets are keyed on
`(plugin, view, instance, client_id)` so a second window cannot silently steal the first's frame
identity.

### Three clocks, deliberately different

| clock | value | the failure it covers |
| --- | --- | --- |
| renderer deadline | `timeout_ms` ≤ 10 s, or 3 s for `host` | the frame is slow or hung |
| server deadline | 20 s | the **renderer** is gone — window closed, laptop asleep |
| unattended, checked **before** parking | — | a scheduler turn with nobody there |

The 10-second ceiling exists because the model is parked inside `run_tool`. `ask` waits fifteen
minutes because a person is answering; a frame is not a person. The frame is never killed on
timeout — that loses the person's scroll position and anything half-typed, to fix a problem
already fixed by the host refusing the late reply.

`release(conversation_id)` goes beside `permissions.release_waiting()`: a turn parked on a frame
is not reading the stop switch.

### Accounting — same commit, or the meter silently lies

Two ledger lines, `plugin_tools` → group `tools` and `plugins` → group `place`, **both**
`GROUP_OF` entries, and both `report.category_text` branches, in one commit. A missing
`GROUP_OF` key is dropped silently by `byGroup` and the meter bar stops summing to its own
total — that is the `directives` bug, already recorded in that file's own comment. The
server-side coverage test catches a missing publisher; nothing catches a missing `GROUP_OF` key,
so it needs its own assertion.

`changes.KINDS` gains three: `plugin` (install/enable/upgrade/uninstall, invalidating
plugins+mcp+skills), `plugin_state`, `plugin_call`. Narrow targets, because publishing `plugin`
on every state write would refetch `/api/skills` — which walks the folder tree three times — at
the surface's write cadence.

**Prompt cost is a first-class UI element.** `estimated_prompt_chars()` per plugin, refused above
`MAX_PROMPT_CHARS`, shown in tokens on the install screen — *and* an installed-total ceiling,
because ten plugins at 3,900 characters each is ~10,500 tokens added to the cached prefix
forever, against a prefix measured at ~8,700. The refusal names which installed plugins are the
expensive ones, so the answer is "remove one" rather than "give up".

### Uninstall does not delete state

`retired_at` and a 30-day sweep, in the same `sweep()` at app start that already handles staging
folders. The dialog offers *remove* (default; a reinstall inside 30 days restores it) and
*remove and delete its data*, with the key count and byte size shown. Silently destroying on
uninstall what is "the person's data" on disable contradicts `skills.remove()`'s explicit
trash-never-destroy rule.

## The dogfood, and its honest verdict

The Work panel is rebuilt as a plugin, and the built-in is **deleted**. While both exist, every
gap has a first-party escape hatch and nobody has to notice it. Deleting the `SurfaceId` member
is the specific act proving a plugin tab is not second-class.

Under this contract Work is rebuildable with **full function and, without two additions,
degraded appearance**. Both additions are in scope:

1. **`wants: ["phrases"]`.** Errand steps render through `describeCall` in
   `lib/tool-language.ts`, which is host-owned and asserted by a server test that reads the
   TypeScript file. A sealed frame cannot import it, so the port would render raw tool names —
   the "mcp probe add" failure this codebase already fixed once, reintroduced.
2. **A core-authored stylesheet and icon sprite, inlined into the sealed document.** The panel
   uses eleven lucide icons and a dozen tokens; `canvasTokens()` exposes nine tokens and no
   icons. **This needs no seal change** — `style-src 'unsafe-inline'` and `img-src data:` are
   already permitted, and both are Kith's own bytes injected at build time exactly as the CSP
   `<meta>` already is. Without it every plugin tab reads as foreign at a glance.

Plus a tab `subtitle` slot (~24 chars, set by a `state` write) for the live round count, whose
chrome core draws.

**The caveat no channel work fixes:** much of what Work shows is host state, so the rebuild
leans on `wants` and `writer: "host"`. That proves the surface path and the store path. It does
not prove a third party could have built Work.

## What v1 deliberately does not do

**No push. A plugin cannot act on its own.** Its subprocess writes state only while servicing a
model-initiated call; its surface only while mounted; its skills only when read. So "CI
finished", "the long job succeeded", "the regulator published a circular" cannot reach the
digest until the model happens to call that server again. Every notifier and watcher plugin is
impossible, and those are much of what people build first. **Reserve the manifest key now** and
write down which of two shapes it will be — honouring MCP server→client notifications (needs a
single-reader rewrite of `client.py`, and must set a `stale` flag rather than mutating
`_Live.tools`, or it discards the prompt cache mid-turn), or a declared `watch` that Kith polls
on a cadence it owns. **Say it on the review screen**, because someone installing "CI Watch"
will reasonably assume it watches.

**No chat contributions.** No message renderer, no slash command, no composer affordance, no
attachment handler. In a chat application that is the extension point people ask for first, and
its absence pushes everything unanticipated into the MCP server — the contribution with the
least schema and the most privilege. Named here as a deliberate v1 scope with a review cadence,
not an oversight. Measure it: if the first ten plugins all ship a server to do something a chat
hook would have done cleanly, add the hook rather than widen `server`.

**No integrity or provenance.** Install is a `copytree` from a local folder. Two cheap things
belong in v1 anyway: record a manifest hash and show *"changed on disk since you approved it"*,
and refuse unpinned runner arguments so the subprocess is at least the code that was reviewed.

**Provisional constants,** marked as such, replaced by T9's measurements: `MAX_DOC_BYTES`,
`RENDER_COALESCE_MS`, and the state write-rate ceiling. House style forbids invented thresholds;
these are the only three in the design.

## Tranches

**T1 — the prerequisite fix and MCP hygiene.** No plugin concept anywhere. MCP dispatch moves
inside the existing `try`, gains a permission check and the `touched.record` it has never had.
Plus the confirmed bugs: the **env-wipe**, `manager.forget()` getting its first caller and a
`DELETE /api/mcp/<label>`, `manager.running()`'s missing `process.alive` check (a crashed server
reads "Connected — 5 tools" forever), and `POLICY` moving to `domain/seal.py` with its drift
test. *Proves:* an MCP call passes through `permissions.py` and a refusal draws the Allow
button. Worth shipping alone.

**T2 — confinement, applied to servers that already exist.** `infra/confinement.py`: constructed
environment, explicit `cwd`, private package cache, a `sandbox-exec` profile from a `reach`
declared in the existing MCP settings form. Behind a per-server toggle. *Proves:* that the only
enforcement in the design holds, measured against the `npx`- and `uvx`-shipped servers people
already run — **before any manifest freezes a `reach` vocabulary**. If confinement proves too
hostile to real servers, the trust model changes shape and this is where you find out. This is
the largest de-risking move available, which is why **T2 comes before T3**.

**T3 — the manifest and the registry, skills only.** `kith.plugin.json`, `domain/plugins.py`,
the registry, install/upgrade/uninstall with staging-then-rename and `sweep()`, the review screen
with its recurring-token figure and the installed-total ceiling, `skills.roots()` with the
person's folder first, and the `skills.remove()` fix. Plus the dev-install symlink and
`POST /api/plugins/<id>/reload` — never a filesystem watcher, since save-on-keystroke would
discard the prompt cache dozens of times a minute. *Proves:* one folder installs, contributes,
upgrades and uninstalls cleanly; `skills.index()` is byte-stable with a plugin installed; the
person's skill wins a name collision.

**T4 — plugin-contributed MCP servers.** `configured()` merges plugin declarations and filters
ungranted rows; `MCPServer` gains `owner` and `reach`, absent from `stored()`/`from_stored()` so
provenance cannot be forged; `save()` writes back only user rows. *Proves:* an untrusted
plugin's subprocess runs inside a boundary it declared and cannot enforce, granted once,
revocable from a screen.

**T5 — state and the digest.** The keyed table, `_kith_state` on the MCP envelope, the read tool,
the host-rendered digest, the ledger line. *Proves:* the zero-cost claim as a byte comparison,
and that an untrusted plugin cannot compose text reaching the prompt — the test is a plugin
writing a value containing `\n\n[Right now]` and asserting it renders on one line.

**T6 — surfaces, read-only.** The `SurfaceId` member, the tab-key grammar, structural
`looksLikeLayout`, the mount ticket, `documents.sealed()` with asset inlining **plus the
stylesheet and icon sprite**, the `render` push, one `wants` projection. Attempt a partial Work
rebuild here. *Proves:* a tab mounts, rehydrates with its real title and width, survives its
plugin being uninstalled by costing one tab — and looks like Kith. Do not defer the sprite: the
token and icon gaps surface here, and finding them after the protocol is frozen is expensive.

**T7 — commands, without the frame.** `host` and `state` delivery, the five-entry allowlist,
toolbar buttons in host chrome, the `plugin__` namespace, the per-call gate, `plugin=[]` for
sub-agents, the `tool-language.ts` phrase rules. *Proves:* the model can act on a plugin, and a
plugin's own button can fire a privileged action, **with the tab closed**. This is what validates
the `delivery` split itself — if most useful actions turn out to need `surface`, the split was
wrong and T8 grows.

**T8 — `delivery: "surface"`, the frame RPC.** The `command`/`result` pair, two-id correlation,
the three clocks, `repeatable`, per-window pending identity, the misbehaving-frame budget, the
closed error vocabulary, the test harness and the protocol-mismatch placeholder. *Proves:*
the one genuinely new runtime primitive — a model tool call routed into a live sealed
frame and awaited — built **last**, on top of everything that did not need it, with its
honest failures (`surface_not_open`, `slow`, `surface_interrupted`) exercised rather than
asserted.

**T9 — Work finished, and the built-in deleted.** `wants: ["phrases"]`, the tab subtitle,
errands rendered in-frame with correct phrasing, and `work-panel.tsx` deleted along with its
`SurfaceId` member and its `workspace.tsx` arm. *Proves:* the honesty test. Measure the activity
push cost here and replace the three provisional constants with real figures.

## Testing and observability, which the design would otherwise ship without

- **A plugin test harness.** A headless page loading a plugin's entry with a mock `window.kith`
  — scriptable `render` pushes, recorded state writes, injectable `command` messages — plus a
  dev-only route that dispatches a declared command with no model. A `plugin-bridge` test file
  mirroring `canvas-bridge.test.ts`, including hostile input: a reply naming an unknown call id,
  a reply from the wrong window, a `returns` field over its declared `maxLength`.
- **A per-plugin ring buffer of the last 32 events** — manifest faults, dropped bridge messages
  (kind and count only, never content), refused state writes with the cap that refused them,
  command timeouts, subprocess exits with code — on the plugin's settings row. Today four
  failure paths are silent by design or by accident, including MCP `stderr` going to `DEVNULL`.
  A plugin that mounts, renders nothing and answers nothing produces **zero** diagnostics, and
  in a packaged Electron build there is no terminal to read.
- **One diagnostics block per plugin**, rendering the digest line **verbatim** — that single item
  answers "why does he not know about my state" in one glance.
- **A constants-parity test** asserting the Python and TypeScript caps agree. At least eight
  paired constants cross that boundary, and the precedent is already broken: the two CSP copies
  have drifted with nothing catching it.
