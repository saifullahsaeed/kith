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
style="color: var(--kith-text); background: var(--kith-muted)"
```

The nine tokens are `--kith-bg`, `--kith-line`, `--kith-text`, `--kith-dim`, `--kith-accent`,
`--kith-accent-soft`, `--kith-second`, `--kith-second-soft`, `--kith-muted`. They are defined for
you in the editor and in the app. A design written in them matches everything else you have made
and follows a theme switch for free; a design full of `#3a3a3a` is a design that looks slightly
wrong next to the others and goes unreadable when the theme changes. The editor offers tokens as
swatches, so this is also the version they can keep editing.

**Spacing goes up in fours.** 4, 8, 12, 16, 24, 32. A 7px gap is the kind of thing nobody notices
and everybody feels.

**Name the parts.** `data-k="hero.title"`, `data-k="settings.save"`. Any node you do not name is
given a number, which works but tells nobody anything — a layers panel full of `k14` is a layers
panel nobody uses. Name the structural pieces and the things you would refer to in a sentence.

## What renders but cannot be dragged

`display:grid`, media queries, pseudo-elements, animation. All of it renders. None of it has
handles, and the editor says so in the corner. Use them when a screen genuinely needs them — just
know you have made that part of it read-only, and do not use grid for a row of three things a
flex line would do.

A stylesheet in a `<style>` block is kept and applied. Their edits are written inline, which wins
over it, so a class you defined stays the default and their change is the exception. That is the
right arrangement: put the repeated parts in classes.

## Check it, because looking is only half

Take a screenshot — `read_file` on an image shows it to you, and "it renders" is not the same as
"it is right". But the defects that survive looking are the measurable ones, so run the checker
too:

```
python3 <skill>/scripts/check_design.py screens/settings.html
```

It reports what you cannot see: nodes that ended up outside the editable dialect, colours written
as literals, spacing off the scale, text that fails contrast, anything wider than what contains
it, and the height at a narrow and a wide column. It exits non-zero while any of that is
outstanding, so you can chain it while you iterate. Fix what it names before you attach the file
— every one of those is something they would otherwise have to tell you.

## After they have edited it

**Re-read the file before you touch it again.** This is the one that will catch you out. Your
default assumption is that a file says what you last wrote in it, and for a design that is wrong
by design — the whole point is that they change it. Their changes are the answer to the question
you were about to ask them.

So when you come back to a design: read it, look at what moved, and take that as the brief. If
they tightened every gap from 16 to 12, that is a preference, and it applies to the next screen
too. If they moved the primary action to the right, do not move it back.

Then build the thing they left you, not the thing you drew.
