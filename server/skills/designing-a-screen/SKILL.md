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

**The rest of this skill is in two halves, and the second one is the one that gets skipped.**
The dialect below is what makes a screen *editable*. It says nothing at all about whether the
screen is any good, and a screen can satisfy every rule in it — every colour a token, every gap
on the scale, every contrast ratio passing — and still look like nothing anybody chose. Judgment
is the other half, and it comes first, because it is a decision about what to draw rather than a
rule about how to write it down.

## Decide whether it is a mockup at all

In order of preference — the first that fits wins:

1. **Prose.** Most answers. If you can say it in three sentences, say it in three sentences.
2. **A ```mermaid diagram.** Structure, flow, how the pieces relate. Cheaper for you to write and
   faster for them to read than any picture of a screen.
3. **An animated diagram.** When what matters is the *order* things happen in. See
   `animating-a-diagram`.
4. **A design file, on the canvas.** Only when the thing being decided is what a screen should *look like*, and
   only when somebody is going to build it afterwards.

If nobody is going to build it, you are drawing a picture of an app rather than designing one.

## Read the system before you decide anything

```
read_design_system()
```

One call, and it answers with four things: the palette and type scale this product uses, **the
inventory of components already built for it**, **the conventions somebody wrote down about how
its screens go together**, and where all of that lives.

Do this first, every time, before the plan and long before the markup. Two of those four are the
difference between a set of screens and a product:

**The inventory is what you build out of.** A component is a part of this product that has
already been decided — a button, a card, a field, a table row — written once and named. Compose
the screen from the parts that exist and only invent the ones that do not. This is the entire
reason a codebase with a component library produces consistent interfaces and one without it
does not, and it is the same for you: screen four looks like screen one when it is made of screen
one's parts, and does not when you draw a fresh button from raw divs each time.

**Use a part by reference, never by copy.** Each part is a file beside the system, and a screen
points at it:

```html
<k-use name="button.primary" label="Save changes"></k-use>
<k-use name="row.detail" label="Checking" value="$3,240"></k-use>
<k-use name="panel" title="Coming up">
  <k-use name="row.detail" label="Rent" value="$1,500"></k-use>
