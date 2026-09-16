import { beforeEach, describe, expect, it } from "vitest";

import {
  SIDEBAR_DEFAULT,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
  clamp,
  readSidebar,
} from "@/components/shell/sidebar";

const KEY = "kith-sidebar";

beforeEach(() => localStorage.clear());

describe("how wide the rail may be", () => {
  it("holds the floor", () => {
    expect(clamp(10)).toBe(SIDEBAR_MIN);
  });

  it("holds the ceiling", () => {
    expect(clamp(9000)).toBe(SIDEBAR_MAX);
  });

  it("leaves a width between them alone, rounded", () => {
    expect(clamp(301.4)).toBe(301);
  });
});

describe("what the rail opens as", () => {
  it("is the default when nothing has been stored", () => {
    expect(readSidebar()).toEqual({ width: SIDEBAR_DEFAULT, open: true });
  });

  it("remembers a width the last session settled on", () => {
    localStorage.setItem(KEY, JSON.stringify({ width: 320, open: true }));
    expect(readSidebar().width).toBe(320);
  });

  it("remembers a rail that was hidden", () => {
    localStorage.setItem(KEY, JSON.stringify({ width: SIDEBAR_DEFAULT, open: false }));
    expect(readSidebar().open).toBe(false);
  });

  /* Only an explicit `false` hides it. Everything else — absent, the wrong type, a shape this
   * key might grow later — means "no preference", and the rail is how you reach every
   * conversation, so the forgiving answer is the visible one. */
  it("opens when the stored value says anything but false", () => {
    localStorage.setItem(KEY, JSON.stringify({ width: 300, open: "no" }));
    expect(readSidebar().open).toBe(true);

    localStorage.setItem(KEY, JSON.stringify({ width: 300 }));
    expect(readSidebar().open).toBe(true);
  });

  /* Three ways a stored value can be useless, and none of them is a reason to fail a boot.
   * The rail has a correct default; a half-understood preference is not worth propagating. */
  it("falls back when the stored value is not JSON", () => {
    localStorage.setItem(KEY, "{half-writ");
    expect(readSidebar()).toEqual({ width: SIDEBAR_DEFAULT, open: true });
  });

  it("falls back when the stored value is the wrong shape", () => {
    localStorage.setItem(KEY, JSON.stringify(["not", "an", "object"]));
    expect(readSidebar().width).toBe(SIDEBAR_DEFAULT);
  });

  /* A width can go out of range without anyone editing storage by hand: drag the rail wide on a
   * 32" display, open the same app on a laptop. Clamping beats discarding — the intent ("I like
   * it wide") survives, it just meets this screen's ceiling. */
  it("clamps a stored width rather than discarding it", () => {
    localStorage.setItem(KEY, JSON.stringify({ width: 9000, open: true }));
    expect(readSidebar().width).toBe(SIDEBAR_MAX);
  });

  it("does not take a width that is not a number", () => {
    localStorage.setItem(KEY, JSON.stringify({ width: "wide", open: true }));
    expect(readSidebar().width).toBe(SIDEBAR_DEFAULT);
  });
});
