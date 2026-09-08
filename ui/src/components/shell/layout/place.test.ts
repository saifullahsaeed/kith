/**
 * Where an open lands — the policy, one rung at a time.
 *
 * The waterfall in place.ts is easy to get 90% right, and the last 10% is the difference
 * between an open you never think about and one you have to undo: a chat into a railed pane,
 * a plugin into a stranger's pane, a preference silently ignored. Each test here is one rung,
 * asserted against a tree whose panes have names, so a failure reads as a sentence.
 */
import { describe, expect, it } from "vitest";

import { choosePane, DEFAULT_PLACEMENT, isPlacementMode, modeFor } from "./place";
import { pane, split, type Node } from "./tree";

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

const base = { widths: {} as Record<string, number>, placements: {} };

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
      placements: {},
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
  it("defaults to own — the section rule — everywhere", () => {
    expect(DEFAULT_PLACEMENT).toBe("own");
    expect(modeFor({}, "chat")).toBe("own");
    expect(modeFor({ chat: "grouped" }, "chat")).toBe("grouped");
  });

  it("refuses to trust a mode this build does not know", () => {
    expect(isPlacementMode("own")).toBe(true);
    expect(isPlacementMode("beside")).toBe(true);
    expect(isPlacementMode("wherever")).toBe(false);
    expect(isPlacementMode(42)).toBe(false);
    expect(isPlacementMode(undefined)).toBe(false);
  });
});
