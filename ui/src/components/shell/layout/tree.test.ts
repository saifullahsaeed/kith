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
  clearPlace,
  closePane,
  closeTab,
  dedupePlaces,
  dockTab,
  findTab,
  flipSplit,
  hasTab,
  moveTab,
  openAtSlot,
  openBeside,
  openTab,
  orderPinned,
  pane,
  paneBeside,
  paneWithPlace,
  renameTab,
  panes,
  resizeSplit,
  rotateSplit,
  slotFor,
  split,
  splitAbove,
  stampPlace,
  tabKey,
  type Node,
} from "./tree";

const chat = (id: string) => ({ surface: "chat" as const, conversationId: id });
const work = { surface: "work" as const };
const roadmap = { surface: "board" as const };
const files = { surface: "settings" as const };

/** Every invariant, checked at once, so each test can assert its own point and still know the
 *  tree it produced is a legal one. */
function isSound(node: Node): true {
  const seen = new Set<string>();
  const places = new Set<string>();
  const walk = (one: Node): void => {
    if (one.kind === "pane") {
      // Invariant 8. A place naming two panes is "which of these is the right column" with no
      // answer, and every pin pointing at it inherits the ambiguity.
      if (one.place !== undefined) {
        expect(places.has(one.place), `${one.place} names two panes`).toBe(false);
        places.add(one.place);
      }
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
    const found = findTab(after, "settings")!;
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

    expect(hasTab(after, "settings")).toBe(true);
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
    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["board"]);
    isSound(after);
  });

  it("collapses a chain of splits rather than leaving stubs behind", () => {
    const deep = split("column", [pane([files]), pane([roadmap])]);
    const tree = split("row", [pane([work]), deep]);

    const after = closeTab(closeTab(tree, "settings"), "board");

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
    const after = closeTab(one, "settings");

    expect(panes(after)[0].active).toBe(1);
    isSound(after);
  });

  it("leaves the sizes summing to 100 after a sibling goes", () => {
    const tree = split("row", [pane([work]), pane([roadmap]), pane([files])], [50, 30, 20]);

    const after = closeTab(tree, "board");

    expect(after.kind).toBe("split");
    isSound(after);
    // The survivors keep their proportions: 50:20 becomes 71.4:28.6.
    const sizes = (after as { sizes: number[] }).sizes;
    expect(sizes[0] / sizes[1]).toBeCloseTo(50 / 20, 5);
  });

  it("does nothing for a tab that is not there", () => {
    const tree = pane([work]);
    expect(closeTab(tree, "board")).toBe(tree);
  });
});

describe("docking", () => {
  it("splits the target on the side it was dropped", () => {
    const left = pane([work]);
    const right = pane([roadmap, files]);
    const tree = split("row", [left, right]);

    const after = dockTab(tree, "settings", left.id, "top");

    expect(after.kind).toBe("split");
    const column = (after as { children: Node[] }).children[0];
    expect(column.kind).toBe("split");
    expect((column as { direction: string }).direction).toBe("column");
    // Dropped on the top edge, so it goes first.
    expect(panes(column)[0].tabs.map(tabKey)).toEqual(["settings"]);
    expect(panes(column)[1].tabs.map(tabKey)).toEqual(["work"]);
    isSound(after);
  });

  it("puts it after the target when dropped on the far side", () => {
    const left = pane([work]);
    const tree = split("row", [left, pane([roadmap, files])]);

    const after = dockTab(tree, "settings", left.id, "right");
    const row = (after as { children: Node[] }).children[0];

    expect(panes(row).map((p) => p.tabs.map(tabKey))).toEqual([["work"], ["settings"]]);
    isSound(after);
  });

  it("moves a tab into another pane's strip", () => {
    const left = pane([work]);
    const right = pane([roadmap, files]);
    const tree = split("row", [left, right]);

    const after = dockTab(tree, "settings", left.id, "center");

    expect(findTab(after, "settings")!.pane.id).toBe(left.id);
    expect(panes(after).find((p) => p.id === right.id)!.tabs.map(tabKey)).toEqual(["board"]);
    isSound(after);
  });

  it("is a no-op when a tab is dropped on the strip it came from", () => {
    const one = pane([work, roadmap], 1);
    expect(dockTab(one, "board", one.id, "center")).toBe(one);
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

    const after = dockTab(one, "board", one.id, "right");

    expect(after.kind).toBe("split");
    expect(panes(after).map((p) => p.tabs.map(tabKey))).toEqual([["work"], ["board"]]);
    isSound(after);
  });

  it("does nothing for a tab that is not there", () => {
    const tree = pane([work]);
    expect(dockTab(tree, "board", tree.id, "left")).toBe(tree);
  });
});

