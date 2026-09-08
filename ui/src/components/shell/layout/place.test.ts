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

  it("falls through when the pane it named is gone", () => {
    const choice = choosePane(arrangement(), settings, { ...base, paneId: "closed-yesterday" });
    expect(choice).toEqual({ paneId: "sidebar", beside: false });
  });

  it("splits when the surface prefers a pane of its own", () => {
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

  it("groups a chat with its kind, which is the whole point of tabs", () => {
    const choice = choosePane(arrangement(), chat("c-2"), { ...base, focused: "work-pane" });
    expect(choice).toEqual({ paneId: "chats", beside: false });
  });

  it("prefers the grouped pane you are already in", () => {
    const tree = split("row", [pane([chat("c-1")], 0, "one"), pane([chat("c-2")], 0, "two")]);
    const choice = choosePane(tree, chat("c-3"), { ...base, focused: "two" });
    expect(choice).toEqual({ paneId: "two", beside: false });
  });

  it("will not open a wide surface into a pane the yielding rule collapsed", () => {
    /* The chat pane was railed — 36px measured; the sidebar has focus. The old paneFor put the
     * chat into the rail, technically open and practically invisible. The focused pane takes
     * it instead, which you can see and argue with. */
    const choice = choosePane(arrangement(), chat("c-2"), {
      widths: { chats: 36 },
      placements: {},
      focused: "sidebar",
    });
    expect(choice).toEqual({ paneId: "sidebar", beside: false });
  });

  it("keeps a group together when nothing anywhere fits", () => {
    /* Every pane undersized: between two bad answers, "with its kind" is still the one you can
     * find again afterwards. */
    const choice = choosePane(arrangement(), chat("c-2"), {
      widths: { chats: 36, sidebar: 240, "work-pane": 300 },
      placements: {},
    });
    expect(choice).toEqual({ paneId: "chats", beside: false });
  });

  it("gives an unmeasured pane the benefit of the doubt", () => {
    /* Placement runs before the first measurement arrives. Treating an unknown width as zero
     * would route every early open to the fallback — the app misbehaving worst exactly when
     * it is starting up. */
    const choice = choosePane(arrangement(), chat("c-2"), { ...base });
    expect(choice).toEqual({ paneId: "chats", beside: false });
  });

  it("groups a plugin with its own plugin, not with every plugin", () => {
    const tree = split("row", [pane([work], 0, "one"), pane([sketchDraw], 0, "two")]);

    // A second face of the same plugin belongs beside the first.
    const secondFace = choosePane(tree, sketchErase, { ...base, focused: "one" });
    expect(secondFace).toEqual({ paneId: "two", beside: false });

    // A different plugin is a different kind that happens to share a union member.
    const stranger = choosePane(tree, browserTab, { ...base, focused: "one" });
    expect(stranger).toEqual({ paneId: "one", beside: false });
  });

  it("opens into the pane that is there, even an empty one", () => {
    /* An "empty tree" is still a pane — invariant 5 — so the empty pane is a real answer,
     * and the only one: paneId null is beside's answer, not this rung's. */
    const empty = pane([]);
    const choice = choosePane(empty, settings, { ...base });
    expect(choice.paneId, "the empty pane is the only pane").toBe(empty.id);
    expect(choice.beside).toBe(false);
  });
});

describe("the modes themselves", () => {
  it("defaults to grouped, everywhere", () => {
    expect(DEFAULT_PLACEMENT).toBe("grouped");
    expect(modeFor({}, "chat")).toBe("grouped");
    expect(modeFor({ chat: "focused" }, "chat")).toBe("focused");
  });

  it("refuses to trust a mode this build does not know", () => {
    expect(isPlacementMode("beside")).toBe(true);
    expect(isPlacementMode("wherever")).toBe(false);
    expect(isPlacementMode(42)).toBe(false);
    expect(isPlacementMode(undefined)).toBe(false);
  });
});
