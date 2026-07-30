/**
 * Remember where the window was, and put it back there.
 *
 * A Mac app that opens at the same default size every launch feels like a web page
 * in a frame. This is small, but it is most of the difference.
 *
 * The part that is easy to get wrong: saved bounds can become invalid. Restore a
 * window onto a display that has since been unplugged and it opens off-screen,
 * where the user cannot reach it and cannot tell why the app "didn't start". So
 * saved geometry is validated against the displays that exist *now*, and falls
 * back to the default rather than being trusted.
 */

import * as fs from "node:fs";
import * as path from "node:path";

import { app, screen, type BrowserWindow, type Rectangle } from "electron";

import { WINDOW } from "./config";

/** Debounce: resizing fires continuously, and this must not write on every pixel. */
const SAVE_DEBOUNCE_MS = 400;

interface WindowState extends Rectangle {
  maximized: boolean;
}

function stateFile(): string {
  return path.join(app.getPath("userData"), "window-state.json");
}

function read(): WindowState | null {
  try {
    const parsed = JSON.parse(fs.readFileSync(stateFile(), "utf8")) as Partial<WindowState>;
    if (
      typeof parsed.x !== "number" ||
      typeof parsed.y !== "number" ||
      typeof parsed.width !== "number" ||
      typeof parsed.height !== "number"
    ) {
      return null;
    }
    return {
      x: parsed.x,
      y: parsed.y,
      width: parsed.width,
      height: parsed.height,
      maximized: parsed.maximized === true,
    };
  } catch {
    // No file yet on first launch, or it got corrupted — either way, defaults.
    return null;
  }
}

/**
 * Is this rectangle still meaningfully on a screen that exists?
 *
 * Requires a real overlap, not merely a corner: a window one pixel onto a display
 * is unusable, and "technically visible" is not the test worth passing.
 */
function isOnSomeDisplay(bounds: Rectangle): boolean {
  const MIN_VISIBLE = 80;
  return screen.getAllDisplays().some(({ workArea }) => {
    const overlapX = Math.min(bounds.x + bounds.width, workArea.x + workArea.width) - Math.max(bounds.x, workArea.x);
    const overlapY = Math.min(bounds.y + bounds.height, workArea.y + workArea.height) - Math.max(bounds.y, workArea.y);
    return overlapX >= MIN_VISIBLE && overlapY >= MIN_VISIBLE;
  });
}

/**
 * Geometry for a new window: the remembered position if it is still reachable,
 * otherwise the default size with no position (so the OS centres it).
 */
export function restoredBounds(): { bounds: Partial<Rectangle>; maximized: boolean } {
  const saved = read();
  if (!saved) return { bounds: { width: WINDOW.width, height: WINDOW.height }, maximized: false };

  const bounds: Rectangle = {
    x: saved.x,
    y: saved.y,
    width: Math.max(saved.width, WINDOW.minWidth),
    height: Math.max(saved.height, WINDOW.minHeight),
  };
  if (!isOnSomeDisplay(bounds)) {
    console.log("[kith] saved window position is off-screen now — using defaults");
    return { bounds: { width: bounds.width, height: bounds.height }, maximized: saved.maximized };
  }
  return { bounds, maximized: saved.maximized };
}

/** Persist geometry as it changes. Call once per window. */
export function trackWindowState(window: BrowserWindow): void {
  let timer: NodeJS.Timeout | undefined;

  const save = (): void => {
    // getNormalBounds, not getBounds: while maximized, getBounds returns the
    // screen-filling rectangle, which would then be remembered as the restored
    // size and lose the user's actual window forever.
    const normal = window.getNormalBounds();
    const state: WindowState = { ...normal, maximized: window.isMaximized() };
    try {
      fs.mkdirSync(path.dirname(stateFile()), { recursive: true });
      fs.writeFileSync(stateFile(), JSON.stringify(state, null, 2));
    } catch (error) {
      // Losing window geometry is a cosmetic problem; never let it break a close.
      console.warn("[kith] could not save window state:", error);
    }
  };

  const scheduleSave = (): void => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(save, SAVE_DEBOUNCE_MS);
  };

  window.on("resize", scheduleSave);
  window.on("move", scheduleSave);
  // Closing means hiding here, so this is the reliable moment to flush.
  window.on("close", () => {
    if (timer) clearTimeout(timer);
    save();
  });
}
