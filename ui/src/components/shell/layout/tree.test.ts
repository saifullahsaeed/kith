/**
 * The layout tree's invariants, held one at a time.
 *
 * These are the failures a tiling layout actually ships with, and none of them is worth finding
 * through a drag gesture: a divider with nothing on one side, a tab that vanishes because it
 * was dropped onto the pane it came from, sizes that stop summing to 100 after a close so every
 * later drag is computed against the wrong total, an `active` index left pointing past the end
 * of a shortened tab list.
 *
 * Written against the pure functions rather than the rendered layout on purpose. Every one of
 * these is a property of the value; testing them through React would be testing
 * `react-resizable-panels` as well, slower, and with the actual assertion buried.
 */
import { describe, expect, it } from "vitest";

import {
  activateTab,
  closeTab,
  dockTab,
  findTab,
  hasTab,
  openTab,
  pane,
  panes,
  resizeSplit,
  split,
  tabKey,
  type Node,
} from "./tree";

const chat = (id: string) => ({ surface: "chat" as const, conversationId: id });
const work = { surface: "work" as const };
const roadmap = { surface: "roadmap" as const };
const files = { surface: "files" as const };

/** Every invariant, checked at once, so each test can assert its own point and still know the
 *  tree it produced is a legal one. */
function isSound(node: Node): true {
  const seen = new Set<string>();
  const walk = (one: Node): void => {
    if (one.kind === "pane") {
      expect(one.active).toBeGreaterThanOrEqual(0);
      if (one.tabs.length) expect(one.active).toBeLessThan(one.tabs.length);
      for (const tab of one.tabs) {
        const key = tabKey(tab);
        expect(seen.has(key), `${key} appears twice`).toBe(false);
        seen.add(key);
      }
      return;
    }
    expect(one.children.length, "a split with fewer than two children").toBeGreaterThanOrEqual(2);
    expect(one.sizes.length).toBe(one.children.length);
    expect(one.sizes.reduce((sum, n) => sum + n, 0)).toBeCloseTo(100, 6);
    one.children.forEach(walk);
  };
  walk(node);
  return true;
}

describe("opening", () => {
  it("puts a new tab in the pane it was asked for, and focuses it", () => {
    const left = pane([work]);
    const right = pane([roadmap]);
    const tree = split("row", [left, right]);

    const { tree: after, focused } = openTab(tree, files, right.id);

    expect(focused).toBe(false);
    const found = findTab(after, "files")!;
    expect(found.pane.id).toBe(right.id);
    expect(found.pane.active).toBe(found.index);
    isSound(after);
  });

  it("focuses an existing tab rather than making a second one", () => {
    const one = pane([work, roadmap], 0);
    const { tree, focused } = openTab(one, roadmap);

    expect(focused).toBe(true);
    expect(panes(tree)[0].tabs).toHaveLength(2);
    expect(panes(tree)[0].active).toBe(1);
    isSound(tree);
  });

  it("treats two conversations as two tabs and one conversation as one", () => {
    let tree: Node = pane([]);
    tree = openTab(tree, chat("a")).tree;
    tree = openTab(tree, chat("b")).tree;
    tree = openTab(tree, chat("a")).tree;

    expect(panes(tree)[0].tabs.map(tabKey)).toEqual(["chat:a", "chat:b"]);
    expect(panes(tree)[0].active, "reopening `a` focused it").toBe(0);
    isSound(tree);
  });

  it("opens into the first pane when the one asked for is gone", () => {
    const tree = pane([work]);
    const { tree: after } = openTab(tree, files, "a-pane-that-was-closed");

    expect(hasTab(after, "files")).toBe(true);
    isSound(after);
  });
});