describe("renaming a tab in place", () => {
  it("gives a draft chat its conversation without moving it", () => {
    const tree = pane([chat(""), work], 0);

    const after = renameTab(tree, "chat:", chat("c-9"));

    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["chat:c-9", "work"]);
    expect(panes(after)[0].active, "and without stealing focus").toBe(0);
    isSound(after);
  });

  it("keeps it in the pane it was in", () => {
    const left = pane([chat("")]);
    const tree = split("row", [left, pane([work])]);

    const after = renameTab(tree, "chat:", chat("c-9"));

    expect(findTab(after, "chat:c-9")!.pane.id).toBe(left.id);
    isSound(after);
  });

  it("refuses a rename onto a conversation already open", () => {
    /* Two tabs claiming one conversation is two runtimes on one stream. The caller finding out
     * its rename did nothing and closing the draft is the better end. */
    const tree = pane([chat(""), chat("c-9")], 0);

    expect(renameTab(tree, "chat:", chat("c-9"))).toBe(tree);
  });

  it("does nothing for a tab that is not there, or a name that has not changed", () => {
    const tree = pane([chat("c-1")]);
    expect(renameTab(tree, "chat:nope", chat("c-2"))).toBe(tree);
    expect(renameTab(tree, "chat:c-1", chat("c-1"))).toBe(tree);
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

describe("moving and sorting tabs", () => {
  it("reorders a tab within its pane", () => {
    const one = pane([work, roadmap, files], 0);
    const after = moveTab(one, "work", one.id, 2);

    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["board", "settings", "work"]);
    isSound(after);
  });

  it("keeps the pane showing what it was showing while a background tab moves", () => {
    /* The strip renumbered; what the pane is showing is identity, not position — found again
     * by uid, the rule closeTab already paid for. Board was active and stays active. */
    const one = pane([work, roadmap, files], 1);
    const after = moveTab(one, "work", one.id, 2);

    expect(panes(after)[0].active).toBe(0);
    isSound(after);
  });

  it("moves the active tab without taking focus anywhere else", () => {
    const one = pane([work, roadmap], 0);
    const after = moveTab(one, "work", one.id, 1);

    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["board", "work"]);
    expect(panes(after)[0].active).toBe(1);
    isSound(after);
  });

  it("is a no-op when the move ends where it started", () => {
    const one = pane([work, roadmap], 1);
    expect(moveTab(one, "work", one.id, 0)).toBe(one);
  });

  it("clamps a position past the end to the end", () => {
    const one = pane([work, roadmap], 0);
    const after = moveTab(one, "work", one.id, 9);

    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["board", "work"]);
    isSound(after);
  });

  it("moves into another pane's strip where it was aimed, not at the end", () => {
    const left = pane([work, roadmap]);
    const right = pane([files]);
    const tree = split("row", [left, right]);

    const after = moveTab(tree, "settings", left.id, 1);

    const mine = panes(after).find((p) => p.id === left.id)!;
    expect(mine.tabs.map(tabKey)).toEqual(["work", "settings", "board"]);
    expect(mine.active, "the moved tab activates").toBe(1);
    isSound(after);
  });

  it("collapses the pane it emptied, and still lands the tab", () => {
    const left = pane([work]);
    const right = pane([roadmap]);
    const tree = split("row", [left, right]);

    const after = moveTab(tree, "work", right.id, 0);

    expect(after.kind, "the emptied source pane went with the move").toBe("pane");
    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["work", "board"]);
    isSound(after);
  });

  it("does nothing for a tab that is not there, or a pane that is not there", () => {
    const one = pane([work]);
    expect(moveTab(one, "board", one.id, 0)).toBe(one);
    expect(moveTab(one, "work", "a-pane-that-was-closed", 0)).toBe(one);
  });
});

