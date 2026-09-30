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
 * * **A fixed verb list.** Place, hide, and the things a person's own hand does to a browser —
 *   its tabs among them, and `resize`, because putting the page back to the full pane is exactly that: the
 *   one-click escape hatch from a viewport the model set. There is no generic `invoke`, nothing
 *   that names a file, and nothing that runs script in the page — the model's side of that goes
 *   over loopback with a token, where the Python server has already decided the caller is
 *   allowed.
 * * **Two verbs that exist only here.** `pick` and `cookie-import` have no `/browse` case and
 *   are not acts of the `browser` tool, so the model cannot start either. That is not tidiness: both are
 *   defined by a person doing something — pointing at a component, naming a site they are
 *   already signed into — and a version of them the model could call would be a different
 *   feature with a different argument behind it. See `cookie-import.ts` for the long form.
 * * **One browser, keyed by conversation.** The renderer names only which conversation's view it
 *   is placing. There is nothing else to name — the browser is built in, one partition, and a
 *   conversation id is a key rather than a permission (see below).
 * * **The sealed frames cannot reach it.** A plugin surface is a sandboxed iframe with an opaque
 *   origin, so `window.kith` in the top document is not something a plugin's page can touch.
 *   Only Kith's own trusted renderer code is on this channel.
 */

import { ipcMain } from "electron";

import { getMainWindow } from "../window/window";

import * as cookies from "./cookie-import";
import * as views from "./web-views";

/** Renderer → main. One channel, a verb inside. */
export const ASK = "kith:web-view";
/** Main → renderer. Status for the chrome to draw. */
export const STATUS = "kith:web-view-status";

export function installWebViewChannel(): void {
  ipcMain.handle(ASK, async (_event, raw: unknown) => {
    const request = raw as { verb?: unknown; owner?: unknown } | null;
    const verb = String(request?.verb ?? "");
    /* Which conversation's browser. Not checked against anything, and it does not need to be:
     * it is a *key*, not a permission. Cookies live in the one partition regardless of owner, so
     * naming somebody else's conversation reaches a page in the same profile the caller could
     * have opened itself. What the owner buys is separation for parallel work, not a boundary. */
    const at = { owner: String(request?.owner ?? "") };

    try {
      switch (verb) {
        case "place": {
          const rect = (raw as { rect?: views.Rect }).rect;
          if (!rect) return { error: "place needs a rectangle" };
          return views.place(at, rect);
        }
        case "hide":
          views.hide(at);
          return { ok: true };
        // The picture the pane keeps under the view, so hiding it never looks like the page
        // going away. Asked for when the status says there is a newer one — see `views.frame`.
        case "frame":
          return views.frame(at) ?? { seq: 0 };
        case "navigate":
          return await views.navigate(at, String((raw as { url?: unknown }).url ?? ""));
        // The strip: a person opening, switching and closing tabs with their own hand.
        case "tab-new":
          return await views.newTab(at, String((raw as { url?: unknown }).url ?? ""), {
            private: (raw as { private?: unknown }).private === true,
          });
        case "tab-switch":
          return views.switchTab(at, String((raw as { tab?: unknown }).tab ?? ""));
        case "tab-close":
          return views.closeTab(at, String((raw as { tab?: unknown }).tab ?? ""));
        case "back":
          return await views.back(at);
        case "forward":
          return await views.forward(at);
        case "reload":
          return await views.reload(at);
        case "resize": {
          const asked = raw as {
            preset?: unknown;
            width?: unknown;
            height?: unknown;
            ratio?: unknown;
          };
          return await views.resize(at, {
            preset: typeof asked.preset === "string" ? asked.preset : undefined,
            width: typeof asked.width === "number" ? asked.width : undefined,
            height: typeof asked.height === "number" ? asked.height : undefined,
            ratio: typeof asked.ratio === "string" ? asked.ratio : undefined,
          });
        }
        /* The inspector. Person-only — see the note at the top of this file — and it answers
         * once the person has clicked something or given up, which is why the renderer treats
         * it as a long call and not as a round trip. */
        case "pick": {
          const asked = raw as { pad?: unknown };
          return await views.pick(at, typeof asked.pad === "number" ? asked.pad : undefined);
        }
        case "unpick":
          await views.unpick(at);
          return { ok: true };
        case "cookie-sources":
          return { sources: cookies.sources() };
        // What that browser is holding a login for. Read-only, no keychain — host names are not
        // encrypted in either format — and on the same chrome-only channel as the import it
        // precedes, so neither the model nor a plugin can ask it what you are signed into.
        case "cookie-domains": {
          const asked = raw as { source?: unknown; profile?: unknown };
          return await cookies.domainsIn(
            String(asked.source ?? ""),
            asked.profile ? String(asked.profile) : undefined,
          );
        }
        case "cookie-import": {
          const asked = raw as { source?: unknown; domain?: unknown; profile?: unknown };
          return await cookies.importCookies(
            String(asked.source ?? ""),
            String(asked.domain ?? ""),
            asked.profile ? String(asked.profile) : undefined,
          );
        }
        case "status":
          return views.status(at) ?? { error: "not open" };
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