describe("closing", () => {
  it("removes an emptied pane and collapses the split above it", () => {
    const left = pane([work]);
    const right = pane([roadmap]);
    const tree = split("row", [left, right]);

    const after = closeTab(tree, "work");

    expect(after.kind, "a split with one child must become that child").toBe("pane");
    expect(panes(after)).toHaveLength(1);
    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["roadmap"]);
    isSound(after);
  });

  it("collapses a chain of splits rather than leaving stubs behind", () => {
    const deep = split("column", [pane([files]), pane([roadmap])]);
    const tree = split("row", [pane([work]), deep]);

    const after = closeTab(closeTab(tree, "files"), "roadmap");

    expect(after.kind).toBe("pane");
    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["work"]);
    isSound(after);
  });

  it("keeps the last pane, empty, rather than leaving nothing on screen", () => {
    const after = closeTab(pane([work]), "work");

    expect(panes(after)).toHaveLength(1);
    expect(panes(after)[0].tabs).toEqual([]);
    isSound(after);
  });

  it("moves focus to a tab that still exists", () => {
    const one = pane([work, roadmap, files], 2);
    const after = closeTab(one, "files");

    expect(panes(after)[0].active).toBe(1);
    isSound(after);
  });

  it("leaves the sizes summing to 100 after a sibling goes", () => {
    const tree = split("row", [pane([work]), pane([roadmap]), pane([files])], [50, 30, 20]);

    const after = closeTab(tree, "roadmap");

    expect(after.kind).toBe("split");
    isSound(after);
    // The survivors keep their proportions: 50:20 becomes 71.4:28.6.
    const sizes = (after as { sizes: number[] }).sizes;
    expect(sizes[0] / sizes[1]).toBeCloseTo(50 / 20, 5);
  });

  it("does nothing for a tab that is not there", () => {
    const tree = pane([work]);
    expect(closeTab(tree, "roadmap")).toBe(tree);
  });
});

describe("docking", () => {
  it("splits the target on the side it was dropped", () => {
    const left = pane([work]);
    const right = pane([roadmap, files]);
    const tree = split("row", [left, right]);

    const after = dockTab(tree, "files", left.id, "top");

    expect(after.kind).toBe("split");
    const column = (after as { children: Node[] }).children[0];
    expect(column.kind).toBe("split");
    expect((column as { direction: string }).direction).toBe("column");
    // Dropped on the top edge, so it goes first.
    expect(panes(column)[0].tabs.map(tabKey)).toEqual(["files"]);
    expect(panes(column)[1].tabs.map(tabKey)).toEqual(["work"]);
    isSound(after);
  });

  it("puts it after the target when dropped on the far side", () => {
    const left = pane([work]);
    const tree = split("row", [left, pane([roadmap, files])]);

    const after = dockTab(tree, "files", left.id, "right");
    const row = (after as { children: Node[] }).children[0];

    expect(panes(row).map((p) => p.tabs.map(tabKey))).toEqual([["work"], ["files"]]);
    isSound(after);
  });

  it("moves a tab into another pane's strip", () => {
    const left = pane([work]);
    const right = pane([roadmap, files]);
    const tree = split("row", [left, right]);

    const after = dockTab(tree, "files", left.id, "center");

    expect(findTab(after, "files")!.pane.id).toBe(left.id);
    expect(panes(after).find((p) => p.id === right.id)!.tabs.map(tabKey)).toEqual(["roadmap"]);
    isSound(after);
  });

  it("is a no-op when a tab is dropped on the strip it came from", () => {
    const one = pane([work, roadmap], 1);
    expect(dockTab(one, "roadmap", one.id, "center")).toBe(one);
  });

  it("refuses to split a pane against its own only tab", () => {
    /* The source empties and is removed, so the pane being split no longer exists — without
     * this the tab is closed and never re-docked, and dragging a lone tab onto its own edge
     * silently loses it. */
    const one = pane([work]);
    expect(dockTab(one, "work", one.id, "right")).toBe(one);
    isSound(dockTab(one, "work", one.id, "right"));
  });

  it("still splits when the source pane has other tabs to keep it alive", () => {
    const one = pane([work, roadmap], 0);

    const after = dockTab(one, "roadmap", one.id, "right");

    expect(after.kind).toBe("split");
    expect(panes(after).map((p) => p.tabs.map(tabKey))).toEqual([["work"], ["roadmap"]]);
    isSound(after);
  });

  it("does nothing for a tab that is not there", () => {
    const tree = pane([work]);
    expect(dockTab(tree, "roadmap", tree.id, "left")).toBe(tree);
  });
});

describe("focus and sizes", () => {
  it("clamps an activation past the end instead of pointing at nothing", () => {
    const one = pane([work, roadmap], 0);
    const after = activateTab(one, one.id, 99);

    expect(panes(after)[0].active).toBe(1);
    isSound(after);
  });

  it("records a divider drag", () => {
    const tree = split("row", [pane([work]), pane([roadmap])]);
    const after = resizeSplit(tree, tree.id, [70, 30]);

    expect((after as { sizes: number[] }).sizes).toEqual([70, 30]);
    isSound(after);
  });

  it("ignores a size list that does not match the split", () => {
    const tree = split("row", [pane([work]), pane([roadmap])]);
    const after = resizeSplit(tree, tree.id, [70, 20, 10]);

    expect((after as { sizes: number[] }).sizes).toEqual([50, 50]);
    isSound(after);
  });
});
