/**
 * The board's arithmetic, which is the half that decides whether a board is usable.
 *
 * Two things here are worth asserting rather than assuming. A design attached a minute ago has
 * no stored position, and the wrong answer — 0,0 — puts it underneath the first frame where
 * nobody will ever find it. And the stored file is a file in his workspace, so it is not trusted
 * input: a NaN coordinate does not throw, it removes a frame from the visible world, which is a
 * bug that cannot be diagnosed by looking at the board.
 */
import { describe, expect, it } from "vitest";

import {
  EMPTY,
  extent,
  FRAME_H,
  FRAME_W,
  frameHeight,
  layout,
  readBoard,
  writeBoard,
  ZOOM_RANGE,
} from "@/lib/design/board";
import { PRESETS } from "@/lib/design/frame";

describe("reading a board someone else wrote", () => {
  it("round-trips what it wrote", () => {
    const board = {
      frames: { "a.html": { x: 10, y: 20, w: 400 } },
      notes: [{ id: "n1", x: 0, y: 0, w: 240, text: "the baseline" }],
      view: { x: -30, y: 12, zoom: 0.5 },
    };
    expect(readBoard(writeBoard(board))).toEqual(board);
  });

  it("falls back to the derived layout rather than throwing", () => {
    expect(readBoard("not json at all")).toEqual(EMPTY);
    expect(readBoard("")).toEqual(EMPTY);
    expect(readBoard("[1,2,3]")).toEqual(EMPTY);
  });

  it("drops a frame whose position is not a number", () => {
    // The failure this prevents: NaN propagates into a transform, the frame renders nowhere, and
    // the board looks like it lost a design.
    const board = readBoard(
      JSON.stringify({ frames: { "a.html": { x: "left", y: 0, w: 400 }, "b.html": { x: 0, y: 0, w: 400 } } }),
    );
    expect(Object.keys(board.frames)).toEqual(["b.html"]);
  });

  it("repairs a width that would render a frame invisible", () => {
    const board = readBoard(JSON.stringify({ frames: { "a.html": { x: 0, y: 0, w: 2 } } }));
    expect(board.frames["a.html"].w).toBe(FRAME_W);
  });

  it("clamps a zoom that would leave nothing on screen", () => {
    expect(readBoard(JSON.stringify({ view: { x: 0, y: 0, zoom: 900 } })).view!.zoom).toBeLessThanOrEqual(ZOOM_RANGE[1]);
    expect(readBoard(JSON.stringify({ view: { x: 0, y: 0, zoom: 0 } })).view!.zoom).toBeGreaterThan(0);
  });

  it("caps note text rather than holding a novel", () => {
    const board = readBoard(JSON.stringify({ notes: [{ id: "n1", x: 0, y: 0, w: 240, text: "x".repeat(9000) }] }));
    expect(board.notes[0].text.length).toBe(2000);
  });
});

describe("layout — where a frame goes when nobody has said", () => {
  it("keeps every position that was arranged by hand", () => {
    const board = { frames: { "a.html": { x: 900, y: 40, w: 400 } }, notes: [] };
    expect(layout(["a.html"], board)["a.html"]).toEqual({ x: 900, y: 40, w: 400 });
  });

  it("puts new designs in a grid instead of on top of each other", () => {
    const placed = layout(["a.html", "b.html", "c.html", "d.html"], EMPTY);
    const points = Object.values(placed).map((f) => `${f.x},${f.y}`);
    expect(new Set(points).size).toBe(4);
    // Three across, then wrap.
    expect(placed["a.html"].y).toBe(placed["c.html"].y);
    expect(placed["d.html"].y).toBeGreaterThan(placed["a.html"].y);
  });

  it("places a newly attached design below the arrangement, never under it", () => {
    // The case that matters: he attaches a fifth variant while you are looking at four you
    // arranged. Landing it at 0,0 hides it beneath the one you care about most.
    const board = { frames: { "a.html": { x: 0, y: 0, w: FRAME_W } }, notes: [] };
    const placed = layout(["a.html", "new.html"], board);
    expect(placed["new.html"].y).toBeGreaterThanOrEqual(FRAME_H);
  });

  it("spaces rows by the height the frames are actually drawn at", () => {
    // A phone is drawn at its own proportions — 440 wide is ~950 tall — and the grid used to
    // step by a fixed 560-tall box, so every row of phones covered the bottom third of the row
    // above it. 54 mobile screens came up as a stack of cards with their lower halves hidden.
    const phones = ["a.html", "b.html", "c.html", "d.html"];
    const sizes = Object.fromEntries(phones.map((file) => [file, PRESETS.mobile]));
    const placed = layout(phones, EMPTY, sizes);
    const tall = frameHeight(placed["a.html"], PRESETS.mobile);
    expect(tall).toBeGreaterThan(FRAME_H);
    expect(placed["d.html"].y).toBeGreaterThanOrEqual(placed["a.html"].y + tall);
  });

  it("steps each row by its own tallest frame, not by the tallest on the board", () => {
    const files = ["p1.html", "p2.html", "p3.html", "w1.html", "w2.html", "w3.html", "last.html"];
    const sizes = {
      ...Object.fromEntries(files.slice(0, 3).map((file) => [file, PRESETS.mobile])),
      ...Object.fromEntries(files.slice(3).map((file) => [file, PRESETS.desktop])),
    };
    const placed = layout(files, EMPTY, sizes);
    const phone = frameHeight(placed["p1.html"], PRESETS.mobile);
    // The phones' row is as tall as a phone; the desktops' row below it is not.
    expect(placed["w1.html"].y).toBeGreaterThanOrEqual(phone);
    expect(placed["last.html"].y - placed["w1.html"].y).toBeLessThan(phone);
  });

  it("places a new design below a tall arrangement, not inside it", () => {
    const board = { frames: { "home.html": { x: 0, y: 0, w: FRAME_W } }, notes: [] };
    const sizes = { "home.html": PRESETS.mobile, "new.html": PRESETS.mobile };
    const placed = layout(["home.html", "new.html"], board, sizes);
    expect(placed["new.html"].y).toBeGreaterThanOrEqual(frameHeight(board.frames["home.html"], PRESETS.mobile));
  });

  it("ignores a stored position for a design that is no longer attached", () => {
    const board = { frames: { "gone.html": { x: 10, y: 10, w: 400 } }, notes: [] };
    expect(Object.keys(layout(["a.html"], board))).toEqual(["a.html"]);
  });
});

describe("extent — what fit-to-screen fits to", () => {
  it("covers every frame and every note", () => {
    const box = extent(
      { "a.html": { x: 0, y: 0, w: 400 }, "b.html": { x: 600, y: 0, w: 400 } },
      [{ id: "n1", x: -200, y: 0, w: 240, text: "" }],
    );
    expect(box.x).toBe(-200);
    expect(box.w).toBe(1200);
  });

  it("reaches the bottom of a frame taller than the default box", () => {
    const box = extent({ "a.html": { x: 0, y: 0, w: FRAME_W } }, [], { "a.html": PRESETS.mobile });
    expect(box.h).toBe(frameHeight({ w: FRAME_W }, PRESETS.mobile));
  });

  it("gives an empty board something to look at rather than a zero-sized box", () => {
    expect(extent({}, []).w).toBeGreaterThan(0);
    expect(extent({}, []).h).toBeGreaterThan(0);
  });
});
