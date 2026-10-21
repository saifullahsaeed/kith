import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { emulationFor, fitted, parseRatio, reachesKith, VIEWPORT_PRESETS } from "./web-views";

/**
 * The letterbox, which is the whole of `resize`'s geometry.
 *
 * `web-views.ts` is Electron end to end — every interesting behaviour in it needs a real
 * `WebContentsView`, which vitest cannot hold. What *can* be held to account here is the piece
 * that keeps a viewport honest, and it now has two halves that have to agree:
 *
 * * the drawn rectangle is centred in the pane and never a pixel past its edge — a view painting
 *   outside its pane would cover Kith's own chrome, which is the one thing that file must never
 *   allow;
 * * and the **scale** is the truth about the difference between the size the page is laid out at
 *   and the size it is painted at. A viewport bigger than the pane used to be silently cut down
 *   to the pane and still reported as the size that had been asked for, so `resize(preset:
 *   "desktop")` in a narrow pane laid the page out at the mobile breakpoint and said 1920×1080.
 *   Every judgement made from that was confident and about the wrong layout.
 */
const pane = { x: 120, y: 80, width: 1200, height: 700 };

describe("the letterbox", () => {
  it("fills the pane when no viewport is set", () => {
    expect(fitted(pane, null)).toEqual({ ...pane, scale: 1 });
  });

  it("lands exactly on the viewport, unscaled, when the pane can hold it", () => {
    const placed = fitted(pane, { width: 800, height: 600 });
    expect(placed).toEqual({ x: 320, y: 130, width: 800, height: 600, scale: 1 });
  });

  it("keeps the viewport's shape when the pane is too small for it", () => {
    // A phone is taller than this pane. The page is still laid out at 390×844 — that is what
    // `scale` is for — and what is *drawn* is the same rectangle shrunk, not a 390×700 crop of
    // it. The aspect ratio surviving is how you can tell the difference.
    const placed = fitted(pane, { width: 390, height: 844 });
    expect(placed.scale).toBeCloseTo(700 / 844, 5);
    expect(placed.height).toBe(700);
    expect(placed.width).toBe(Math.round(390 * (700 / 844)));
    expect(placed.width / placed.height).toBeCloseTo(390 / 844, 2);
    expect(placed.x).toBe(pane.x + Math.round((pane.width - placed.width) / 2));
  });

  it("never magnifies a viewport smaller than the pane", () => {
    // Scale only ever shrinks. A 320-wide page blown up to fill a 1200-wide pane would be a
    // picture of a phone layout, which is not what anybody asked to look at.
    expect(fitted(pane, { width: 320, height: 480 }).scale).toBe(1);
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
      expect(placed.scale).toBeLessThan(1);
    }
  });

  it("stays inside a small pane at every named size, and says how much it shrank", () => {
    // A pane narrower than any device. Every preset letterboxes down rather than spilling — and
    // every one of them reports a scale below 1, because a page drawn at a third of its size
    // while the answer claims the full size is the failure this whole thing exists to remove.
    const cramped = { x: 0, y: 0, width: 300, height: 240 };
    for (const viewport of Object.values(VIEWPORT_PRESETS)) {
      const placed = fitted(cramped, viewport);
      expect(placed.width).toBeLessThanOrEqual(300);
      expect(placed.height).toBeLessThanOrEqual(240);
      expect(placed.scale).toBeLessThan(1);
      expect(placed.scale).toBeGreaterThan(0);
    }
  });
});

describe("aspect ratios", () => {
  it("reads the ways a person writes one", () => {
    expect(parseRatio("16:9")).toBeCloseTo(16 / 9, 6);
    expect(parseRatio("16/9")).toBeCloseTo(16 / 9, 6);
    expect(parseRatio("16x9")).toBeCloseTo(16 / 9, 6);
    expect(parseRatio(" 9:16 ")).toBeCloseTo(9 / 16, 6);
    expect(parseRatio("1.5")).toBe(1.5);
  });

  it("answers zero for anything it cannot read, rather than guessing", () => {
    // Zero is the caller's cue to refuse out loud. A ratio quietly treated as 1 would lay the
    // page out square and report a size nobody asked for, which is the same class of quiet
    // wrongness as the clamped viewport above.
    for (const bad of ["", "wide", "16:", ":9", "16:0", "0:9", "-2", "16:9:3"]) {
      expect(parseRatio(bad)).toBe(0);
    }
  });
});

