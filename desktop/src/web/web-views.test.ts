import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { fitted, VIEWPORT_PRESETS } from "./web-views";

/**
 * The letterbox, which is the whole of `resize`'s geometry.
 *
 * `web-views.ts` is Electron end to end — every interesting behaviour in it needs a real
 * `WebContentsView`, which vitest cannot hold. What *can* be held to account here is the piece
 * that keeps a viewport honest: the view's bounds are exactly the viewport, centred in the
 * pane, and never a pixel past the pane's edge. A view painting outside its pane would cover
 * Kith's own chrome, which is the one thing this file must never allow — so the invariant is
 * tested, not argued.
 */
const pane = { x: 120, y: 80, width: 1200, height: 700 };

describe("the letterbox", () => {
  it("fills the pane when no viewport is set", () => {
    expect(fitted(pane, null)).toEqual(pane);
  });

  it("is exactly the viewport, centred in the pane", () => {
    const placed = fitted(pane, { width: 390, height: 844 });
    expect(placed.width).toBe(390);
    expect(placed.height).toBe(700); // the pane is shorter than the phone; the pane wins
    expect(placed.x).toBe(pane.x + Math.round((pane.width - 390) / 2));
    expect(placed.y).toBe(pane.y);
  });

  it("lands exactly on the viewport when the pane can hold it", () => {
    const placed = fitted(pane, { width: 800, height: 600 });
    expect(placed).toEqual({ x: 320, y: 130, width: 800, height: 600 });
  });

  it("never paints past the pane, at any size", () => {
    for (const viewport of [
      { width: 3840, height: 2160 },
      { width: 1_000_000, height: 1_000_000 },
    ]) {
      const placed = fitted(pane, viewport);
      expect(placed.x).toBeGreaterThanOrEqual(pane.x);
      expect(placed.y).toBeGreaterThanOrEqual(pane.y);
      expect(placed.x + placed.width).toBeLessThanOrEqual(pane.x + pane.width);
      expect(placed.y + placed.height).toBeLessThanOrEqual(pane.y + pane.height);
    }
  });

  it("stays inside a small pane at every named size", () => {
    // A pane narrower than any device. Every preset must letterbox down rather than spill.
    const cramped = { x: 0, y: 0, width: 300, height: 240 };
    for (const viewport of Object.values(VIEWPORT_PRESETS)) {
      const placed = fitted(cramped, viewport);
      expect(placed.width).toBeLessThanOrEqual(300);
      expect(placed.height).toBeLessThanOrEqual(240);
    }
  });
});

describe("the named sizes", () => {
  const manifest = JSON.parse(
    readFileSync(join(__dirname, "../../../examples/plugins/browser/kith.plugin.json"), "utf8"),
  ) as {
    commands: { name: string; params: Record<string, { enum?: string[] }> }[];
  };

  it("are exactly the ones the browser plugin declares, besides reset", () => {
    const resize = manifest.commands.find((command) => command.name === "resize");
    expect(resize).toBeDefined();
    const declared = new Set(resize?.params.preset?.enum ?? []);
    // `reset` is a verb dressed as a size; it is declared in the manifest and handled before
    // the table is consulted, so it is the one name the table need not carry.
    declared.delete("reset");
    expect(new Set(Object.keys(VIEWPORT_PRESETS))).toEqual(declared);
  });

  it("are all landscape or portrait pairs of real devices, not guesses", () => {
    expect(VIEWPORT_PRESETS.phone).toEqual({ width: 390, height: 844 });
    expect(VIEWPORT_PRESETS["phone-landscape"]).toEqual({ width: 844, height: 390 });
    expect(VIEWPORT_PRESETS.tablet).toEqual({ width: 820, height: 1180 });
    expect(VIEWPORT_PRESETS.desktop).toEqual({ width: 1920, height: 1080 });
  });
});
