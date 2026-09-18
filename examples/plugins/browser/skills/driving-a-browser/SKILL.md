---
name: driving-a-browser
description: Use when driving the Browser tab — reading a page, filling a form, getting past a login, or checking how something looks. Covers spending the fewest rounds, when a screenshot is worth its tokens, and how to hand the wheel back when you are stuck.
---

# Driving the browser

The **Browser** tab is a real browser in one of Kith's panes, and you are not the only one
driving it. The person scrolls it, types in it and logs into it with their hands; you drive the
same page with `plugin__browser__*`. Not a copy of their page and not a screenshot of it — the
same one. That single fact is what most of this skill follows from.

## Read before you look, and crop before you read

`read` returns the page as text and costs a few hundred tokens. `look` returns a **path to a
screenshot**, and reading one costs a few thousand.

So `read` to find out what a page says. Reach for the picture only when

- layout is the question ("is this button off screen?", "does it wrap?"),
- the text does not explain what you are seeing, or
- they asked what it *looks* like.

A path is not an image. `look` costs you almost nothing until you `read_file` it — so a step
that returns one has not spent anything yet, and the decision is still yours.

**And when you do look, look at the part you were asked about.** `look(text: "Pricing")` or
`look(selector: ".card")` crops to that component with a margin of page around it, and the
answer carries the selector it used so you can click or type into the same thing afterwards.
A crop of a card is a few hundred tokens where the page it sits on is a few thousand, and the
rest of the page was not the question. The margin is deliberate: a component cropped exactly to
its own edges cannot show you that it is overlapping its neighbour, which is half of what
anybody is asking.

Nothing matches? The answer lists what is on the page, the same way `click`'s does. Use the
list rather than falling back to a picture of everything.

## Click by what it says

`click(text: "Sign in")` survives a redesign and is how a person would describe it. A selector
is for when text will not do — an icon with no label, two identical buttons.

When nothing matches, the answer lists **what is clickable**. Use that list rather than taking a
screenshot to find out; it is already in front of you and costs nothing.

To fill a field: `type(text: "…", into: "Work email")`, then `press(key: "Enter")` — or click
the button by name. `type` is a paste rather than keystrokes, so it survives fields that
reformat as you go.

## Set the viewport before you judge a layout

`resize` lays the page out at a size you name — the real thing, not a scaled picture: media
queries and every breakpoint see exactly that width. Use it when the question is "what does
this look like on a phone", and to check the layout they are building.

- A named size is usually the right call: `resize(preset: "phone")` for a phone, `"tablet"`
  for a tablet, `"laptop"` or `"desktop"` for the sizes a page is designed at. `"square"` is
  there for a feed or a canvas that should hold up at 1:1. The sizes behind the names are
  phone 390×844, phone-landscape 844×390, tablet 820×1180, tablet-landscape 1180×820,
  laptop 1280×800, desktop 1920×1080, square 900×900.
- `resize(width: 320)` is the precise version — an odd width from a design file, a screenshot
  spec. Width and height are independent: give only the one you mean to change.
- `resize(ratio: "16:9")` reshapes around the width already in force, and a ratio given
  alongside one dimension settles the other: `resize(width: 1440, ratio: "16:10")` is 1440×900
  without you doing the arithmetic. `"21:9"`, `"9:16"`, `"4:3"`, `"1:1"` all read.
- **The pane's size does not change the layout any more, and `scale` is how you know.** The
  page is laid out at exactly the size you asked for whatever the pane can hold, and painted
  smaller when it will not fit — so `resize(preset: "desktop")` in a narrow pane really is the
  desktop breakpoint, and the answer's `scale` (0.6, say) tells you the picture is at 60%. That
  used to be a lie: the viewport was quietly cut down to the pane and reported as the size you
  asked for, so a "desktop" check in a narrow pane was a look at the mobile layout. If you are
  reading old notes that treat a small pane as a constraint on `resize`, they are out of date.
