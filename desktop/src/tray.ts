/**
 * The menu-bar presence.
 *
 * Closing the window does not stop Kith — the agent runs in the backend process,
 * which is separate. The tray exists so that is *visible*: something in the menu
 * bar means he is still there when no window is open.
 */

import { Menu, Tray, app, nativeImage, shell } from "electron";

import { BACKEND_ORIGIN, TRAY_GUID, TRAY_ICON } from "./config";
import { showMainWindow } from "./window";

/**
 * Module scope, not a local. A Tray assigned to a local variable gets garbage
 * collected and the icon silently vanishes from the menu bar.
 */
let tray: Tray | null = null;

/**
 * The menu-bar menu, rebuilt from live state each time it is opened.
 *
 * Built on demand rather than once at startup because half of it is a readout: whether he
 * is roaming, what he is doing right now, and whether he is waiting on a permission. A
 * menu assembled at launch would say "idle" forever.
 *
 * The point of it is the times the window is closed. He keeps working when it is — that is
 * the whole premise — so the menu bar is the only place that can say so, and the only place
 * you can stop him from without bringing the window back.
 */
async function rebuildMenu(onQuit: () => void): Promise<void> {
  if (!tray || tray.isDestroyed()) return;
  const state = await readState();

  tray.setToolTip(state.doing ? `Kith — ${state.doing}` : "Kith");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: state.doing ? `${state.roaming ? "Roaming" : "Here"} — ${state.doing}` : "Kith",
        enabled: false,
      },
      ...(state.pending
        ? [
            {
              // Named with the count because this is the one thing in here that is waiting
              // on a person, and a tick can raise it with the window shut.
              label: `${state.pending} waiting for permission…`,
              click: () => showMainWindow(),
            } as const,
          ]
        : []),
      { type: "separator" as const },
      { label: "Open Kith", accelerator: "Command+O", click: () => showMainWindow() },
      {
        label: state.roaming ? "Stop roaming" : "Let it roam",
        click: () => void control(state.roaming ? "stop" : "start"),
      },
      { label: "Take a step now", enabled: !state.roaming, click: () => void control("tick") },
      { type: "separator" as const },
      { label: "Open his folder", click: () => void openWorkspace() },
      { type: "separator" as const },
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
}

/** What he is up to, for the menu. Failure is silent: a menu is not worth an error. */
async function readState(): Promise<{ roaming: boolean; doing: string; pending: number }> {
  try {
    const [status, permissions] = (await Promise.all([
      fetch(`${BACKEND_ORIGIN}/api/autonomy`).then((r) => r.json()),
      fetch(`${BACKEND_ORIGIN}/api/permissions`).then((r) => r.json()),
    ])) as [{ running?: boolean; current?: string }, { pending?: unknown[] }];
    return {
      roaming: Boolean(status?.running),
      doing: String(status?.current ?? ""),
      pending: Array.isArray(permissions?.pending) ? permissions.pending.length : 0,
    };
  } catch {
    return { roaming: false, doing: "", pending: 0 };
  }
}

async function control(action: "start" | "stop" | "tick"): Promise<void> {
  try {
    await fetch(`${BACKEND_ORIGIN}/api/autonomy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  } catch {
    /* the window will show the real state when it opens */
  }
}

async function openWorkspace(): Promise<void> {
  try {
    const status = (await fetch(`${BACKEND_ORIGIN}/api/permissions`).then((r) => r.json())) as {
      workspace?: { root?: string };
    };
    const root = String(status.workspace?.root ?? "");
    if (root) await shell.openPath(root);
  } catch {
    /* nothing to open */
  }
}

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
  rebuildMenu(onQuit);

  // Rebuilt as it opens, so "Roaming — reading a web page" is true when you read it.
  tray.on("right-click", () => void rebuildMenu(onQuit));
  tray.on("click", () => void rebuildMenu(onQuit));

  // Note: once a context menu is set, macOS stops delivering click events, so
  // left-clicking opens the menu rather than the window. That is deliberate —
  // one predictable interaction beats a hidden one.
  return tray;
}

export function destroyTray(): void {
  tray?.destroy();
  tray = null;
}
