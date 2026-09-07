---
name: driving-a-browser
description: Use when driving the Browser plugin — reading a page, filling a form, getting past a login, or checking something rendered. Covers how to spend the fewest rounds, when a screenshot is worth its tokens, and how to accept help from the person when you are stuck.
---

# Driving the browser

`mcp__browser__*` gives you a real Chromium. The **Browser** tab shows the person what you are
looking at, and they can click into it.

## Read before you look

`read` returns the page as text and costs a few hundred tokens. `open`, `click`, `type`, `look`
and `scroll` all return a **screenshot path**, and reading one costs a few thousand.

So: `read` to find out what a page says. Reach for the picture only when

- layout is the question ("is this button hidden?", "does it wrap on mobile?"),
- the text does not explain what you are seeing, or
- the person asked you what it *looks* like.

A screenshot path is a file. It does nothing until you `read_file` it — so a step that returns
one has not cost you anything yet, and you can decide.

## Click by text, not by selector

`click(text: "Sign in")` survives a redesign and is what a person would say. A CSS selector is
for when text will not do — two identical buttons, an icon with no label.

When a click fails, the reply lists what *is* clickable. Use that list rather than taking another
screenshot to find out.

## Getting past something you cannot do

Logins, captchas, a two-factor prompt, a cookie wall that will not dismiss. Do not grind at
these — **ask, and let them click.**

1. `look`, so there is a current screenshot in the tab.
2. Use `ask` to say what is in the way and what you need: *"there's a login on this page — could
   you sign in, or click the button you want me to use? I'll carry on from there."*
3. When they click on the picture, a coordinate lands in the plugin's store. Call
   `plugin_state(plugin: "browser")` to see it — it comes back as `click: {x, y}`.
4. Act on it, then call `plugin__browser__clear_click(confirm: true)` **straight away**. A click
   you leave in the store is a click you will act on again next time you look.

`plugin__browser__where` asks the tab directly, and its answer includes `pendingClick`. Use it
when you want to know whether they have done something without pulling the whole store.

## What the tab is for

It is a view, not the browser. Chromium runs in the plugin's own subprocess — a surface is a
sealed frame with no network, so it could not load a page even if you asked it to. The tab shows
the last screenshot, the URL, and a log of your steps, so the person can follow what you did
without reading the transcript.

If the tab is not open, everything above still works except `where` and the person clicking.
Nothing needs the tab to be open for you to drive.

## Costs worth knowing

| | |
| --- | --- |
| `read` on an ordinary page | a few hundred tokens |
| `read_file` on a screenshot | a few thousand |
| a step that returns a path you do not read | ~50 tokens |

The browser starts on your first call and stays up for the rest of the session, so the first
`open` is slower than the ones after it. That is Chromium starting, not the page being slow.

## Things it will not do

- **No downloads.** The plugin's storage is the only place it may write, and nothing serves
  files out of it. Read what you need from the page instead.
- **No second tab.** One page, so `back` is how you retrace.
- **A fixed 1280x820 window**, deliberately: a screenshot whose dimensions change between calls
  is one you cannot compare to the last.
