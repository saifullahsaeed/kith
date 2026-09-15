/**
 * The layout's keyboard, as a table.
 *
 * `commandFor` is pure so that this can be a list of strokes and the commands they are, rather
 * than something verified by pressing keys and watching the window. Two things in here are the
 * reason the file is worth its length: the `code`-not-`key` rule, which is the difference
 * between a shortcut that works on a Mac and one that silently never fires, and the guards,
 * which are the difference between a shortcut and a typo in someone's message.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { commandFor, fromEditable, run, type Stroke } from "./keys";
import { useLayout } from "./store";
import { pane, panes, split, tabKey } from "./tree";

/** A stroke with the primary modifier down, which is what nearly every binding needs. */
const cmd = (code: string, more: Partial<Stroke> = {}): Stroke => ({
  code,
  metaKey: true,
  ...more,
});

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
});

describe("which command a stroke is", () => {
  it("reads the digits as panes, one-based on the keyboard and zero-based in the tree", () => {
    expect(commandFor(cmd("Digit1"))).toEqual({ kind: "focusPane", index: 0 });
    expect(commandFor(cmd("Digit9"))).toEqual({ kind: "focusPane", index: 8 });
  });

  it("walks tabs on alt and throws them on shift, which are different gestures", () => {
    expect(commandFor(cmd("ArrowRight", { altKey: true }))).toEqual({ kind: "walkTab", delta: 1 });
    expect(commandFor(cmd("ArrowLeft", { altKey: true }))).toEqual({ kind: "walkTab", delta: -1 });
    expect(commandFor(cmd("ArrowRight", { shiftKey: true }))).toEqual({
      kind: "throwTab",
      delta: 1,
    });
  });

  it("refuses an arrow with both modifiers rather than guessing which was meant", () => {
    expect(commandFor(cmd("ArrowRight", { altKey: true, shiftKey: true }))).toBeNull();
  });

  it("takes the letters off `code`, because Option+letter is not that letter on a Mac", () => {
    /* This is the whole reason `Stroke` carries `code`. With Option held, macOS puts the
     * composed character in `event.key` — Option+M is "µ" — so a handler comparing `key` never
     * fires on the platform this ships as a desktop app for, with no error to find. */
    expect(commandFor(cmd("KeyM", { altKey: true }))).toEqual({ kind: "zoom" });
    expect(commandFor(cmd("KeyP", { altKey: true }))).toEqual({ kind: "pin" });
    expect(commandFor(cmd("KeyW", { altKey: true }))).toEqual({ kind: "closePane" });
  });

  it("leaves zoom on a bare Escape, and asks for no modifier to do it", () => {
    expect(commandFor({ code: "Escape" })).toEqual({ kind: "unzoom" });
    expect(commandFor(cmd("Escape"))).toBeNull();
  });

  it("ignores everything without the primary modifier", () => {
    expect(commandFor({ code: "Digit1" })).toBeNull();
    expect(commandFor({ code: "KeyM", altKey: true })).toBeNull();
  });

  it("takes Ctrl as the primary too, the way every other shortcut in this app does", () => {
    expect(commandFor({ code: "Digit2", ctrlKey: true })).toEqual({
      kind: "focusPane",
      index: 1,
    });
  });

  it("claims none of the four combos this renderer has already spoken for", () => {
    /* Cmd+F (session-bar), Cmd+S (persona-tab), Cmd+K and Cmd+R (control-panel). A binding
     * added here that shadowed one of those would take a feature away silently, since the
     * handler that wins is whichever `preventDefault`s first. */
    for (const code of ["KeyF", "KeyS", "KeyK", "KeyR"]) {
      expect(commandFor(cmd(code)), code).toBeNull();
      expect(commandFor(cmd(code, { altKey: true })), `${code} with alt`).toBeNull();
    }
  });

  it("claims none of the accelerators the application menu owns", () => {
    /* `hardening.ts` installs the standard menu roles, and a menu accelerator is dispatched in
     * the main process where a renderer keydown cannot cancel it. Binding one here would be a
     * shortcut that appears to do nothing. */
    expect(commandFor(cmd("KeyW")), "Cmd+W is Close Window").toBeNull();
    expect(commandFor(cmd("Digit0")), "Cmd+0 is page zoom").toBeNull();
    expect(commandFor(cmd("KeyM")), "Cmd+M is minimize").toBeNull();
    expect(commandFor(cmd("KeyF", { ctrlKey: true })), "Ctrl+Cmd+F is fullscreen").toBeNull();
  });
});

