/**
 * The stored layout, and the one rule that matters about it: it can never stop the app opening.
 *
 * A layout outlives the code that wrote it. Someone reloads into a build whose tree has a
 * different shape, or into storage half-written by a crash, or into a browser where
 * `localStorage` throws on read. Every one of those has to end in a working window — so
 * anything unreadable, anything from a version this build does not know, and anything that
 * fails its own structural check is replaced by the default rather than repaired, guessed at,
 * or thrown.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  defaultLayout,
  layoutKey,
  layoutVersion,
  looksLikeLayout,
  placementsKey,
  readPlacements,
  readStored,
  useLayout,
} from "./store";
import { pane, panes, split, tabKey } from "./tree";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("what the app opens as", () => {
  it("is the three columns the fixed layout had", () => {
    const tree = defaultLayout();
    expect(panes(tree).map((one) => one.tabs.map(tabKey))).toEqual([
      ["conversations"],
      ["chat:"],
      ["work"],
    ]);
  });

  it("is itself a legal layout", () => {
    expect(looksLikeLayout(defaultLayout())).toBe(true);
  });
});

describe("reading what was stored", () => {
  it("round-trips a layout", () => {
    const tree = split("row", [pane([{ surface: "work" }]), pane([{ surface: "settings" }])]);
    localStorage.setItem(layoutKey, JSON.stringify({ version: layoutVersion, tree }));

    const back = readStored();

    expect(back).not.toBeNull();
    expect(panes(back!).map((one) => one.tabs.map(tabKey))).toEqual([["work"], ["settings"]]);
  });

  it("refuses a layout from a version it does not know", () => {
    const tree = defaultLayout();
    localStorage.setItem(layoutKey, JSON.stringify({ version: layoutVersion + 1, tree }));

    expect(readStored()).toBeNull();
  });

  it("refuses something that is not JSON", () => {
    localStorage.setItem(layoutKey, "{half-writ");
    expect(readStored()).toBeNull();
  });

  it("refuses a tree that would not survive being walked", () => {
    localStorage.setItem(
      layoutKey,
      JSON.stringify({
        version: layoutVersion,
        // A split with one child: legal JSON, and a divider with nothing on one side.
        tree: { kind: "split", id: "s", direction: "row", children: [pane([])], sizes: [100] },
      }),
    );

    expect(readStored()).toBeNull();
  });

  it("refuses a split whose sizes do not match its children", () => {
    localStorage.setItem(
      layoutKey,
      JSON.stringify({
        version: layoutVersion,
        tree: {
          kind: "split",
          id: "s",
          direction: "row",
          children: [pane([]), pane([])],
          sizes: [100],
        },
      }),
    );

    expect(readStored()).toBeNull();
  });

  it("survives storage that throws on read", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("private mode");
    });

    expect(readStored()).toBeNull();
  });

  it("is null when nothing has been stored yet", () => {
    expect(readStored()).toBeNull();
  });
});

describe("what counts as a layout", () => {
  it("rejects a surface this build does not know", () => {
    /* It passed the structural check and then threw during render, on `SURFACES[...]`, out of
     * stored data — the same class of failure as a duplicate pane id, and reachable the first
     * time a surface is renamed without bumping the version. */
    expect(
      looksLikeLayout({
        kind: "pane",
        id: "p",
        active: 0,
        tabs: [{ surface: "a-surface-that-was-renamed" }],
      }),
    ).toBe(false);
  });


  it("rejects the shapes that are not one", () => {
    expect(looksLikeLayout(null)).toBe(false);
    expect(looksLikeLayout("pane")).toBe(false);
    expect(looksLikeLayout({})).toBe(false);
    expect(looksLikeLayout({ kind: "pane" })).toBe(false);
    expect(looksLikeLayout({ kind: "elsewhere", id: "x" })).toBe(false);
  });

  it("rejects a pane whose tabs are not tabs", () => {
    expect(looksLikeLayout({ kind: "pane", id: "p", active: 0, tabs: ["work"] })).toBe(false);
  });

  it("accepts a pane with no tabs, which is what the last close leaves", () => {
    expect(looksLikeLayout({ kind: "pane", id: "p", active: 0, tabs: [] })).toBe(true);
  });
});

describe("where surfaces prefer to open", () => {
  it("remembers a placement and writes it where it can be read back", () => {
    const was = useLayout.getState().placements;
    try {
      useLayout.getState().setPlacement("settings", "beside");

      expect(useLayout.getState().placements.settings).toBe("beside");
      expect(JSON.parse(localStorage.getItem(placementsKey) ?? "{}").settings).toBe("beside");
      expect(readPlacements().settings).toBe("beside");
    } finally {
      useLayout.setState({ placements: was });
    }
  });

  it("ignores what this build cannot make sense of", () => {
    localStorage.setItem(
      placementsKey,
      JSON.stringify({
        settings: "beside",
        board: "somewhere-else",
        chat: 42,
        inbox: "focused",
      }),
    );

    const read = readPlacements();
    expect(read.settings).toBe("beside");
    expect(read.inbox).toBe("focused");
    expect(read.board).toBeUndefined();
    expect(read.chat).toBeUndefined();
  });

  it("is empty when storage has nothing to say", () => {
    expect(readPlacements()).toEqual({});
  });

  it("splits a new pane when the surface prefers beside, and focuses it", () => {
    useLayout.setState({ tree: pane([{ surface: "work" }]), focused: "" });
    const was = useLayout.getState().placements;
    try {
      useLayout.getState().setPlacement("settings", "beside");
      useLayout.getState().open({ surface: "settings" });

      const tree = useLayout.getState().tree;
      expect(tree.kind).toBe("split");
      const made = panes(tree)[1];
      expect(made.tabs.map(tabKey)).toEqual(["settings"]);
      expect(useLayout.getState().focused, "the pane you asked for is where you are").toBe(
        made.id,
      );
    } finally {
      useLayout.setState({ placements: was });
    }
  });
});
