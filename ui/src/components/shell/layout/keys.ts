import { useEffect } from "react";

import { useLayout, type LayoutState } from "./store";
import { paneBeside, panes, tabKey } from "./tree";

/**
 * The layout's keyboard.
 *
 * Until this file the layout had **no** shortcuts at all — every one of focus a pane, switch a
 * tab, move a tab, close a pane was drag, click or a context menu. Fifteen-odd components in
 * this app each attach their own `window` keydown listener and not one of them is about the
 * arrangement.
 *
 * One listener rather than a sixteenth ad-hoc one, and the *decision* pulled out of it as a
 * pure function so the binding table is something a test asserts rather than something you
 * verify by pressing keys and watching.
 *
 * **`code`, not `key`, for every letter and digit.** With Option held, macOS gives
 * `event.key` the composed character — Option+M is `"µ"`, Option+P is `"π"`, Option+W is
 * `"∑"` — so a handler comparing `key.toLowerCase() === "m"` never fires, silently, on the one
 * platform this app ships as a desktop build for. `code` names the physical key and is
 * unaffected by modifiers or layout.
 *
 * **What is already taken, enumerated rather than assumed.** In this renderer: `Cmd+F`
 * (`session-bar.tsx`), `Cmd+S` (`persona-tab.tsx`), `Cmd+K` and `Cmd+R`
 * (`control-panel/index.tsx`). In the main process, `hardening.ts` installs an application menu
 * of standard roles, which owns `Cmd+W` (Close Window), `Cmd+0` and `Cmd+±` (page zoom),
 * `Cmd+R` (reload — so that one is already double-bound), `Ctrl+Cmd+F` (fullscreen) and
 * `Cmd+M` (minimize). Menu accelerators are dispatched in the main process and a renderer
 * `keydown` cannot cancel them, so none of those are available here whatever this file does.
 * That is why closing a *tab* is not `Cmd+W`: taking it needs a File-menu item and IPC, which
 * is a change to the desktop shell and belongs in its own piece of work.
 */

export type Command =
  | { kind: "focusPane"; index: number }
  | { kind: "walkTab"; delta: -1 | 1 }
  | { kind: "throwTab"; delta: -1 | 1 }
  | { kind: "zoom" }
  | { kind: "unzoom" }
  | { kind: "pin" }
  | { kind: "closePane" };

/** The part of a keyboard event this decision is about. */
export type Stroke = {
  /** The physical key — `"KeyM"`, `"Digit3"`, `"ArrowLeft"`, `"Escape"`. */
  code: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
};

/** Which command a stroke is, or none.
 *
 * | Binding | Command |
 * |---|---|
 * | `Cmd+1` … `Cmd+9` | focus the nth pane in render order |
 * | `Cmd+Alt+←` / `→` | previous / next tab in the focused pane |
 * | `Cmd+Shift+←` / `→` | throw the active tab into the neighbouring pane |
 * | `Cmd+Alt+M` | zoom the focused pane, or leave zoom |
 * | `Esc` | leave zoom |
 * | `Cmd+Alt+P` | pin / unpin the active tab |
 * | `Cmd+Alt+W` | close the focused pane |
 *
 * `Cmd` is `metaKey || ctrlKey`, which is the convention every other shortcut in this app
 * already uses — so the table reads as Ctrl on a machine without a Command key without a second
 * table to maintain.
 */
export function commandFor(stroke: Stroke): Command | null {
  // Escape carries no modifier and belongs to whatever is on screen; the caller only acts on it
  // when something is actually zoomed, so it stays available to everything else.
  if (stroke.code === "Escape" && !stroke.metaKey && !stroke.ctrlKey && !stroke.altKey) {
    return { kind: "unzoom" };
  }

  const primary = !!(stroke.metaKey || stroke.ctrlKey);
  if (!primary) return null;

  const digit = /^Digit([1-9])$/.exec(stroke.code);
  if (digit && !stroke.altKey && !stroke.shiftKey) {
    return { kind: "focusPane", index: Number(digit[1]) - 1 };
  }

  const arrow = stroke.code === "ArrowLeft" ? -1 : stroke.code === "ArrowRight" ? 1 : 0;
  if (arrow !== 0) {
    if (stroke.altKey && !stroke.shiftKey) return { kind: "walkTab", delta: arrow };
    if (stroke.shiftKey && !stroke.altKey) return { kind: "throwTab", delta: arrow };
    return null;
  }

  if (!stroke.altKey || stroke.shiftKey) return null;
  if (stroke.code === "KeyM") return { kind: "zoom" };
  if (stroke.code === "KeyP") return { kind: "pin" };
  if (stroke.code === "KeyW") return { kind: "closePane" };
  return null;
}

/** Is the person typing?
 *
 * A digit that re-focuses a pane while a message is half-written is worse than a shortcut you
 * have to click out of first, so the guard is uniform rather than per-command. `closest` as
 * well as the element itself, because the composer's editable region has children. */
export function fromEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return !!target.closest('input, textarea, select, [contenteditable="true"]');
}

/** Carry out a command. Returns whether anything happened, which is what decides whether the
 *  event is consumed — an `Escape` with nothing zoomed has to stay available to the dialog,
 *  the find bar and the menu that were listening for it before this file existed. */
export function run(command: Command, state: LayoutState): boolean {
  const all = panes(state.tree);
  const here = all.find((one) => one.id === state.focused) ?? all[0];

  switch (command.kind) {
    case "focusPane": {
      const target = all[command.index];
      if (!target || target.id === state.focused) return false;
      state.focus(target.id);
      return true;
    }
    case "walkTab": {
      if (!here || here.tabs.length < 2) return false;
      const to = here.active + command.delta;
      if (to < 0 || to >= here.tabs.length) return false;
      state.activate(here.id, to);
      return true;
    }
    case "throwTab": {
      const moving = here?.tabs[here.active];
      if (!here || !moving) return false;
      const target = paneBeside(state.tree, here.id, command.delta);
      if (!target) return false;
      // "center" is a drop into the target's strip, which is what a throw is — the edges are
      // for splitting, and a shortcut that silently restructured the layout would be a bad one.
      state.dock(tabKey(moving), target.id, "center");
      return true;
    }
    case "zoom": {
      if (!here) return false;
      state.zoom(here.id);
      return true;
    }
    case "unzoom": {
      if (!state.zoomed) return false;
      state.zoom(null);
      return true;
    }
    case "pin": {
      const target = here?.tabs[here.active];
      if (!target) return false;
      const key = tabKey(target);
      if (key in state.pins) state.unpin(key);
      else state.pin(key);
      return true;
    }
    case "closePane": {
      if (!here) return false;
      state.closePane(here.id);
      return true;
    }
  }
}

/** Install the layout's keyboard for as long as the layout is mounted. */
export function useLayoutKeys(): void {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Something nearer the person already answered this. Checked first, so a shortcut here
      // can never take a key out from under the composer or a modal.
      if (event.defaultPrevented) return;
      if (fromEditable(event.target)) return;
      const command = commandFor(event);
      if (!command) return;
      // Read through `getState` rather than a subscription: the handler is installed once and
      // must see the layout as it is when the key is pressed, not as it was on mount.
      if (!run(command, useLayout.getState())) return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