</k-use>
```

Change the part's file and every screen that uses it changes. A pasted copy does not, which is
why copies drift: the same `button.primary` has been found in four versions across seven screens
of one product. `read_design_system` gives each part's exact tag under `write`, with its props.

* Attributes on `<k-use>` fill the part's `{{holes}}` — `label="Checking"` fills `{{label}}`.
  Values are text. `name`, `style` and `class` are the tag's own.
* What you write *inside* `<k-use>` goes where the part has `<k-slot></k-slot>`.
* `style` on the `<k-use>` changes this one instance — `style="margin-top: 24px"` — and leaves
  the part alone. To change the part, edit its file (`path` in `read_design_system`).

**The conventions are the house style.** Density, what the brand does and does not do, where the
primary action goes, decisions already argued out. They are prose because that is the shape
judgment comes in. Follow them; they outrank your own taste on this product, and where they
contradict the section below on what makes a screen look designed, they win — they are specific
and it is general.

**When a part or a rule turns out to belong to the product, put it back.**

```
update_design_system(
  components={"card.metric": {
    "html": "<div data-k=\"metric\" style=\"display:flex; flex-direction:column; gap:4px\"><span>{{label}}</span><strong>{{value}}</strong></div>",
    "use": "One number with its label."
  }},
  conventions="...the whole file, with the new line in it..."
)
```

A part's markup is one root element in the dialect, with `{{holes}}` where screens differ and
`<k-slot></k-slot>` if screens put content inside it. It may carry its own `<style>` block, which
travels with it — so a part does not depend on whichever screen's stylesheet it was first drawn
in. It is saved as `.kith/design/components/card.metric.html`.

The trigger for a component is the second screen: the first time you need a card you draw one,
the second time you need the same card it is a part — add it, then replace the first screen's
copy with `<k-use>` too, so there is one card and not two. Adding it there means every screen
after this one has it. The trigger for a convention is the second correction: when he tightens
the same gap or moves the same button twice, that is not a note about this screen, it is a rule
about this product, and writing it down is the only thing that stops you re-learning it next
week. Merging is the default, so adding one part does not disturb the rest.

**There is no system yet?** Then if this product is getting more than one screen, write one
before you draw: the palette, the type roles, the spacing scale. Four to six colours and three
or four type roles is a whole system on day one. Starting without it means every screen gets
its own values and there is nothing to make them agree later.

## The plan comes before the file

Before the first line of HTML, write the plan in your reply. Four parts, in this order, about a
dozen lines all told. It is short on purpose: it exists so that the decisions get made once,
deliberately, instead of being made one at a time by whatever came to hand while you were typing
markup.

**Subject.** What the product is, who is looking at this screen, and the one job the screen does
for them. If the brief did not say, decide, and say what you decided so they can correct it. A
scheduling tool for clinic receptionists and a trading terminal are not the same screen with
different words in it, and almost everything else in the plan follows from this line.

**Palette.** Four to six of the system's colours, each with what it is for — surface, panel, body
text, quiet text, the one accent, a state colour if the screen has states. A system with
twenty-one colours is not an instruction to use twenty-one. Decide here which one colour carries
emphasis on this screen, because everything else then has to stay out of its way.

**Type.** Which roles you are using and the hierarchy they make. Name the largest thing on the
screen and the smallest, and check there is a real gap between the levels — two steps of the
scale, not one. Three sizes used decisively beat six used tentatively.

**Structure.** The frame size this screen is for — `desktop`, `mobile`, or the pixels — then
six lines of ASCII, plus one sentence naming what the eye should hit first and why that is the
right thing for it to hit. Name which inventory components each region is made
of, and say plainly which parts you are having to invent — that list is the proposal for what
should go into the system afterwards.

```
┌──────────────────────────────────────┐
│ ▛ brand        nav nav nav    [btn]  │
├──────────────────────────────────────┤
│                                      │
│   HEADLINE ───────────                │
│   sub · sub                          │
```

**Then read the plan back and ask two questions.** First: would I have written this same plan
for any other product in this industry? Every part where the answer is yes is a default rather
than a decision. Second: does anything here contradict the conventions? Change those parts, say
in one line what you changed and why, and only then write the HTML.

## What makes a screen look designed

These are the things that separate a screen someone chose from a screen that assembled itself.
None of them are enforced by anything; all of them are visible at a glance.

**Space does the grouping, and it has to be unequal.** The single most common reason a screen
reads as flat is that every gap in it is the same number. Things that belong together sit
closer than things that do not — if the gap inside a card is 12, the gap between cards is 24 and
the gap between sections is 48. Two adjacent values do not group anything; you need a clear
step. When a screen feels undesigned and you cannot say why, this is usually it.

**Spend boldness in one place.** One element is the memorable thing — a headline, a number, an
image, one block of colour. Everything around it stays quiet and disciplined. A screen with
three bold moves has none, because they cancel. Before you attach it, find the one accessory to
remove.

**Hierarchy is several signals at once, on one thing.** The important element gets size *and*
weight *and* colour *and* the space around it. The unimportant ones get none of those — not a
smaller version of each. Most weak screens apply a little emphasis to everything.

**Structure carries information.** A border, a divider, a rule, an eyebrow, a number — each
should tell the reader something true about the content. A divider means these are separate
things. Numbered markers mean this is a sequence; if the content is not a sequence, they are
decoration wearing a uniform. Before adding a structural device, say what it tells someone.

**Write the real words.** Placeholder copy makes a design look generic faster than any colour
choice. Name things the way the user would say them, not the way the system stores them. A
button says what happens — "Save changes", not "Submit" — and the same action keeps that name
through the whole flow, so a "Publish" button produces a "Published" toast. Empty states are an
invitation to act, and errors say what went wrong and what to do, without apologising.

**Draw the states the screen actually has.** A list screen with no empty state, a form with no
error, a table with three tidy rows of perfect-length text — those are the parts a mockup
usually skips and the parts the build always needs. One realistic long name, one wrapped line,
one error, one empty state. It will look worse and be far more useful.

## Check the plan against the defaults

These are the moves that show up regardless of subject. Each is legitimate for *some* brief — if
the brief asks for one, give them it. What you should not do is spend a free choice on one.
Where the brief leaves an axis open, the tells to watch for on this surface are:

1. **One gap value everywhere.** 16 between everything, so nothing groups. The flex tell.
2. **The card kit.** Content chopped into identical rounded panels, one radius on everything
   regardless of hierarchy, the same soft shadow under each.
3. **Three in a row** for content that is not three of a kind.
4. **Template chrome.** A tracked-out ALL-CAPS eyebrow above every heading; meta joined with
   middle dots (`A · B · C`); `WORD — fragment` with a spaced em dash; `→` appended to button and
   link text; a monospace face for small labels.
5. **One accented word** in a headline, in italic or bold or the accent colour.
6. **The accent everywhere,** so nothing is accented.
7. **Numbered markers** (01 / 02 / 03) on content that is not a sequence.
8. **The system applied flat** — every colour at its named default, no tint, no depth, nothing
   that suggests the palette was looked at rather than enumerated.

Where the brief pins a direction, follow it exactly; the brief's own words always win.

## How a design gets made

Get the icons, write the file, attach it. There is no design tool and no special deliverable
kind:

```
find_icons(names=["back", "search", "bell", "user", "settings"])
write_file(".kith/screens/settings.html", ...)
add_deliverable(id=41, kind="file", title="Settings screen", content=".kith/screens/settings.html")
```

The editor appears on the deliverable by itself.

**Designs go in `.kith/`, never in the source tree.** `.kith/screens/` for the screens,
`.kith/design/` for the system and its conventions. That folder is already where a project
keeps its Kith state — its memory, its references — and the reason is simple: a mockup is your
output *about* the product, not part of it. A `screens/` folder in somebody's repository root
is a folder they did not create, cannot explain in review, and have to decide whether to
commit. Put it where it belongs and their source tree stays theirs.

**Then show them.**

```
show_canvas(id=41, note="the row height is the thing I want your eye on")
```

That opens the task's canvas on their screen — every design side by side, at a zoom where they
can be compared — and puts the canvas in your reply as well. Do it the moment you have finished a
design, and any time they ask to see something you made. A paragraph
describing four screens is not showing them four screens — and there is nothing else on this
machine that can open one: no screenshot tool, no file that opens itself, and searching for the
word "canvas" finds nothing, because the canvas is a surface in the app rather than a file on disk.

**Never paste a screen into your reply as an ```html fence.** It is shown as source there, not
drawn — the canvas is the only place a screen is drawn, and the only place they can edit it.

