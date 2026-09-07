---
name: driving-a-browser
description: Use when driving the Browser tab — reading a page, filling a form, getting past a login, or checking how something looks. Covers spending the fewest rounds, when a screenshot is worth its tokens, and how to hand the wheel back when you are stuck.
---

# Driving the browser

The **Browser** tab is a real browser in one of Kith's panes, and you are not the only one
driving it. The person scrolls it, types in it and logs into it with their hands; you drive the
same page with `plugin__browser__*`. Not a copy of their page and not a screenshot of it — the
same one. That single fact is what most of this skill follows from.

## Read before you look

`read` returns the page as text and costs a few hundred tokens. `look` returns a **path to a
screenshot**, and reading one costs a few thousand.

So `read` to find out what a page says. Reach for the picture only when

- layout is the question ("is this button off screen?", "does it wrap?"),
- the text does not explain what you are seeing, or
- they asked what it *looks* like.

A path is not an image. `look` costs you almost nothing until you `read_file` it — so a step
that returns one has not spent anything yet, and the decision is still yours.

## Click by what it says

`click(text: "Sign in")` survives a redesign and is how a person would describe it. A selector
is for when text will not do — an icon with no label, two identical buttons.

When nothing matches, the answer lists **what is clickable**. Use that list rather than taking a
screenshot to find out; it is already in front of you and costs nothing.

To fill a field: `type(text: "…", into: "Work email")`, then `press(key: "Enter")` — or click
the button by name. `type` is a paste rather than keystrokes, so it survives fields that
reformat as you go.

## When you are stuck, hand the wheel back

A login, a captcha, two-factor, a cookie wall that will not dismiss. Do not grind at these.

**Just ask them to do it.** The tab is right there and it is the same page:

> "There's a login on this page — could you sign in? I'll carry on from where you leave it."

They type into the tab, and your next call is on the far side of it. Nothing needs clearing,
nothing needs passing back, and no coordinate goes anywhere. This is the one thing the old
screenshot version of this plugin could not do at all, and it is why almost everything else in
this skill is shorter than it used to be.

The session persists, so a login holds for later turns as well. It is the plugin's own browser
profile — not their everyday one — so it starts logged out and holds only what they have signed
into here.

## Things worth knowing

- **The app has to be running.** The browser is a view the Kith app draws, so a scheduled turn
  with no app has none. The call tells you in a sentence; do not retry it.
- **Opening, reading, clicking and typing all work with the tab shut.** The page is live whether
  or not anybody is looking at it, so you can do a whole errand and only then ask them to look.
- **`look` is the exception** — a screenshot needs the tab open, because there is no picture of a
  page that is not on screen. The call says so and tells you to use `read` instead, which works
  either way. Prefer `read` regardless; see the top of this skill.
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
| `read_file` on a screenshot | a few thousand |
| `look`, unread | ~50 tokens |
| every other call | ~50 tokens |