describe("opening beside", () => {
  it("splits a pane of its own off the anchor", () => {
    const left = pane([work]);
    const tree = split("row", [left, pane([roadmap])]);

    const { tree: after, paneId } = openBeside(tree, files, left.id);

    expect(after.kind).toBe("split");
    expect(findTab(after, "settings")!.pane.id).toBe(paneId);
    // Right of the anchor: work, settings, board.
    expect(panes(after).map((p) => p.tabs.map(tabKey))).toEqual([
      ["work"],
      ["settings"],
      ["board"],
    ]);
    isSound(after);
  });

  it("splits below when asked, which is how a column gets built", () => {
    const one = pane([work]);
    const { tree: after } = openBeside(one, roadmap, one.id, "bottom");

    expect(after.kind).toBe("split");
    expect((after as { direction: string }).direction).toBe("column");
    isSound(after);
  });

  it("falls back to the first pane when the anchor is gone", () => {
    const tree = split("row", [pane([work]), pane([roadmap])]);

    const { tree: after, paneId } = openBeside(tree, files, "a-pane-that-was-closed");

    expect(findTab(after, "settings")!.pane.id).toBe(paneId);
    isSound(after);
  });

  it("falls back to the first pane even when it is an empty one", () => {
    /* A root is always a pane — invariant 5 — so "nothing to split" never happens; the anchor
     * being unknown falls to the first pane, empty or not, and splits it. */
    const { tree, paneId } = openBeside(pane([]), files, "a-pane-that-was-closed");

    expect(tree.kind).toBe("split");
    expect(findTab(tree, "settings")!.pane.id).toBe(paneId);
    isSound(tree);
  });
});

describe("places", () => {
  it("names one pane, and stamping a second takes it off the first", () => {
    const tree = split("row", [pane([work], 0, "a", "side"), pane([roadmap], 0, "b")]);

    const after = stampPlace(tree, "b", "side");

    expect(paneWithPlace(after, "side")!.id).toBe("b");
    isSound(after);
  });

  it("comes off a pane when asked, and leaves the key absent rather than undefined", () => {
    const after = clearPlace(pane([work], 0, "a", "side"), "a");
    expect("place" in after).toBe(false);
  });

  it("survives every operation that keeps the pane", () => {
    /* The claim the design rests on: no operation here can lose or duplicate a place, because
     * `dockTab` splits by building a *new* pane and keeping the original object, and everything
     * else spreads the pane it found. */
    let tree: Node = split("row", [
      pane([work, roadmap], 0, "a", "side"),
      pane([files], 0, "b"),
    ]);
    tree = dockTab(tree, "board", "b", "right");
    tree = moveTab(tree, "settings", "b", 0);
    tree = activateTab(tree, "a", 0);
    tree = closeTab(tree, "board");

    expect(paneWithPlace(tree, "side")!.tabs.map(tabKey)).toEqual(["work"]);
    isSound(tree);
  });

  it("goes with the pane when the pane goes, which is what a homeless pin is for", () => {
    const tree = split("row", [pane([work], 0, "a", "side"), pane([roadmap], 0, "b")]);
    expect(paneWithPlace(closeTab(tree, "work"), "side")).toBeNull();
  });

  it("is kept by an emptied last pane, because a pane emptied is not a place abandoned", () => {
    const after = closePane(pane([work], 0, "only", "side"), "only");
    expect(after.kind === "pane" && after.place).toBe("side");
    expect(panes(after)[0].tabs).toEqual([]);
  });

  it("keeps the winner with the most pins when a tree arrives already broken", () => {
    /* Not reachable through the operations above — this is a hand-edited or twice-stamped tree,
     * and the answer has to be deterministic rather than "whichever the walk saw last". */
    const broken = split("row", [
      pane([work], 0, "a", "side"),
      pane([roadmap, files], 0, "b", "side"),
    ]);

    const fixed = dedupePlaces(broken, (key) => key === "board" || key === "settings");

    expect(paneWithPlace(fixed, "side")!.id).toBe("b");
    isSound(fixed);
  });
});