It answers with what is actually on it, so name those titles in what you say next.

**They can point back.** Every design has an *Ask about this* button that drops its path into
whatever they were typing, so a message about a screen usually arrives as a path in backticks.
When one does, read the file before answering — they are pointing at it because they changed
it, and the version in your head is the version before they did.

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

The tokens come from **the design system this product is in**, not from Kith. `read_design_system`
is how you find out which; the checker prints the same answer on its first line, and the editor's
colour swatches are that system's palette. The names you have are:

* `--ds-<colour>` — every colour in the system, by its own name.
* `--ds-text-<role>-size`, `-weight`, `-tracking` — the type scale, by role.
* `--ds-radius-<name>`, `--ds-font-<role>`.

With no system anywhere you get Kith's own nine under the same `--ds-` names, plus the older
`--kith-*` spellings, which still work everywhere.

**The system is a file, and you may edit it** with `update_design_system`. It is found
nearest-first — the project's folder, then his workspace, then any installed skill that carries a
brand — so a product with its own brand gets its own, and one without inherits. If a screen needs
a colour the system does not have, that is worth saying out loud rather than working around: a
colour you reach for three times in one screen belongs in the system. Propose it; do not quietly
add a local token and move on.

One case to know about: if the system you resolved lives **inside an installed skill**, it is a
brand shared with whatever it was authored for — a deck, a report. Editing it there would change
that too, so an update gives this project its own copy seeded from it. The read tells you when
you are in that position.

**You may define your own tokens.** A design can declare what it needs:

```html
<div data-k="page" style="--brand-shadow: 0 2px 8px rgba(0,0,0,.08)">
```

