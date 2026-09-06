# Example plugins

A plugin is one folder with a `kith.plugin.json` in it. Install one from **Settings → Plugins**,
or point the API at it directly:

```sh
curl -s -X POST localhost:8611/api/plugins \
  -H "X-Kith-Token: $(cat ~/.kith/api-token)" \
  -H 'Content-Type: application/json' \
  -d "{\"path\": \"$PWD/sketchpad\"}"
```

Nothing is installed by default. A folder sitting here contributes nothing until somebody
installs it — the row in the database is what makes a plugin exist, not the folder.

## sketchpad

A tab he draws on. The shortest honest demonstration of the whole loop:

1. He calls `plugin__sketchpad__draw`, and one record lands in the plugin's store.
2. Core pushes the store into the sealed frame.
3. The page folds the record into its own list of shapes and writes the list back.
4. You drag a shape; that writes back too.
5. The next thing you say to him carries a digest line — `Sketchpad — title: Intake flow,
   shapes: 11, note: moved Intake` — so he knows what happened while he was not looking.

It contributes all four kinds of thing a plugin can: a **surface** (the tab), three
**commands** (two he can call, one drawn as a button), a **skill** telling him how to lay a
diagram out, and **state** with a digest. It bundles no MCP server, so it runs no program and
needs no boundary approved.

Read `ui/board.html` for what a surface actually looks like. The two things worth noticing:

- **`window.kith` is the only channel out**, and it has no verb that reaches past this plugin's
  own store. No fetch, no navigate, no open. Data arrives because core pushes it; actions
  happen because the manifest declared a command and core drew the button.
- **Nothing is fetched.** The frame has no network at all. Every relative script, stylesheet
  and image is inlined by the server before the document is served, and a reference to
  something remote is refused at install with the URL quoted — so a plugin whose stylesheet
  lives on a CDN finds out while somebody is deciding, rather than rendering unstyled later.

## Writing one

`docs/superpowers/specs/2026-09-06-plugin-host-design.md` is the contract. The short version:

| you want to | declare |
| --- | --- |
| a tab | `surfaces[]` with an `entry` html file |
| something he can call | `commands[]` with `model: true` |
| a button in the tab's chrome | `commands[]` with `present: {in: "toolbar"}` |
| something that survives the turn | `state`, and a `digest` if he should know it exists |
| to teach him how to use it | a folder under `skills/`, in the ordinary `SKILL.md` format |
| to run real code | one `server` — an MCP server, with the files it may see declared |

Two limits worth knowing before you start. A command with `model: true` costs prompt tokens on
**every round of every turn**, which is why it defaults to false and why the install screen
prices your plugin before anyone agrees to it. And a `params` shape is primitives only —
string, number, integer, boolean — in both directions; objects and arrays are how structure
gets smuggled into a turn, so a command that needs a record uses `does.collect` and lets the
surface do the folding.
