---
name: designing-a-screen
description: "Use when mocking up a screen someone will look at and then want built."
---

# Designing a screen

An HTML file you attach to a task is not just a file. If it is a screen, they can open it in a
design editor: click any part of it, change the spacing, the type, the colour, and save. What
comes back to you is a diff in the file you wrote — `padding: 16px → 24px` — so a correction you
would otherwise have received as "make it a bit tighter" arrives as the number.

That is what this is for: the loop where you draft a screen, they fix what is wrong with it by
hand, and you build the thing they fixed.

## Decide whether it is a mockup at all

In order of preference — the first that fits wins:

1. **Prose.** Most answers. If you can say it in three sentences, say it in three sentences.
2. **A ```mermaid diagram.** Structure, flow, how the pieces relate. Cheaper for you to write and
   faster for them to read than any picture of a screen.
3. **A ```html canvas in the reply.** Something that *moves* or has a knob on it. See
   `drawing-a-canvas` — that is a different tool for a different job, and it is not editable.
4. **A design file.** Only when the thing being decided is what a screen should *look like*, and
   only when somebody is going to build it afterwards.

If nobody is going to build it, you are drawing a picture of an app rather than designing one.

## How a design gets made

Write the file and attach it. There is no design tool and no special deliverable kind:

```
write_file("screens/settings.html", ...)
add_deliverable(id=41, kind="file", title="Settings screen", content="screens/settings.html")
```

The editor appears on the deliverable by itself. Put designs somewhere sensible — `screens/`,
or beside the code they are for if the task has a project.

## The dialect

A design is ordinary HTML, but two constraints are what make it *editable* rather than merely
viewable. Break them and it still renders; it just stops having handles, and the person is back
to describing what they wanted in words.

**Layout is flex. Never coordinates.**

```html
<div data-k="row" style="display:flex; gap:12px; align-items:center; padding:16px">
```

Not `position:absolute; top:340px; left:24px`. The editor's handles adjust padding and gap and
its drag reorders — because that is what a flex layout *has*. A design pinned to coordinates
cannot be adjusted by anyone, including you, and it is also the part you are worst at: you cannot
see the page, so every number you invent is a guess you have no way to check. Let the browser do
the arithmetic. If you genuinely need an overlay — a badge on an avatar, a dropdown over a field
— mark the containing frame `data-k-absolute` and position inside it.

**Colour is a token. Never a literal.**

```html
style="color: var(--ds-ink); background: var(--ds-panel)"
```

The tokens come from **the design system this workspace is in**, not from Kith. The checker
prints which one it found on its first line, and the editor's colour swatches are that system's
palette. If there is a `design-system.json` (or a `brand/profile.json`) anywhere above the screen
you are writing, those are the names you have:

* `--ds-<colour>` — every colour in the system, by its own name.
* `--ds-text-<role>-size`, `-weight`, `-tracking` — the type scale, by role.
* `--ds-radius-<name>`, `--ds-font-<role>`.

Run the checker once before you start: its `system` line tells you whose palette you are in, and
names the file. With no system anywhere you get Kith's own nine under the same `--ds-` names,
plus the older `--kith-*` spellings, which still work everywhere.

**The system is a file, and you may edit it.** It is found nearest-first — the project's folder,
then his workspace, then any installed skill that carries a brand — so a product with its own
brand gets its own, and one without inherits. If a screen needs a colour the system does not have,
that is worth saying out loud rather than working around: a colour you reach for three times in
one screen belongs in the system, and adding it there means every screen after this one has it
too. Propose it; do not quietly add a local token and move on.

**You may define your own tokens.** A design can declare what it needs:

```html
<div data-k="page" style="--brand-shadow: 0 2px 8px rgba(0,0,0,.08)">
```

That is the right way to carry a value the system has not named — and the checker does not count
a declaration as a literal colour. The two namespaces you cannot take are `--ds-*` (the system)
and `--kith-*` (the app, and what the editor draws its own selection outline with).

**Spacing follows the system's scale**, which the checker knows and measures against. With no
system that is 4, 8, 12, 16, 24, 32, 48, 64.

**Pictures work.** `<img src="assets/logo.svg">` — a workspace-relative path, resolved for you.
Not an http URL: a design cannot reach the network, by construction. A `data:` URI is fine for
something small.

**Name the parts.** `data-k="hero.title"`, `data-k="settings.save"`. Any node you do not name is
given a number, which works but tells nobody anything — a layers panel full of `k14` is a layers
panel nobody uses. Name the structural pieces and the things you would refer to in a sentence.

## What renders but cannot be dragged

`display:grid`, media queries, `::before`/`::after`, `@keyframes` and animation. All of it
renders and all of it is allowed — what none of it has is *handles*, and the editor says so in
the corner. Use them when a screen genuinely needs them; just know you have made that part of it
read-only, and do not reach for grid to lay out a row of three things a flex line would do.

A stylesheet in a `<style>` block is kept and applied. Their edits are written inline, which wins
over it, so a class you defined stays the default and their change is the exception. That is the
right arrangement: put the repeated parts in classes.

## Check it, and look at it

One command does both:

```
python3 <skill>/scripts/check_design.py screens/settings.html
```

It reports what you cannot see — nodes outside the editable dialect, colours written as literals,
spacing off the scale, text failing contrast, anything wider than what contains it, the height at
a narrow and a wide column — and it exits non-zero while any of that is outstanding, so you can
chain it while you iterate. Fix what it names before you attach the file; every one of those is
something they would otherwise have to tell you.

**And it writes two PNGs**, wide and narrow, printed on the `shot` line. Open them with
`read_file` — an image comes back to you as a picture. Do that. "It renders" is not the same as
"it is right", and the checker is deliberately blind to whether a thing looks good.

**Do not go looking for another way to see it.** There is no screenshot tool on this machine: the
window's own capture needs a display surface and a headless turn has none, so `read_file` can
*show* you a picture but nothing else will *make* one. The way to see a design is this script.
Standing up a web server and opening a browser panel works and is three moving parts where one
will do.

## After they have edited it

**Re-read the file before you touch it again.** This is the one that will catch you out. Your
default assumption is that a file says what you last wrote in it, and for a design that is wrong
by design — the whole point is that they change it. Their changes are the answer to the question
you were about to ask them.

So when you come back to a design: read it, look at what moved, and take that as the brief. If
they tightened every gap from 16 to 12, that is a preference, and it applies to the next screen
too. If they moved the primary action to the right, do not move it back.

Then build the thing they left you, not the thing you drew.
