import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { surfaceFor } from "./surfaces";
import { layoutKey, layoutVersion, pinsKey, readCompanions, readPins, readStored } from "./store";
import { builtIn, builtInKey, pane, panes, tabKey, type TabRef } from "./tree";

/**
 * The Browser is a core tab now, and it used to be the plugin surface `plugin:browser/page`.
 *
 * Every layout stored before the change holds the old address. Left alone, the pane where the
 * person's browser was would say "this plugin is not installed" — so it is carried across on
 * read, keeping the tab's own `uid` so React sees the same tab.
 */

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

const old: TabRef = { surface: "plugin", plugin: "browser", view: "page", conversationId: "c1", uid: "t9" };

describe("the Browser as a core tab", () => {
  it("is one per conversation", () => {
    expect(tabKey({ surface: "browser", conversationId: "c1" })).toBe("browser@c1");
    expect(tabKey({ surface: "browser", conversationId: "c2" })).toBe("browser@c2");
  });

  it("is always known, with no plugin index to wait for", () => {
    expect(surfaceFor({ surface: "browser", conversationId: "c1" })).toMatchObject({
      title: "Browser",
      standing: "known",
    });
  });
});

describe("a tab stored while the Browser was a plugin", () => {
  it("becomes the core tab, keeping its conversation and its uid", () => {
    expect(builtIn(old)).toEqual({ surface: "browser", conversationId: "c1", uid: "t9" });
  });

  it("leaves every other plugin's tab alone", () => {
    const sketch: TabRef = { surface: "plugin", plugin: "sketchpad", view: "page", conversationId: "c1" };
    expect(builtIn(sketch)).toBe(sketch);
  });

  it("is carried across in a stored layout", () => {
    const tree = pane([{ surface: "chat", conversationId: "c1", uid: "t1" }, old]);
    localStorage.setItem(layoutKey, JSON.stringify({ version: layoutVersion, tree }));

    const back = readStored();

    expect(back).not.toBeNull();
    const tabs = panes(back!).flatMap((one) => one.tabs);
    expect(tabs.map(tabKey)).toEqual(["chat:c1", "browser@c1"]);
    expect(tabs[1]?.uid).toBe("t9");
  });

  it("is carried across in a chat's companions, and in a pin's key", () => {
    localStorage.setItem("kith-companions", JSON.stringify({ "chat:c1": [old] }));
    localStorage.setItem(
      pinsKey,
      JSON.stringify({ "plugin:browser/page@c1": { place: "right", slot: { direction: "row", index: 1, size: 40 } } }),
    );

    expect(readCompanions()["chat:c1"]?.map(tabKey)).toEqual(["browser@c1"]);
    expect(Object.keys(readPins())).toEqual(["browser@c1"]);
    expect(builtInKey("plugin:browser/pages@c1")).toBe("plugin:browser/pages@c1");
  });
});
