/**
 * The menu-bar presence.
 *
 * Closing the window does not stop Kith — the agent runs in the backend process,
 * which is separate. The tray exists so that is *visible*: something in the menu
 * bar means he is still there when no window is open.
 */

import { Menu, Tray, app, nativeImage } from "electron";

import { TRAY_GUID, TRAY_ICON } from "./config";
import { showMainWindow } from "./window";

/**
 * Module scope, not a local. A Tray assigned to a local variable gets garbage
 * collected and the icon silently vanishes from the menu bar.
 */
let tray: Tray | null = null;

export function createTray(onQuit: () => void): Tray | null {
  const icon = nativeImage.createFromPath(TRAY_ICON);
  if (icon.isEmpty()) {
    // Not fatal — the app is perfectly usable without a menu-bar icon, and a
    // missing asset should not stop it launching.
    console.warn(`[kith] tray icon missing or unreadable: ${TRAY_ICON}`);
    return null;
  }
  // Belt and braces: the filename already ends in Template, but marking it
  // explicitly keeps the light/dark inversion working if the file is ever renamed.
  icon.setTemplateImage(true);

  tray = process.platform === "darwin" ? new Tray(icon, TRAY_GUID) : new Tray(icon);
  tray.setToolTip("Kith");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Open Kith", click: () => showMainWindow() },
      { type: "separator" },
      {
        // Says "Quit Kith" rather than "Quit": the window closing is not quitting,
        // and this is the control that actually ends the app.
        label: "Quit Kith",
        accelerator: "Command+Q",
        click: () => {
          onQuit();
          app.quit();
        },
      },
    ]),
  );

  // Note: once a context menu is set, macOS stops delivering click events, so
  // left-clicking opens the menu rather than the window. That is deliberate —
  // one predictable interaction beats a hidden one.
  return tray;
}

export function destroyTray(): void {
  tray?.destroy();
  tray = null;
}