describe("the guard against typing", () => {
  it("holds for a field, a textarea and anything editable", () => {
    for (const tag of ["input", "textarea", "select"]) {
      expect(fromEditable(document.createElement(tag)), tag).toBe(true);
    }
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    expect(fromEditable(editable)).toBe(true);
  });

  it("holds for a child of an editable region, which is where the composer puts the caret", () => {
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    const inner = document.createElement("span");
    editable.append(inner);
    document.body.append(editable);
    expect(fromEditable(inner)).toBe(true);
    editable.remove();
  });

  it("does not hold for the page at large", () => {
    expect(fromEditable(document.createElement("div"))).toBe(false);
    expect(fromEditable(null)).toBe(false);
  });
});

describe("carrying a command out", () => {
  beforeEach(() => {
    useLayout.setState({
      tree: split("row", [
        pane([{ surface: "work" }, { surface: "board" }], 0, "left"),
        pane([{ surface: "inbox" }], 0, "right"),
      ]),
      focused: "left",
      order: ["left", "right"],
      pins: {},
      zoomed: null,
    });
  });

  it("focuses the nth pane, and reports nothing done when there is no nth", () => {
    expect(run({ kind: "focusPane", index: 1 }, useLayout.getState())).toBe(true);
    expect(useLayout.getState().focused).toBe("right");
    expect(run({ kind: "focusPane", index: 7 }, useLayout.getState())).toBe(false);
  });

  it("walks tabs and stops at the end rather than wrapping", () => {
    expect(run({ kind: "walkTab", delta: 1 }, useLayout.getState())).toBe(true);
    expect(panes(useLayout.getState().tree)[0].active).toBe(1);
    expect(run({ kind: "walkTab", delta: 1 }, useLayout.getState())).toBe(false);
  });

  it("throws the active tab into the pane next door, into its strip and not as a split", () => {
    run({ kind: "throwTab", delta: 1 }, useLayout.getState());
    const tabs = panes(useLayout.getState().tree).map((one) => one.tabs.map(tabKey));
    expect(tabs).toEqual([["board"], ["inbox", "work"]]);
  });

  it("does nothing at the far edge, rather than something arbitrary", () => {
    expect(run({ kind: "throwTab", delta: -1 }, useLayout.getState())).toBe(false);
  });

  it("toggles zoom on the focused pane and leaves it on Escape", () => {
    run({ kind: "zoom" }, useLayout.getState());
    expect(useLayout.getState().zoomed).toBe("left");
    expect(run({ kind: "unzoom" }, useLayout.getState())).toBe(true);
    expect(useLayout.getState().zoomed).toBeNull();
  });

  it("leaves Escape alone when nothing is zoomed, so the find bar still gets it", () => {
    expect(run({ kind: "unzoom" }, useLayout.getState())).toBe(false);
  });

  it("pins and unpins the tab in front of you", () => {
    run({ kind: "pin" }, useLayout.getState());
    expect(Object.keys(useLayout.getState().pins)).toEqual(["work"]);
    run({ kind: "pin" }, useLayout.getState());
    expect(useLayout.getState().pins).toEqual({});
  });

  it("closes the focused pane", () => {
    run({ kind: "closePane" }, useLayout.getState());
    const tree = useLayout.getState().tree;
    expect(tree.kind, "the split collapsed to its surviving child").toBe("pane");
    expect(panes(tree)[0].tabs.map(tabKey)).toEqual(["inbox"]);
  });
});
