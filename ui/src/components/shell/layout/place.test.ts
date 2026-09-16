/**
 * Where an open lands — the policy, one rung at a time.
 *
 * The waterfall in place.ts is easy to get 90% right, and the last 10% is the difference
 * between an open you never think about and one you have to undo: a chat into a railed pane,
 * a plugin into a stranger's pane, a preference silently ignored. Each test here is one rung,
 * asserted against a tree whose panes have names, so a failure reads as a sentence.
 */
import { describe, expect, it } from "vitest";

import { choosePane, DEFAULT_PLACEMENT, isPlacementMode, modeFor, type PlacementMode } from "./place";
import { pane, split, type Node, type SurfaceId } from "./tree";

const chat = (id: string) => ({ surface: "chat" as const, conversationId: id });
const work = { surface: "work" as const };
const board = { surface: "board" as const };
const settings = { surface: "settings" as const };
const sketchDraw = { surface: "plugin" as const, plugin: "sketchpad", view: "draw" };
const sketchErase = { surface: "plugin" as const, plugin: "sketchpad", view: "erase" };
const browserTab = { surface: "plugin" as const, plugin: "browser", view: "tab" };

/** sidebar | chats | work — the arrangement the app opens as, with names to assert against. */
function arrangement(): Node {
  return split(
    "row",
    [pane([work], 0, "sidebar"), pane([chat("c-1")], 0, "chats"), pane([board], 0, "work-pane")],
    [18, 56, 26],
  );
}

/* The section rule, asked for by name.
 *
 * These tests are about the waterfall's rungs, and most of them are about the `own` rung. They
 * used to get it from an empty `placements` because `own` was the default; the default is
 * `focused` now — one surface at a time, full width — so the rung under test has to be named or
 * every one of them would be testing the new default instead. What the default *is* has its own
 * test below. */
const SECTION: Partial<Record<SurfaceId, PlacementMode>> = {
  chat: "own",
  work: "own",
  board: "own",
  settings: "own",
  context: "own",
  plugin: "own",
};

const base = { widths: {} as Record<string, number>, placements: SECTION };

