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
});
