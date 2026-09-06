---
name: sketching
description: Use when drawing a diagram on the Sketchpad tab — how to place shapes so a sketch reads as a picture rather than a pile, and how to check what is already on it.
---

# Drawing on the Sketchpad

The board is 1000x1000 in its own units and scales to whatever the pane is. Nothing is
pixel-accurate and nothing needs to be.

## Before you draw

Read `plugin_state` for `sketchpad`. It tells you what is already there — `title`, and a
`shapes` count. Drawing on top of an existing sketch without looking is how you end up with two
overlapping diagrams and no way to tell which is which.

If there is something on it and you are starting a new picture, call `clear` first.

## Placing things

- **Name it first.** `set_title` with what the drawing is *of*. The tab shows it, so someone
  looking at the pane knows what they are looking at without asking.
- **Boxes want room.** A `rect` at `w: 180, h: 60` holds about four words of `label` at a
  readable size. Leave 40 units between boxes or the labels touch.
- **Lines are drawn from a point.** `x, y` is where it starts and `w, h` is the offset to where
  it ends — so a line right and down 100 is `w: 100, h: 100`, and one straight down is `w: 0`.
- **Colour means something.** `accent` for the thing being explained, `second` for anything
  supporting it, `dim` for context nobody needs to read, `text` for plain structure. Four
  choices on purpose: a diagram in nine colours is a diagram nobody can scan.

## What the person does with it

They can drag a shape, and dragging writes back into the plugin's own store — so the next thing
they say to you carries the new position. You do not have to ask whether they moved something;
read `plugin_state` and it is there.
