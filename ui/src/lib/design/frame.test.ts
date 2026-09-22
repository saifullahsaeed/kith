/**
 * How big the screen is, and why the surface must not decide.
 *
 * Every design used to be rendered at a hardcoded 1200 into a fixed 560-tall box, so a phone
 * could not be drawn: a mobile screen was laid out as a desktop and then scaled down, and the
 * columns that should have stacked did not. These pin the declaration and its bounds; the
 * checker holds the same table in `check_design.py`, which its own frame line asserts.
 */
import { describe, expect, it } from "vitest";

import { DEFAULT_FRAME, describeFrame, frameAttribute, PRESETS, readFrame } from "@/lib/design/frame";

describe("reading a declared frame", () => {
  it("takes a preset by name", () => {
    expect(readFrame("mobile")).toEqual({ ...PRESETS.mobile, name: "mobile" });
  });

  it("does not care about case or surrounding space", () => {
    expect(readFrame("  Desktop ").width).toBe(1440);
  });

  it("takes exact pixels", () => {
    expect(readFrame("1600x1000")).toEqual({ width: 1600, height: 1000, name: "custom" });
  });

  it("accepts the multiplication sign people actually type", () => {
    expect(readFrame("390×844").width).toBe(390);
  });

  it("falls back rather than failing the design", () => {
    // A typo in one attribute must not decide whether the screen renders at all.
    for (const bad of ["", "enormous", "0x0", "99999x10", "12x12", "1440", "<script>"]) {
      expect(readFrame(bad)).toEqual(DEFAULT_FRAME);
    }
  });

  it("defaults to what designs were implicitly authored against", () => {
    // Changing this would resize work nobody touched.
    expect(DEFAULT_FRAME).toEqual({ width: 1200, height: 800, name: "default" });
  });
});

describe("lifting the attribute out of source", () => {
  it("finds it on body", () => {
    expect(frameAttribute('<body data-k-frame="mobile"><div>a</div></body>')).toBe("mobile");
  });

  it("finds it on the outermost element of a fragment", () => {
    expect(frameAttribute('<div data-k-frame="tablet">a</div>')).toBe("tablet");
  });

  it("is null when nothing said", () => {
    expect(frameAttribute("<div>a</div>")).toBeNull();
  });
});

describe("saying it back", () => {
  it("names a preset and its size", () => {
    expect(describeFrame(readFrame("mobile"))).toBe("mobile · 390×844");
  });

  it("gives just the numbers when there is no name for them", () => {
    expect(describeFrame(readFrame("1600x1000"))).toBe("1600×1000");
  });
});