describe("a slot", () => {
  const three = () =>
    split(
      "row",
      [pane([work], 0, "a"), pane([roadmap], 0, "b"), pane([files], 0, "c")],
      [18, 56, 26],
    );

  it("is the pane's index among the root's children, middle columns included", () => {
    expect(slotFor(three(), "b")).toEqual({ direction: "row", index: 1, size: 56 });
  });

  it("reports the column a nested pane visually belongs to", () => {
    const nested = split(
      "row",
      [pane([work], 0, "a"), split("column", [pane([roadmap], 0, "b"), pane([files], 0, "c")])],
      [30, 70],
    );
    expect(slotFor(nested, "c")).toEqual({ direction: "row", index: 1, size: 70 });
  });

  it("is the whole window when the root is the pane", () => {
    expect(slotFor(pane([work], 0, "a"), "a")).toEqual({ direction: "row", index: 0, size: 100 });
  });

  it("is null for a pane that is not in the tree", () => {
    expect(slotFor(three(), "gone")).toBeNull();
  });

  it("replays into the middle of a row, not at an edge", () => {
    const { tree, paneId } = openAtSlot(three(), { surface: "context" }, slotFor(three(), "b")!, "mid");

    const order = panes(tree).map((one) => one.tabs.map(tabKey));
    expect(order).toEqual([["work"], ["context"], ["board"], ["settings"]]);
    expect(paneWithPlace(tree, "mid")!.id).toBe(paneId);
    isSound(tree);
  });

  it("clamps into a root that has since lost columns", () => {
    const two = split("row", [pane([work], 0, "a"), pane([roadmap], 0, "b")], [40, 60]);
    const { tree } = openAtSlot(two, { surface: "context" }, { direction: "row", index: 9, size: 20 }, "far");

    expect(panes(tree).map((one) => one.tabs.map(tabKey))).toEqual([
      ["work"],
      ["board"],
      ["context"],
    ]);
    isSound(tree);
  });

  it("splits a bare root, and does not claim the whole window doing it", () => {
    const { tree } = openAtSlot(pane([work], 0, "a"), { surface: "context" }, { direction: "row", index: 0, size: 100 }, "p");

    expect(tree.kind).toBe("split");
    // A slot recorded at 100 — the root *was* one pane — clamped, or the pane it split has no
    // room left at all.
    expect(tree.kind === "split" && tree.sizes[0]).toBe(90);
    isSound(tree);
  });
});

describe("pinned tabs sitting at the front", () => {
  it("moves them to the prefix, keeping the order inside each group", () => {
    const tree = pane([work, roadmap, files, chat("c-1")], 0, "a");

    const after = orderPinned(tree, (key) => key === "settings" || key === "chat:c-1");

    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["settings", "chat:c-1", "work", "board"]);
    isSound(after);
  });

  it("keeps the pane showing what it was showing", () => {
    const tree = pane([work, roadmap, files], 1, "a");
    const after = orderPinned(tree, (key) => key === "settings");
    expect(panes(after)[0].tabs[panes(after)[0].active]).toMatchObject({ surface: "board" });
  });

  it("returns the same tree when there is nothing to do", () => {
    const tree = pane([work, roadmap], 0, "a");
    expect(orderPinned(tree, () => false)).toBe(tree);
    expect(orderPinned(tree, () => true)).toBe(tree);
  });
});

