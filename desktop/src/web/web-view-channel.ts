/**
 * The renderer's side of a browser pane: where to put it, and what it is showing.
 *
 * ## Why the preload grows a two-way channel here
 *
 * `preload.ts` says its surface is one receive-only function, and gives the reason: a preload
 * would otherwise be attack surface bought for nothing, and what it buys now — an event stream
 * in the main process — was the first thing worth paying for. This is the second.
 *
 * A `WebContentsView` is composited by the main process and does not participate in the page's
 * layout, so *only* the renderer knows where the pane is and *only* the main process can move
 * the view there. That is not a preference about architecture, it is the shape of the API. No
 * amount of CSS reaches a child view, and no amount of main-process cleverness knows where a
 * flexbox put a pane.
 *
 * What keeps this narrow:
 *
 * * **A fixed verb list.** Place, hide, and the four things a person's own hand does to a
 *   browser. There is no generic `invoke`, nothing that names a file, and nothing that runs
 *   script in the page — the model's side of that goes over loopback with a token, where the
 *   Python server has already decided the caller is allowed.
 * * **A plugin id checked against what is installed.** The renderer says which plugin's view it
 *   is placing; the main process refuses anything that is not a `web` surface the person
 *   actually installed, so a compromised renderer cannot conjure a browser out of a name.
 * * **The sealed frames cannot reach it.** A plugin surface is a sandboxed iframe with an opaque
 *   origin, so `window.kith` in the top document is not something a plugin's page can touch.
 *   Only Kith's own trusted renderer code is on this channel.
 */

import { ipcMain } from "electron";

import { BACKEND_ORIGIN } from "../config";
import { apiHeaders } from "../server/api-token";

import { getMainWindow } from "../window/window";

import * as views from "./web-views";

/** Renderer → main. One channel, a verb inside. */
export const ASK = "kith:web-view";
/** Main → renderer. Status for the chrome to draw. */
export const STATUS = "kith:web-view-status";

/** Which browser panes are real, as `<plugin>/<view>`. Empty until asked for, which is the
 *  right default: an unknown key is a bug or an attempt, and a browser that appears because
 *  somebody typed a name is neither. */
let allowed: Set<string> = new Set();

/**
 * Ask the backend which browser panes exist, and remember the answer.
 *
 * Lazily, and only on a miss. The alternative was for the shell to refresh this at startup and
 * on every plugin change, which means a second thing to keep in step with installs — and a
 * stale list there is a browser pane that will not open with nothing on screen saying why.
 * Asking when an unknown key turns up is self-healing: install a plugin, open its tab, and the
 * first `place` teaches the shell about it.
 */
async function refreshPermitted(): Promise<void> {
  try {
    const response = await fetch(`${BACKEND_ORIGIN}/api/plugins/surfaces`, {
      headers: apiHeaders(),
    });
    if (!response.ok) return;
    const body = (await response.json()) as {
      surfaces?: { plugin?: string; view?: string; kind?: string }[];
    };
    allowed = new Set(
      (body.surfaces ?? [])
        .filter((one) => one.kind === "web")
        .map((one) => `${one.plugin}/${one.view}`),
    );
  } catch {
    /* The backend is down; the pane will say so on its own. */
  }
}

export function installWebViewChannel(): void {
  ipcMain.handle(ASK, async (_event, raw: unknown) => {
    const request = raw as { verb?: unknown; plugin?: unknown; view?: unknown } | null;
    const verb = String(request?.verb ?? "");
    const plugin = String(request?.plugin ?? "");
    const view = String(request?.view ?? "");
    if (!allowed.has(`${plugin}/${view}`)) await refreshPermitted();
    if (!allowed.has(`${plugin}/${view}`)) {
      return { error: `${plugin}/${view} is not an installed browser pane` };
    }

    try {
      switch (verb) {
        case "place": {
          const rect = (raw as { rect?: views.Rect }).rect;
          const home = String((raw as { home?: unknown }).home ?? "");
          if (!rect) return { error: "place needs a rectangle" };
          return views.place(plugin, view, rect, home);
        }
        case "hide":
          views.hide(plugin, view);
          return { ok: true };
        case "navigate":
          return await views.navigate(plugin, view, String((raw as { url?: unknown }).url ?? ""));
        case "back":
          return await views.back(plugin, view);
        case "forward":
          return await views.forward(plugin, view);
        case "reload":
          return await views.reload(plugin, view);
        case "status":
          return views.status(plugin, view) ?? { error: "not open" };
        default:
          return { error: `no such verb ${verb}` };
      }
    } catch (error) {
      return { error: (error as Error).message };
    }
  });

  // Push every status change at the window. The chrome is drawn from these — the address bar's
  // contents, the back button's enabled state, the spinner — so a missed one is a pane that
  // says it is still loading a page that arrived a minute ago.
  views.onViewStatus((status) => {
    const window = getMainWindow();
    if (window && !window.isDestroyed()) window.webContents.send(STATUS, status);
  });
}