describe("the waterfall", () => {
  it("gives a caller that names a pane that pane, while it exists", () => {
    const choice = choosePane(arrangement(), settings, { ...base, paneId: "work-pane" });
    expect(choice).toEqual({ paneId: "work-pane", beside: false });
  });

  it("falls to the policy when the pane it named is gone", () => {
    /* Pinned to the section rule: no settings pane anywhere, so a section of its own. */
    const choice = choosePane(arrangement(), settings, { ...base, paneId: "closed-yesterday" });
    expect(choice).toEqual({ paneId: null, beside: true });
  });

  it("splits every time when told to always take a new pane", () => {
    const choice = choosePane(arrangement(), settings, {
      ...base,
      placements: { settings: "beside" },
    });
    expect(choice).toEqual({ paneId: null, beside: true });
  });

  it("opens where you are when the surface prefers focused, grouping be damned", () => {
    /* A chat already lives in the chats pane; focused wins anyway, because that is what was
     * asked. The preference is the point of the preference. */
    const choice = choosePane(arrangement(), chat("c-2"), {
      ...base,
      focused: "work-pane",
      placements: { chat: "focused" },
    });
    expect(choice).toEqual({ paneId: "work-pane", beside: false });
  });

  it("adds a chat to the list the chat pane already keeps", () => {
    /* The section exists and can show another chat: into its strip, not a second section. */
    const choice = choosePane(arrangement(), chat("c-2"), { ...base, focused: "work-pane" });
    expect(choice).toEqual({ paneId: "chats", beside: false });
  });

  it("prefers the grouped pane you are already in", () => {
    const tree = split("row", [pane([chat("c-1")], 0, "one"), pane([chat("c-2")], 0, "two")]);
    const choice = choosePane(tree, chat("c-3"), { ...base, focused: "two" });
    expect(choice).toEqual({ paneId: "two", beside: false });
  });

  it("raises a section of its own when the kind has no pane", () => {
    /* The default, and the whole point of own: settings has no pane, so it gets one beside
     * where you are — not absorbed into the 240px sidebar you happened to be clicking in,
     * which is how a 420px surface used to open into a column it could not live in. */
    const choice = choosePane(arrangement(), settings, { ...base, focused: "sidebar" });
    expect(choice).toEqual({ paneId: null, beside: true });
  });

  it("raises a new section rather than opening into one the yielding rule collapsed", () => {
    /* The chat pane was railed — 36px measured. paneFor opened the chat into it: technically
     * open, practically invisible. Own declines the unfit pane and raises a section instead,
     * because between "with its kind" and "visible", visible wins. */
    const choice = choosePane(arrangement(), chat("c-2"), {
      widths: { chats: 36, sidebar: 240, "work-pane": 300 },
      placements: SECTION,
      focused: "sidebar",
    });
    expect(choice).toEqual({ paneId: null, beside: true });
  });

  it("keeps a grouped surface with its kind even when nothing anywhere fits", () => {
    /* Grouped is the older habit kept on purpose: between two bad answers, "with its kind" is
     * still the one you can find again afterwards. */
    const choice = choosePane(arrangement(), chat("c-2"), {
      widths: { chats: 36, sidebar: 240, "work-pane": 300 },
      placements: { chat: "grouped" },
    });
    expect(choice).toEqual({ paneId: "chats", beside: false });
  });

  it("gives an unmeasured pane the benefit of the doubt", () => {
    /* Placement runs before the first measurement arrives. Treating an unknown width as zero
     * would raise a new section for every early open — the app misbehaving worst exactly when
     * it is starting up. */
    const choice = choosePane(arrangement(), chat("c-2"), { ...base });
    expect(choice).toEqual({ paneId: "chats", beside: false });
  });

  it("groups a plugin with its own plugin, not with every plugin", () => {
    const tree = split("row", [pane([work], 0, "one"), pane([sketchDraw], 0, "two")]);

    // A second face of the same plugin belongs beside the first.
    const secondFace = choosePane(tree, sketchErase, { ...base, focused: "one" });
    expect(secondFace).toEqual({ paneId: "two", beside: false });

    // A different plugin is a different kind that happens to share a union member — under the
    // section rule it earns a section of its own.
    const stranger = choosePane(tree, browserTab, { ...base, focused: "one" });
    expect(stranger).toEqual({ paneId: null, beside: true });

    // And the older habit keeps a stranger out of another plugin's pane the same way.
    const absorbed = choosePane(tree, browserTab, {
      ...base,
      focused: "one",
      placements: { plugin: "grouped" },
    });
    expect(absorbed).toEqual({ paneId: "one", beside: false });
  });

  it("opens into the pane that is there, even an empty one, when told to group", () => {
    /* An "empty tree" is still a pane — invariant 5. Under the section rule it would raise a
     * pane; grouped absorbs instead, which is what grouped is for. */
    const empty = pane([]);
    const choice = choosePane(empty, settings, {
      widths: {},
      placements: { settings: "grouped" },
    });
    expect(choice.paneId, "the empty pane is the only pane").toBe(empty.id);
    expect(choice.beside).toBe(false);
  });
});

describe("the modes themselves", () => {
  /* The default moved from `own` to `focused` when the window stopped opening as three
   * standing columns. Under `own`, opening Work raised a column of its own and cut the thread
   * down to make room for it; `focused` opens it as a tab in the pane you are already in, and
   * splitting stays the deliberate gesture it always was — drag a tab to a pane's edge. */
  it("defaults to focused — open where you are, split when you ask", () => {
    expect(DEFAULT_PLACEMENT).toBe("focused");
    expect(modeFor({}, "chat")).toBe("focused");
    expect(modeFor({ chat: "grouped" }, "chat")).toBe("grouped");
  });

  it("puts a surface in the pane you are looking at, without splitting it", () => {
    const choice = choosePane(arrangement(), settings, {
      widths: {},
      placements: {},
      focused: "chats",
    });
    expect(choice).toEqual({ paneId: "chats", beside: false });
  });

  it("refuses to trust a mode this build does not know", () => {
    expect(isPlacementMode("own")).toBe(true);
    expect(isPlacementMode("beside")).toBe(true);
    expect(isPlacementMode("wherever")).toBe(false);
    expect(isPlacementMode(42)).toBe(false);
    expect(isPlacementMode(undefined)).toBe(false);
  });
});