describe("restructuring", () => {
  const two = () => split("row", [pane([work], 0, "a"), pane([roadmap, files], 0, "b")], [40, 60]);

  it("closes a pane and everything in it, collapsing the split", () => {
    const after = closePane(two(), "b");
    expect(after.kind).toBe("pane");
    expect(panes(after)[0].tabs.map(tabKey)).toEqual(["work"]);
    isSound(after);
  });

  it("empties the last pane rather than leaving nothing on screen", () => {
    const after = closePane(pane([work], 0, "only"), "only");
    expect(panes(after)).toHaveLength(1);
    expect(panes(after)[0].tabs).toEqual([]);
    isSound(after);
  });

  it("flips a split's axis and carries the proportions across", () => {
    const before = two();
    const after = flipSplit(before, before.id);
    expect(after.kind === "split" && after.direction).toBe("column");
    expect(after.kind === "split" && after.sizes).toEqual([40, 60]);
    isSound(after);
  });

  it("reverses a split's children and their sizes together", () => {
    const before = two();
    const after = rotateSplit(before, before.id);
    expect(panes(after).map((one) => one.id)).toEqual(["b", "a"]);
    expect(after.kind === "split" && after.sizes).toEqual([60, 40]);
    isSound(after);
  });

  it("finds the split a pane hangs from, and none for a pane that is the window", () => {
    const before = two();
    expect(splitAbove(before, "b")!.id).toBe(before.id);
    expect(splitAbove(pane([work], 0, "a"), "a")).toBeNull();
  });

  it("finds the split above a nested pane rather than the root", () => {
    const inner = split("column", [pane([roadmap], 0, "b"), pane([files], 0, "c")]);
    const outer = split("row", [pane([work], 0, "a"), inner]);
    expect(splitAbove(outer, "c")!.id).toBe(inner.id);
  });

  it("steps to the neighbouring pane, and stops rather than wrapping", () => {
    const tree = two();
    expect(paneBeside(tree, "a", 1)!.id).toBe("b");
    expect(paneBeside(tree, "a", -1)).toBeNull();
    expect(paneBeside(tree, "b", 1)).toBeNull();
  });
});

describe("surfaces bound to a conversation", () => {
  it("makes Work for two chats two different tabs", () => {
    const a = tabKey({ surface: "work", conversationId: "c-1" });
    const b = tabKey({ surface: "work", conversationId: "c-2" });
    expect(a).not.toBe(b);
  });

  it("keeps the same surface for the same chat a singleton", () => {
    expect(tabKey({ surface: "work", conversationId: "c-1" })).toBe(
      tabKey({ surface: "work", conversationId: "c-1" }),
    );
  });

  /* Board and Settings are about Kith, not about a chat, so a conversation on one of them is
   * not part of its identity — otherwise "open settings" from two chats would give you two
   * Settings tabs showing the same thing. */
  it("ignores a conversation on a surface that is not about one", () => {
    expect(tabKey({ surface: "settings", conversationId: "c-1" })).toBe("settings");
    expect(tabKey({ surface: "board", conversationId: "c-1" })).toBe("board");
  });

  it("binds a plugin surface too, alongside its instance", () => {
    const one = tabKey({ surface: "plugin", plugin: "cma", view: "feed", conversationId: "c-1" });
    const two = tabKey({ surface: "plugin", plugin: "cma", view: "feed", conversationId: "c-2" });
    expect(one).not.toBe(two);
    expect(one).toContain("plugin:cma/feed");
  });

  /* An unbound ref still keys the way it always did, which is what lets a tab stored before
   * this change keep working instead of turning into a second, duplicate tab. */
  it("keys an unbound surface the old way", () => {
    expect(tabKey({ surface: "work" })).toBe("work");
  });

  it("lets both chats keep their own Work open at once — invariant 4 still holds", () => {
    const tree = pane([
      { surface: "work" as const, conversationId: "c-1" },
      { surface: "work" as const, conversationId: "c-2" },
    ]);
    const keys = panes(tree)[0]!.tabs.map(tabKey);
    expect(new Set(keys).size).toBe(2);
    isSound(tree);
  });
});