describe("the named sizes", () => {
  /* The `browser` tool offers the names and this file holds the pixels behind them. Read out of
   * the Python source rather than imported, because it is a different language in a different
   * process — and the two lists drifting apart is a tool offering a size nobody knows. */
  const tool = readFileSync(join(__dirname, "../../../server/kith/tools/browser.py"), "utf8");
  const declared = new Set(
    [...(/^PRESETS = \(([^)]*)\)/m.exec(tool)?.[1] ?? "").matchAll(/"([a-z-]+)"/g)].map((match) => match[1]),
  );

  it("are exactly the ones the browser tool offers, besides reset", () => {
    expect(declared.size).toBeGreaterThan(0);
    // `reset` is a verb dressed as a size; it is offered by the tool and handled before the
    // table is consulted, so it is the one name the table need not carry.
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

describe("Kith's own server, from inside the pane", () => {
  /* The block compared origins, and `http://localhost:8611` is not the origin
   * `http://127.0.0.1:8611` — so Kith's own interface, which carries the API token in the page it
   * serves, loaded signed in inside a pane the model drives. Every spelling of loopback on
   * Kith's ports has to be refused, and nothing else may be: the pane exists partly so that
   * somebody can open the app they are building on localhost. */
  const ports = new Set(["8611", "8610"]);

  it("is refused under every name loopback goes by", () => {
    for (const address of [
      "http://127.0.0.1:8611/",
      "http://localhost:8611/",
      "http://LOCALHOST:8611/settings",
      "http://localhost.:8611/",
      "http://app.localhost:8611/",
      "http://127.0.0.2:8611/",
      "http://0.0.0.0:8611/",
      "http://[::1]:8611/",
      "http://[::ffff:127.0.0.1]:8611/",
      "http://2130706433:8611/",
      "http://0x7f.1:8611/",
      "ws://localhost:8611/",
      "http://localhost:8610/api/chat",
    ]) {
      expect(reachesKith(address, ports), address).toBe(true);
    }
  });

  it("leaves every other local address alone", () => {
    for (const address of [
      "http://localhost:5173/",
      "http://127.0.0.1:3000/",
      "http://localhost/",
      "https://localhost/",
      "http://192.168.1.20:8611/",
      "https://example.com:8611/",
      "https://example.com/",
      "not a url",
    ]) {
      expect(reachesKith(address, ports), address).toBe(false);
    }
  });
});

/**
 * The device override goes on only once a tab has a renderer to put it on.
 *
 * `enableDeviceEmulation` on a tab that has not loaded anything yet dereferences null inside
 * Electron and takes the whole app down with SIGSEGV — Kith crashed that way twice on 2026-10-21,
 * the second time 39 seconds after a restart, when the first `resize` reached a fresh tab. There is
 * no exception to catch, so the only fix is not to make the call.
 */
describe("the device override", () => {
  const phone = { width: 390, height: 844 };

  it("is not set on a tab with no renderer, whatever the viewport", () => {
    expect(emulationFor({ live: false, viewport: phone, emulated: null }, 1)).toEqual({ act: "none" });
    expect(emulationFor({ live: false, viewport: null, emulated: { ...phone, scale: 1 } }, 1)).toEqual({ act: "none" });
  });

  it("is set once there is one", () => {
    expect(emulationFor({ live: true, viewport: phone, emulated: null }, 0.8)).toEqual({
      act: "enable",
      wanted: { ...phone, scale: 0.8 },
    });
  });

  it("is not set again when nothing changed, and is taken off when the viewport is", () => {
    const set = { ...phone, scale: 1 };
    expect(emulationFor({ live: true, viewport: phone, emulated: set }, 1)).toEqual({ act: "none" });
    expect(emulationFor({ live: true, viewport: null, emulated: set }, 1)).toEqual({ act: "disable" });
    expect(emulationFor({ live: true, viewport: null, emulated: null }, 1)).toEqual({ act: "none" });
  });
});