/** The arrangement again, with the right-hand pane carrying a place. */
function placed(): Node {
  return split(
    "row",
    [
      pane([work], 0, "sidebar"),
      pane([chat("c-1")], 0, "chats"),
      pane([board], 0, "work-pane", "place-side"),
    ],
    [18, 56, 26],
  );
}

const side = { direction: "row" as const, index: 2, size: 26 };

describe("a pin", () => {
  it("goes to the pane carrying its place, whatever the surface preference says", () => {
    /* `settings` at the default `own` would raise a pane of its own; pinned, it goes home. That
     * is the point of pins: the per-surface preference could not express "this one, there". */
    const choice = choosePane(placed(), settings, {
      ...base,
      pins: { settings: { place: "place-side", slot: side } },
    });
    expect(choice).toEqual({ paneId: "work-pane", beside: false });
  });

  it("rebuilds the place at the slot it recorded, when no pane carries it any more", () => {
    const choice = choosePane(arrangement(), settings, {
      ...base,
      pins: { settings: { place: "place-side", slot: side } },
    });
    expect(choice).toEqual({
      paneId: null,
      beside: false,
      raise: { slot: side, place: "place-side" },
    });
  });

  it("is honoured into a pane too narrow to show it, because a pin is not a heuristic", () => {
    /* A railed pane measures 36px, which is what the `fits` check exists to keep a 560px chat
     * out of. A pin says otherwise, and a rail is one click from coming back. */
    const choice = choosePane(placed(), chat("c-2"), {
      ...base,
      widths: { "work-pane": 36 },
      pins: { "chat:c-2": { place: "place-side", slot: side } },
    });
    expect(choice).toEqual({ paneId: "work-pane", beside: false });
  });

  it("loses to a caller that names a live pane, which knows things no policy does", () => {
    const choice = choosePane(placed(), settings, {
      ...base,
      paneId: "chats",
      pins: { settings: { place: "place-side", slot: side } },
    });
    expect(choice).toEqual({ paneId: "chats", beside: false });
  });

  it("is about one tab: a pin on one chat does not move another", () => {
    const choice = choosePane(placed(), chat("c-2"), {
      ...base,
      focused: "sidebar",
      pins: { "chat:c-1": { place: "place-side", slot: side } },
    });
    expect(choice, "grouped with the chats, as it always was").toEqual({
      paneId: "chats",
      beside: false,
    });
  });
});

describe("an empty pane", () => {
  /** sidebar | (empty) — what a saved layout deliberately comes back as. */
  function withBlank(): Node {
    return split("row", [pane([work], 0, "sidebar"), pane([], 0, "blank")], [30, 70]);
  }

  it("is filled before a new one is raised", () => {
    /* Without this rung the kind has no pane, so `own` splits a *third* column beside the
     * blank second one — the pane that was asking to be filled stays empty. */
    const choice = choosePane(withBlank(), chat("c-1"), { ...base, focused: "sidebar" });
    expect(choice).toEqual({ paneId: "blank", beside: false });
  });

  it("is skipped when it is too narrow for what is opening", () => {
    const choice = choosePane(withBlank(), chat("c-1"), {
      ...base,
      focused: "sidebar",
      widths: { blank: 200 },
    });
    expect(choice).toEqual({ paneId: null, beside: true });
  });

  it("does not outrank where you are when the surface is grouped", () => {
    /* `grouped` means "absorb into the room you are in". An empty pane somewhere else is not
     * the room you are in, so the older habit wins — which is what the mode was chosen for. */
    const choice = choosePane(withBlank(), settings, {
      ...base,
      focused: "sidebar",
      placements: { settings: "grouped" },
    });
    expect(choice).toEqual({ paneId: "sidebar", beside: false });
  });
});
