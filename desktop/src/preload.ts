/**
 * The one thing the page is given, and nothing else.
 *
 * There was no preload here for a long time, and the reasoning written into `window/window.ts` was
 * sound: every security-relevant `webPreferences` default is already the safe one, and the renderer
 * only needs `fetch` to its own origin — so a preload would have been attack surface bought for
 * nothing. The "for nothing" is the part that changed. What it buys now is the event stream living
 * in the main process, which is what makes it survive a reload, authenticate properly, and stay out
 * of Chromium's six-connections-per-origin pool. See `server/events.ts`.
 *
 * The surface is one receive-only function. No `invoke`, no `send`, no way for the page to ask the
 * main process for anything — so this widens what the renderer can *hear*, not what it can *do*,
 * and it hears only events it could already have subscribed to itself over HTTP.
 *
 * `contextIsolation` and `sandbox` both stay on. A sandboxed preload still gets `contextBridge` and
 * `ipcRenderer`, which is the entire requirement.
 */

import { contextBridge, ipcRenderer } from "electron";

/** Mirrors `CHANNEL` in `server/events.ts`. The two are the whole contract. */
const CHANNEL = "kith:event";
/** Mirrors `ASK` and `STATUS` in `web/web-view-channel.ts`, which is where the reasoning for a
 *  two-way channel existing at all is written down. */
const WEB_VIEW = "kith:web-view";
const WEB_VIEW_STATUS = "kith:web-view-status";

contextBridge.exposeInMainWorld("kith", {
  /**
   * Listen to the app's event stream. Returns a function that stops listening.
   *
   * The page uses the presence of this function to decide it is in the desktop shell; a browser
   * tab finds nothing here and opens its own `EventSource` instead. One transport in each place,
   * chosen by what is available rather than by a flag someone has to set.
   */
  onEvent(listener: (event: { id: number; type: string; data: unknown }) => void): () => void {
    const forward = (_: unknown, event: { id: number; type: string; data: unknown }) =>
      listener(event);
    ipcRenderer.on(CHANNEL, forward);
    return () => {
      ipcRenderer.off(CHANNEL, forward);
    };
  },

  /**
   * A browser pane: where it goes, and what it is showing.
   *
   * The one thing on this surface that the page can *do* rather than hear, and it exists because
   * a `WebContentsView` is composited by the main process and is invisible to CSS — so only the
   * renderer knows where the pane is and only the main process can put the view there. The verb
   * list is fixed and every call is checked against the installed plugins on the far side; see
   * `web/web-view-channel.ts`.
   *
   * Absent outside the desktop shell, which is how `plugin-web-view.tsx` knows to say that a
   * browser pane needs the app rather than drawing an empty rectangle.
   */
  webView: {
    ask(request: { verb: string; plugin: string; view: string; [key: string]: unknown }) {
      return ipcRenderer.invoke(WEB_VIEW, request) as Promise<Record<string, unknown>>;
    },
    onStatus(listener: (status: Record<string, unknown>) => void): () => void {
      const forward = (_: unknown, status: Record<string, unknown>) => listener(status);
      ipcRenderer.on(WEB_VIEW_STATUS, forward);
      return () => {
        ipcRenderer.off(WEB_VIEW_STATUS, forward);
      };
    },
  },
});