- **Read or look afterwards.** Resizing changes nothing a person would call content; the
  answer tells you the size that now holds, and `read` or `look` is how you see what it did
  to the page. A `look` at a phone viewport is the cheap way to answer "does the nav
  collapse?".
- **Put it back.** `resize(preset: "reset")` — or a bare `resize()` — returns the page to the
  full tab when you are done. The person sees a smaller page centred in their tab otherwise,
  and the chip in the tab's header clears it with one click if you forget.

## When they know which one they mean, let them point at it

There is a crosshair in the tab's header. They click it, click a component on the page, and it
arrives in the composer as a cropped picture with a caption carrying **its selector** and the
words on it.

That matters to you in two ways. The obvious one is that a picture that turns up with
``` `main > section:nth-of-type(2) > div.card` ``` attached is a component you can `look` at
again, `click`, or `type` into without looking for it first. The less obvious one is what to do
*before* it arrives: when somebody is describing one card among nine and the description is not
landing, stop narrowing it down by guessing. Say the crosshair is there and ask them to point.
One gesture settles what three rounds of "the third one, no, the one below it" does not.

You cannot start it yourself — it is their hand on the mouse, by design. `look(text: …)` is your
half of the same idea.

## When you are stuck, hand the wheel back

A login, a captcha, two-factor, a cookie wall that will not dismiss. Do not grind at these.

**Call `show`, then ask them.** The tab comes to the front and it is the same page they are
looking at:

> "There's a login on this page — could you sign in? I'll carry on from where you leave it."

They type into the tab, and your next call is on the far side of it. Nothing needs clearing,
nothing needs passing back, and no coordinate goes anywhere. This is the one thing the old
screenshot version of this plugin could not do at all, and it is why almost everything else in
this skill is shorter than it used to be.

The session persists, so a login holds for later turns as well. It is the plugin's own browser
profile — not their everyday one — so it starts logged out and holds only what they have signed
into here.

**There is a second way through a login, and it is worth offering when signing in by hand would
be tedious.** The key button in the tab's header copies one site's cookies out of a browser they
are already signed in on — Chrome, Arc, Brave, Edge, Firefox — into this profile. One domain
that they name, never a whole profile, and macOS asks for that browser's key on the way. So:

> "You're signed into this in Chrome already — the key button in the Browser tab will copy that
> login across for this domain, then I'll carry on."

Like the crosshair, it is theirs to press and not yours to call. Once it has landed, reload the
page and continue.

## Things worth knowing

- **The app has to be running.** The browser is a view the Kith app draws, so a scheduled turn
  with no app has none. The call tells you in a sentence; do not retry it.
- **Opening, reading, clicking and typing all work with the tab shut.** The page is live whether
  or not anybody is looking at it, so you can do a whole errand and only then ask them to look.
- **`look` is the exception** — a screenshot needs the tab open, because there is no picture of a
  page that is not on screen. Call `show` first and it will be. Prefer `read` regardless; see the
  top of this skill.
- **`show` puts the tab on screen yourself.** Use it before a screenshot, and use it when you
  want them to *look* — "I've put it in the tab, have a scroll" beats describing a page.
- **One page.** No tabs, so `back` is how you retrace, and a link that would open a new window
  opens in this one instead.
- **The person can see everything you do.** Every navigation, every click. That is a feature —
  you do not have to narrate what you are looking at — but it also means a page you open is a
  page they are looking at.
- **Kith's own address is unreachable** from in here, deliberately. Other local addresses are
  not: opening `localhost:5173` to look at something they are building is expected.

## Costs

| | |
| --- | --- |
| `read` on an ordinary page | a few hundred tokens |
| `read_file` on a whole-page screenshot | a few thousand |
| `read_file` on a `look` cropped to one component | a few hundred |
| `look`, unread | ~50 tokens |
| every other call | ~50 tokens |
