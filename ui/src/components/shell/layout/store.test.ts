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
  layoutsKey,
  looksLikeLayout,
  pinsKey,
  placementsKey,
  readLayouts,
  readPins,
  readPlacements,
  readStored,
  useLayout,
} from "./store";
import { ids, pane, paneWithPlace, panes, split, tabKey } from "./tree";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("what the app opens as", () => {
  /* One pane, full width. Work is a tab in the pane you are looking at now rather than a
   * column of its own — see `DEFAULT_PLACEMENT` — and the conversation list is the rail. */
  it("is a single full-width chat pane", () => {
    const tree = defaultLayout();
    expect(tree.kind).toBe("pane");
    expect(panes(tree).map((one) => one.tabs.map(tabKey))).toEqual([["chat:"]]);
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

  /* The one that motivated the version bump. A v2 tree is structurally fine — `looksLikeLayout`
   * walks it happily — and holds a `conversations` tab whose surface is no longer a key of
   * `SURFACES`, which `LayoutView` derefs above every per-surface boundary. Version is the only
   * thing that catches it. */
  it("refuses a layout from before the list left the tree", () => {
    const stale = split("row", [
      pane([{ surface: "conversations" } as unknown as { surface: "work" }]),
      pane([{ surface: "chat", conversationId: "" }]),
    ]);
    localStorage.setItem(layoutKey, JSON.stringify({ version: 2, tree: stale }));

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
        context: "focused",
      }),
    );

    const read = readPlacements();
    expect(read.settings).toBe("beside");
    expect(read.context).toBe("focused");
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

describe("the default is the section rule", () => {
  it("raises a pane for a kind that has none, and focuses it", () => {
    useLayout.setState({ tree: pane([{ surface: "work" }]), focused: "" });
    const was = useLayout.getState().placements;
    try {
      // The section rule, named: it is no longer the default, but it is still the rung this
      // test is about.
      useLayout.setState({ placements: { settings: "own" } });
      useLayout.getState().open({ surface: "settings" });

      const tree = useLayout.getState().tree;
      expect(tree.kind, "a section was raised").toBe("split");
      const made = panes(tree)[1];
      expect(made.tabs.map(tabKey)).toEqual(["settings"]);
      expect(useLayout.getState().focused, "the pane you asked for is where you are").toBe(
        made.id,
      );
    } finally {
      useLayout.setState({ placements: was });
    }
  });

  it("adds to the list a kind already keeps, without raising anything", () => {
    useLayout.setState({
      tree: split("row", [
        pane([{ surface: "chat", conversationId: "c-1" }], 0, "a"),
        pane([{ surface: "work" }], 0, "b"),
      ]),
      focused: "",
    });

    useLayout.getState().open({ surface: "chat", conversationId: "c-2" });

    const tree = useLayout.getState().tree;
    expect(tree.kind, "nothing was raised").toBe("split");
    expect(panes(tree).find((one) => one.id === "a")!.tabs.map(tabKey)).toEqual([
      "chat:c-1",
      "chat:c-2",
    ]);
    expect(panes(tree).find((one) => one.id === "b")!.tabs.map(tabKey)).toEqual(["work"]);
  });
});

describe("reading pins back", () => {
  it("round-trips one", () => {
    const pin = { place: "place-1", slot: { direction: "row" as const, index: 2, size: 26 } };
    localStorage.setItem(pinsKey, JSON.stringify({ work: pin }));
    expect(readPins()).toEqual({ work: pin });
  });

  it("drops the entries it cannot read and keeps the ones it can", () => {
    /* One bad pin must not cost the set. The layout gets the same treatment as a whole; a pin
     * is small enough to judge one at a time. */
    localStorage.setItem(
      pinsKey,
      JSON.stringify({
        work: { place: "p", slot: { direction: "row", index: 0, size: 20 } },
        board: { place: "p" },
        plugin: { place: "", slot: { direction: "row", index: 0, size: 20 } },
        settings: { place: "p", slot: { direction: "sideways", index: 0, size: 20 } },
        context: { place: "p", slot: { direction: "row", index: -1, size: 20 } },
      }),
    );
    expect(Object.keys(readPins())).toEqual(["work"]);
  });

  it("answers with nothing at all rather than throwing", () => {
    localStorage.setItem(pinsKey, "{ not json");
    expect(readPins()).toEqual({});
  });
});

describe("pinning", () => {
  beforeEach(() => {
    useLayout.setState({
      tree: split(
        "row",
        [pane([{ surface: "board" }], 0, "left"), pane([{ surface: "work" }], 0, "right")],
        [30, 70],
      ),
      focused: "right",
      order: ["right", "left"],
      pins: {},
      zoomed: null,
    });
  });

  it("mints the pane a place and records where it was", () => {
    useLayout.getState().pin("work");

    const { pins, tree } = useLayout.getState();
    expect(pins.work.slot).toEqual({ direction: "row", index: 1, size: 70 });
    expect(paneWithPlace(tree, pins.work.place)!.id).toBe("right");
  });

  it("puts a second pin on the same pane's existing place", () => {
    useLayout.getState().pin("work");
    useLayout.setState({ tree: useLayout.getState().tree });
    useLayout.getState().open({ surface: "context" }, { paneId: "right" });
    useLayout.getState().pin("context");

    const { pins } = useLayout.getState();
    expect(pins.context.place).toBe(pins.work.place);
  });

  it("does not re-record a slot for a tab that is already pinned", () => {
    useLayout.getState().pin("work");
    const first = useLayout.getState().pins.work;
    useLayout.getState().dock("work", "left", "center");
    useLayout.getState().pin("work");
    expect(useLayout.getState().pins.work).toEqual(first);
  });

  it("sends a closed pinned tab back to its place, not to the policy's answer", () => {
    /* A second tab so the pane outlives the close — the whole point being that reopening lands
     * in *that* pane rather than wherever `own` would have put it, which is a pane of its own
     * beside whatever is focused. */
    useLayout.getState().open({ surface: "context" }, { paneId: "right" });
    useLayout.getState().pin("work");
    useLayout.getState().focus("left");
    useLayout.getState().close("work");
    useLayout.getState().open({ surface: "work" });

    expect(
      panes(useLayout.getState().tree).find((one) => one.id === "right")!.tabs.map(tabKey),
    ).toEqual(["work", "context"]);
  });

  it("rebuilds the pane at the recorded slot when the whole pane went with the tab", () => {
    useLayout.getState().pin("work");
    const place = useLayout.getState().pins.work.place;
    useLayout.getState().closePane("right");
    expect(paneWithPlace(useLayout.getState().tree, place), "the place died with it").toBeNull();

    useLayout.getState().open({ surface: "work" });

    const tree = useLayout.getState().tree;
    expect(panes(tree).map((one) => one.tabs.map(tabKey))).toEqual([["board"], ["work"]]);
    expect(paneWithPlace(tree, place)).not.toBeNull();
  });

  it("keeps pinned tabs at the front of the strip however they are dropped", () => {
    useLayout.getState().open({ surface: "context" }, { paneId: "right" });
    useLayout.getState().pin("context");
    // Aim the unpinned tab at the very front of the strip. Invariant 7 sends it back.
    useLayout.getState().move("work", "right", 0);

    expect(panes(useLayout.getState().tree).find((one) => one.id === "right")!.tabs.map(tabKey))
      .toEqual(["context", "work"]);
  });

  it("carries the pin across a rename, which is how a pinned draft chat survives its first turn", () => {
    useLayout.getState().open({ surface: "chat", conversationId: "" }, { paneId: "right" });
    useLayout.getState().pin("chat:");
    const place = useLayout.getState().pins["chat:"].place;

    useLayout.getState().rename("chat:", { surface: "chat", conversationId: "c-9" });

    const { pins } = useLayout.getState();
    expect(pins["chat:"]).toBeUndefined();
    expect(pins["chat:c-9"]).toEqual({ place, slot: expect.anything() });
  });

  it("drops the place with the last pin on it, and not before", () => {
    useLayout.getState().open({ surface: "context" }, { paneId: "right" });
    useLayout.getState().pin("work");
    useLayout.getState().pin("context");
    const place = useLayout.getState().pins.work.place;

    useLayout.getState().unpin("work");
    expect(paneWithPlace(useLayout.getState().tree, place), "context is still pinned there").not
      .toBeNull();

    useLayout.getState().unpin("context");
    expect(paneWithPlace(useLayout.getState().tree, place)).toBeNull();
  });

  it("keeps a place while a pinned tab is closed, because it has somewhere to come back to", () => {
    useLayout.getState().pin("work");
    const place = useLayout.getState().pins.work.place;
    useLayout.getState().open({ surface: "context" }, { paneId: "right" });
    useLayout.getState().close("work");
    expect(paneWithPlace(useLayout.getState().tree, place)).not.toBeNull();
  });

  it("writes through to storage, so a pin outlives the window", () => {
    useLayout.getState().pin("work");
    expect(Object.keys(readPins())).toEqual(["work"]);
  });
});

describe("zoom", () => {
  beforeEach(() => {
    useLayout.setState({
      tree: split("row", [pane([{ surface: "work" }], 0, "a"), pane([{ surface: "context" }], 0, "b")]),
      focused: "a",
      order: ["a", "b"],
      pins: {},
      zoomed: null,
    });
  });

  it("is not stored — a layout that came back zoomed would look like a layout with panes lost", () => {
    // A real commit first, so there is a stored blob to compare against.
    useLayout.getState().activate("a", 0);
    const before = localStorage.getItem(layoutKey);
    expect(before).not.toBeNull();

    useLayout.getState().zoom("a");

    expect(localStorage.getItem(layoutKey), "zooming wrote nothing").toBe(before);
  });

  it("focuses what it zooms, or the next tab opens into a pane you cannot see", () => {
    useLayout.getState().zoom("b");
    expect(useLayout.getState().focused).toBe("b");
  });

  it("clears itself when its pane is closed, rather than rendering nothing", () => {
    useLayout.getState().zoom("b");
    useLayout.getState().closePane("b");
    expect(useLayout.getState().zoomed).toBeNull();
  });

  it("leaves the arrangement untouched, so coming out of it restores exactly", () => {
    const before = useLayout.getState().tree;
    useLayout.getState().zoom("a");
    useLayout.getState().zoom(null);
    expect(useLayout.getState().tree).toBe(before);
  });
});

describe("saved arrangements", () => {
  beforeEach(() => {
    useLayout.setState({
      tree: split(
        "row",
        [
          pane([{ surface: "board" }], 0, "left"),
          pane([{ surface: "chat", conversationId: "c-1" }], 0, "mid"),
          pane([{ surface: "work" }], 0, "right"),
        ],
        [18, 56, 26],
      ),
      focused: "mid",
      order: ["mid"],
      pins: {},
      layouts: [],
      zoomed: null,
    });
  });

  it("keeps the shape and leaves the conversations out", () => {
    useLayout.getState().saveLayout("Writing");

    const saved = readLayouts();
    expect(saved.map((one) => one.name)).toEqual(["Writing"]);
    expect(panes(saved[0].tree).map((one) => one.tabs.map(tabKey))).toEqual([
      ["board"],
      [],
      ["work"],
    ]);
  });

  it("replaces one of the same name rather than keeping two", () => {
    useLayout.getState().saveLayout("Writing");
    useLayout.getState().saveLayout("Writing");
    expect(useLayout.getState().layouts).toHaveLength(1);
  });

  it("ignores a blank name", () => {
    useLayout.getState().saveLayout("   ");
    expect(useLayout.getState().layouts).toEqual([]);
  });

  it("loads with fresh ids, which is what lets one be loaded twice", () => {
    useLayout.getState().saveLayout("Writing");
    const stored = ids(useLayout.getState().layouts[0].tree);

    useLayout.getState().loadLayout("Writing");
    const first = ids(useLayout.getState().tree);
    useLayout.getState().loadLayout("Writing");
    const second = ids(useLayout.getState().tree);

    expect(first).not.toEqual(stored);
    expect(second).not.toEqual(first);
    expect(new Set([...first, ...second]).size).toBe(first.length + second.length);
  });

  it("brings the places with it, so a global pin still has somewhere to land", () => {
    useLayout.getState().pin("work");
    const place = useLayout.getState().pins.work.place;
    useLayout.getState().saveLayout("Writing");

    useLayout.getState().reset();
    expect(paneWithPlace(useLayout.getState().tree, place)).toBeNull();
    useLayout.getState().loadLayout("Writing");

    expect(paneWithPlace(useLayout.getState().tree, place)).not.toBeNull();
  });

  /* Under the section rule. The empty-pane rung lives inside `own` on purpose — `focused` and
   * `grouped` both mean "where you are", and an empty pane somewhere else is not where you are.
   * With the default now `focused`, a blank pane is only refilled when a surface has been told
   * to keep a section of its own. */
  it("fills the blank chat pane on the next open instead of splitting beside it", () => {
    useLayout.setState({ placements: { chat: "own" } });
    useLayout.getState().saveLayout("Writing");
    useLayout.getState().loadLayout("Writing");

    useLayout.getState().open({ surface: "chat", conversationId: "c-2" });

    expect(panes(useLayout.getState().tree).map((one) => one.tabs.map(tabKey))).toEqual([
      ["board"],
      ["chat:c-2"],
      ["work"],
    ]);
  });

  it("deletes one by name and leaves the rest", () => {
    useLayout.getState().saveLayout("Writing");
    useLayout.getState().saveLayout("Reviewing");
    useLayout.getState().deleteLayout("Writing");
    expect(useLayout.getState().layouts.map((one) => one.name)).toEqual(["Reviewing"]);
    expect(readLayouts().map((one) => one.name)).toEqual(["Reviewing"]);
  });

  it("refuses an entry it cannot read, one at a time", () => {
    localStorage.setItem(
      layoutsKey,
      JSON.stringify([
        { name: "Good", tree: pane([{ surface: "work" }], 0, "a") },
        { name: "", tree: pane([], 0, "b") },
        { name: "No tree" },
        { name: "Junk tree", tree: { kind: "banana" } },
        /* Duplicate ids inside a saved entry are the crash `remintIds` exists to prevent, and
         * this is the earlier lock: never offer it for loading in the first place. */
        { name: "Doomed", tree: split("row", [pane([], 0, "same"), pane([], 0, "same")]) },
      ]),
    );
    expect(readLayouts().map((one) => one.name)).toEqual(["Good"]);
  });

  it("answers with nothing at all rather than throwing", () => {
    localStorage.setItem(layoutsKey, "[ not json");
    expect(readLayouts()).toEqual([]);
  });
});

/**
 * Where a surface that is *about a conversation* lands.
 *
 * Routed inside `open` rather than at the call sites, because four of them ask for one — the
 * plugins tab, both ways in from a tool result, and `open_surface` when the model asks — and
 * four copies of a placement policy drift apart.
 */
describe("a bound surface opens into the chat's column", () => {
  beforeEach(() => {
    localStorage.clear();
    useLayout.setState({
      tree: pane([{ surface: "chat", conversationId: "c-1" }], 0, "here"),
      focused: "here",
      companions: {},
      pins: {},
      zoomed: null,
    });
  });

  it("attaches a plugin surface instead of raising a tab", () => {
    useLayout.getState().open({ surface: "plugin", plugin: "flowpad", view: "board" });

    const tabs = panes(useLayout.getState().tree)[0]!.tabs.map(tabKey);
    expect(tabs, "the strip still holds only the chat").toEqual(["chat:c-1"]);

    const held = useLayout.getState().companions["chat:c-1"] ?? [];
    expect(held.map((one) => one.surface)).toEqual(["plugin"]);
  });

  /* Stamped on the way in, so a panel opened from a tool result — which names a plugin and a
   * view and nothing else — still knows which conversation it is showing. */
  it("stamps the conversation onto a surface that arrived without one", () => {
    useLayout.getState().open({ surface: "plugin", plugin: "flowpad", view: "board" });
    expect(useLayout.getState().companions["chat:c-1"]![0]!.conversationId).toBe("c-1");
  });

  it("is idempotent, so asking twice does not stack two", () => {
    useLayout.getState().open({ surface: "work" });
    useLayout.getState().open({ surface: "work" });
    expect(useLayout.getState().companions["chat:c-1"]).toHaveLength(1);
  });

  /* "Work about what?" has no answer with no chat in front of you, and a tab is the honest
   * way to say so. */
  it("falls back to a tab when no chat is focused", () => {
    useLayout.setState({
      tree: pane([{ surface: "board" }], 0, "here"),
      focused: "here",
      companions: {},
    });
    useLayout.getState().open({ surface: "work" });

    expect(panes(useLayout.getState().tree)[0]!.tabs.map(tabKey)).toEqual(["board", "work"]);
    expect(useLayout.getState().companions).toEqual({});
  });

  /* Naming a pane is an instruction, not a preference — the plugin delivery path relies on it. */
  it("lets a caller that names a pane overrule the column", () => {
    useLayout.getState().open({ surface: "work" }, { paneId: "here" });
    expect(panes(useLayout.getState().tree)[0]!.tabs.map(tabKey)).toContain("work@c-1");
    expect(useLayout.getState().companions).toEqual({});
  });

  it("board and settings are about Kith, so they stay tabs", () => {
    useLayout.getState().open({ surface: "settings" });
    expect(panes(useLayout.getState().tree)[0]!.tabs.map(tabKey)).toContain("settings");
    expect(useLayout.getState().companions).toEqual({});
  });

  /* A ref that names its conversation is answering the question, not asking it.
   *
   * `workspace.tsx` computes which chat a surface is about with a *sticky* rule — a click outside
   * a chat deliberately leaves the answer where it was, so the Context button still works while
   * you are in the panel beside the chat. `focusedChat` here had the opposite rule and refused to
   * fall back at all. So the two disagreed exactly when focus was on a companion: the caller said
   * "context for c-1", this read "no chat is focused", and the panel opened as a stray tab
   * somewhere else instead of in the column it was asked for.
   *
   * One policy, and it is the caller's: if the ref says which conversation, that is the host. */
  it("attaches to the conversation the ref names, not the one that happens to be focused", () => {
    useLayout.setState({
      tree: pane([{ surface: "chat", conversationId: "c-1" }, { surface: "board" }], 1, "here"),
      focused: "here",
      companions: {},
    });
    useLayout.getState().open({ surface: "context", conversationId: "c-1" });

    expect(useLayout.getState().companions["chat:c-1"]).toHaveLength(1);
    expect(panes(useLayout.getState().tree)[0]!.tabs.map(tabKey)).toEqual(["chat:c-1", "board"]);
  });

  it("still falls back to a tab when the named conversation is not open anywhere", () => {
    useLayout.setState({
      tree: pane([{ surface: "board" }], 0, "here"),
      focused: "here",
      companions: {},
    });
    useLayout.getState().open({ surface: "context", conversationId: "c-gone" });

    expect(useLayout.getState().companions).toEqual({});
    expect(panes(useLayout.getState().tree)[0]!.tabs.map(tabKey)).toContain("context@c-gone");
  });

  it("detaching the last panel drops the host rather than storing an empty column", () => {
    useLayout.getState().open({ surface: "work" });
    useLayout.getState().detach("chat:c-1", "work@c-1");
    expect(useLayout.getState().companions).toEqual({});
  });
});