That is the right way to carry a value the system has not named — and the checker does not count
a declaration as a literal colour. The two namespaces you cannot take are `--ds-*` (the system)
and `--kith-*` (the app, and what the editor draws its own selection outline with).

**Spacing follows the system's scale**, which the checker knows and measures against. With no
system that is 4, 8, 12, 16, 24, 32, 48, 64. Being *on* the scale is the rule; which step you
pick is the design, and picking one step for everything is how a screen goes flat.

**Say how big the screen is.** One attribute on `<body>`, and it is the first thing to decide
because everything else follows from it:

```html
<body data-k-frame="desktop">        <!-- 1440x900 -->
<body data-k-frame="laptop">         <!-- 1280x800 -->
<body data-k-frame="tablet">         <!--  834x1112 -->
<body data-k-frame="mobile">         <!--  390x844 -->
<body data-k-frame="1600x1000">      <!-- or exact pixels -->
```

The editor lays the design out at that width, the canvas gives the frame those proportions, and
the checker renders and screenshots at that size and skips its second narrow pass when the
design is already a phone. Leave it out and you get 1200×800 and a line in the checker telling
you that you did not say.

This is not bookkeeping. Without it you are writing a screen against a width you invented and
never stated, and everything downstream picks a different one — which is how a phone screen
comes back rendered as a squeezed desktop, and how a short screen arrives with a third of the
frame empty beneath it. Decide the size, write it down, and the picture you are judged on is
the picture you meant.

**A design is measured in its own frame, never in a window.** `100vh`, `100vw` and their
relatives are lengths against whatever window the thing is being shown in, and a design is not
shown in a window — it is shown in a frame on the canvas, at a zoom, beside other designs. A screen
built on `min-height: 100vh` comes out correct at the top and then a thousand pixels of nothing
below it, which is the most common way a good design arrives looking unfinished. Use `%`, `fr`,
`flex`, or a plain pixel height. The checker fails on any viewport unit.

**A whole document is fine, and its `<body>` is the design root.** You may write
`<!doctype html>` with a `<head>` and a `<style>` block; the editor reads the body and keeps the
stylesheet. Two things are worth knowing because they are where a whole-document design loses
its looks:

* `html`, `body` and `:root` rules apply to the design root, so `body { --ink: #18322E;
  background: var(--paper) }` works the way it does in a browser, media queries included. The
  `style` attribute on `<body>` works too and wins over the rule.
* An `id` is kept and prefixed, and the stylesheet's `#selectors` are rewritten to match, so
  `#auth { display: flex }` works. Classes are simpler and are what to reach for.

**Pictures work.** `<img src="assets/logo.svg">` — a workspace-relative path, resolved for you.
Not an http URL: a design cannot reach the network, by construction. A `data:` URI is fine for
something small.

**Icons come from the set.** Call `find_icons` with what each one is for — `["home",
"calendar", "patients", "inbox", "more"]` in one call — and paste the `<svg>` each answer gives
you where the icon goes. They are one family (24px grid, one stroke weight) and take `color`
from their parent, so an icon inside a pine button is pine-on-white or white-on-pine without
you touching it. Then:

* **One size per context.** 16 inline with small text and in dense rows, 20 in buttons and
  toolbars, 24 in a tab bar. Pass `size`; do not scale them with CSS.
* **An icon sits in a flex row with its label** — `display:flex; align-items:center; gap:8px`
  — which is what puts it on the text's centre line instead of its baseline.
* **Navigation gets a label under or beside every icon.** An icon alone is for actions whose
  meaning is universal: close, search, more, a chevron.
* **The active item changes colour, not icon.** Same glyph, the accent colour, the label
  weighted up.

A Unicode symbol is not an icon — `⌂ ▦ ☰ ✚ ▾` render in whatever font the machine has, and no two
match. The checker's `icons` row names every one it finds. Neither is path data you wrote
yourself; if the set has nothing close, use a plainer word, or a text label.

**Name the parts.** `data-k="hero.title"`, `data-k="settings.save"`. Any node you do not name is
given a number, which works but tells nobody anything — a layers panel full of `k14` is a layers
panel nobody uses. Name the structural pieces and the things you would refer to in a sentence.

## Where handles matter, and where they do not

