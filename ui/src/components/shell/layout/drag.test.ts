/**
 * Where a dropped tab lands, which is the whole of the drag that we implement ourselves.
 *
 * The corner case is the one worth a test: a pointer near two edges at once has to resolve to
 * the one it is deepest into, not to whichever the code happens to check first. An
 * implementation that tests left/right/top/bottom in order silently favours "left" at every
 * corner, and the symptom is a layout you cannot build a bottom split from without aiming away
 * from the corner you are looking at.
 */
import { describe, expect, it } from "vitest";

import { carriesTab, edgeAt, highlightFor, insertIndexAt, landingIndex, TAB_MIME } from "./drag";

/** A 400x200 pane at the origin, so the arithmetic in each case is readable. */
const rect = { left: 0, top: 0, width: 400, height: 200, right: 400, bottom: 200 } as DOMRect;

describe("which edge a drop is", () => {
  it("is the middle when the pointer is nowhere near an edge", () => {
    expect(edgeAt(rect, 200, 100)).toBe("center");
  });

  it("reads the four edges", () => {
    expect(edgeAt(rect, 10, 100)).toBe("left");
    expect(edgeAt(rect, 390, 100)).toBe("right");
    expect(edgeAt(rect, 200, 10)).toBe("top");
    expect(edgeAt(rect, 200, 190)).toBe("bottom");
  });

  it("resolves a corner to the edge it is deepest into", () => {
    // 12px from the left of 400 is 3%; 8px from the top of 200 is 4%. Left is nearer.
    expect(edgeAt(rect, 12, 8)).toBe("left");
    // 40px from the left is 10%; 4px from the top is 2%. Now top is nearer.
    expect(edgeAt(rect, 40, 4)).toBe("top");
    // And the same at the far corner, where an ordered check would still say "left".
    expect(edgeAt(rect, 398, 198)).toBe("right");
    expect(edgeAt(rect, 360, 199)).toBe("bottom");
  });

  it("keeps a middle to drop into on a narrow pane", () => {
    const narrow = { left: 0, top: 0, width: 300, height: 600, right: 300, bottom: 600 } as DOMRect;
    expect(edgeAt(narrow, 150, 300)).toBe("center");
  });

  it("does not divide by zero on a pane that has not been laid out", () => {
    const nothing = { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 } as DOMRect;
    expect(edgeAt(nothing, 0, 0)).toBe("center");
  });
});

describe("what a pane will accept", () => {
  it("recognises one of ours", () => {
    expect(carriesTab({ types: [TAB_MIME] } as unknown as DataTransfer)).toBe(true);
  });

  it("ignores a file dragged in from Finder", () => {
    expect(carriesTab({ types: ["Files"] } as unknown as DataTransfer)).toBe(false);
  });

  it("ignores a drag with nothing attached", () => {
    expect(carriesTab(null)).toBe(false);
  });
});

describe("the highlight", () => {
  it("covers the half a split would take", () => {
    expect(highlightFor("right")).toEqual({
      left: "50%",
      top: "0%",
      width: "50%",
      height: "100%",
    });
    expect(highlightFor("bottom").top).toBe("50%");
  });

  it("covers the whole pane when the drop is into its tab strip", () => {
    expect(highlightFor("center")).toEqual({
      left: "0%",
      top: "0%",
      width: "100%",
      height: "100%",
    });
  });
});

describe("which gap a strip drop is", () => {
  /** Three 100px tabs at 0, 101 and 201, the one-pixel gaps left in for realism. */
  const tabs = [
    { left: 0, right: 100 },
    { left: 101, right: 200 },
    { left: 201, right: 300 },
  ];

  it("is before the tab whose half the pointer is in", () => {
    expect(insertIndexAt(tabs, 40)).toBe(0);
    expect(insertIndexAt(tabs, 60), "the right half is the next gap").toBe(1);
    expect(insertIndexAt(tabs, 150)).toBe(1);
    expect(insertIndexAt(tabs, 250)).toBe(2);
  });

  it("is the end past the last tab", () => {
    expect(insertIndexAt(tabs, 350)).toBe(3);
  });

  it("is the start on an empty strip", () => {
    expect(insertIndexAt([], 10)).toBe(0);
  });
});

describe("where a caret drop lands", () => {
  it("shifts a drop past the dragged tab back by one", () => {
    /* The caret is drawn in the strip as it is on screen; the tab leaves it on the way down,
     * and every tab after it slides left one place. */
    expect(landingIndex(0, 2)).toBe(1);
    expect(landingIndex(1, 2)).toBe(1);
  });

  it("keeps a drop before the dragged tab where it is", () => {
    expect(landingIndex(2, 1)).toBe(1);
    expect(landingIndex(2, 2), "its own left edge is a no-op").toBe(2);
  });
});