`display:grid`, media queries, `::before`/`::after`, `@keyframes` and animation all render and
all are allowed. What none of them has is *handles*, and the editor says so in the corner.

That is a cost worth paying in some places and not others, and the question is always the same:
**is this a part they will want to adjust by hand?** The rhythm of a page — the stack of
sections, the rows inside a card, the gaps between them — is exactly what they will sit and
nudge, so keep it flex and keep it editable. Decoration, a gradient, a rule drawn with
`::after`, an entrance that plays once, a genuine two-dimensional grid where rows and columns
must actually align: those they will accept or reject rather than tune, and making them
read-only costs nothing.

So do not avoid these. Avoid them *in the load-bearing layout* — and do not reach for grid to
lay out a row of three things a flex line would do.

A stylesheet in a `<style>` block is kept and applied. Their edits are written inline, which wins
over it, so a class you defined stays the default and their change is the exception. That is the
right arrangement: put the repeated parts in classes.

## Check it, and then look at it

One command does both:

```
python3 <skill>/scripts/check_design.py screens/settings.html
```

It reports what you cannot see — nodes outside the editable dialect, colours written as literals,
spacing off the scale, text failing contrast, which inventory components the screen is made of,
anything wider than what contains it, the height at a narrow and a wide column — and it exits non-zero while any of that is outstanding, so you can
chain it while you iterate. Fix what it names before you attach the file; every one of those is
something they would otherwise have to tell you.

**Do not do the checker's arithmetic in your head.** Contrast especially: it measures the
rendered text against what is *actually* behind it, which includes the tint, the overlay and
the inherited background, and you cannot know any of those until it renders. Computing WCAG
ratios by hand before the run costs a great deal and answers a question you were about to be
told the real answer to — and it is a different question, because your version compares the
two colours you had in mind rather than the two the browser produced. Write the screen, run
the checker, fix what it names.

**A clean run is not the bar.** The script says so itself: it is deliberately blind to whether
the thing looks good. Every check it runs is hygiene. A screen can pass all of them and be
completely forgettable, and if you treat exit zero as done, forgettable is what you will ship,
because passing the checker is the only part of this with a green light attached.

**So the run ends by looking.** It writes two PNGs, wide and narrow, printed on the `shot` line.
Open them with `read_file` — an image comes back to you as a picture — and then write three
lines before you do anything else:

* what your eye actually hits first;
* whether that is the thing the plan said it should be;
* the one thing you are going to change.

Then change it. A look that produced no change is a look you did not really take. Do this on
every pass, not only the first.

If the pictures come back as a note saying the model cannot see images, say that to the person
in your reply rather than quietly proceeding — on that model you are designing blind, and they
should know that the screen they are about to look at is one nobody has looked at yet.

**Do not go looking for another way to see it.** There is no screenshot tool on this machine: the
window's own capture needs a display surface and a headless turn has none, so `read_file` can
*show* you a picture but nothing else will *make* one. The way to see a design is this script.
Standing up a web server and opening a browser panel works and is three moving parts where one
will do.

## After they have edited it

**Do not write a generator.** A script that prints screens from Python functions gets you reuse
and loses the editor: the next run overwrites every change he made by hand. Parts and
`<k-use>` are the reuse, and the files stay the thing he edits.

**Re-read the file before you touch it again.** This is the one that will catch you out. Your
default assumption is that a file says what you last wrote in it, and for a design that is wrong
by design — the whole point is that they change it. Their changes are the answer to the question
you were about to ask them.

He may also have changed a part — the editor has *Edit component* on every instance — or
detached one instance into plain markup because that screen needed to differ. A detached copy
is a decision, not drift: leave it detached unless he says otherwise.

So when you come back to a design: read it, look at what moved, and take that as the brief. If
they tightened every gap from 16 to 12, that is a preference, and it applies to the next screen
too. If they moved the primary action to the right, do not move it back.

**And then write the preference down.** A correction you only apply to this screen is one you
will make him give you again. If he changed something that is about the product rather than
about this screen, it goes into the conventions; if he reshaped a part you will need again, it
goes into the inventory. That is the difference between a tool he has to keep correcting and one
that learns the house style once.

Then build the thing they left you, not the thing you drew.
