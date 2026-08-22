/**
 * The main window: creation, close behaviour, and where links are allowed to go.
 *
 * There is a preload now, and there was not for a long time. The old reasoning — every
 * security-relevant `webPreferences` default is already the safe one, and the renderer only ever
 * needs `fetch` to its own origin — was right about the defaults and wrong about the gain. What it
 * buys is the event stream held by the main process rather than by the page: one connection that
 * survives a reload, sends the API token like every other call, and stays out of Chromium's
 * six-connections-per-origin pool. See `preload.ts` and `server/events.ts`.
 *
 * The surface is one receive-only function. Nothing here lets the page *ask* the shell for
 * anything, and `contextIsolation` and `sandbox` both stay on.
 */

import { BrowserWindow, shell } from "electron";

import { loadWhenReady, recoverFromBackendRestarts } from "../server/backend";
import { BACKEND_ORIGIN, EXTERNAL_SCHEMES, PRELOAD, WINDOW } from "../config";
import { restoredBounds, trackWindowState } from "./window-state";

let mainWindow_: BrowserWindow | null = null;

/** True once the app is genuinely quitting, so close stops meaning "hide". */
let quitting = false;

export function markQuitting(): void {
  quitting = true;
}

export function getMainWindow(): BrowserWindow | null {
  return mainWindow_;
}

export function createMainWindow(): BrowserWindow {
  const { bounds, maximized } = restoredBounds();
  const window = new BrowserWindow({
    ...bounds,
    minWidth: WINDOW.minWidth,
    minHeight: WINDOW.minHeight,
    title: "Kith",
    // Matches the dark --background in web/src/index.css. The UI's pre-paint theme
    // script defaults to dark, so Electron's white default would flash on every
    // launch before the first frame.
    backgroundColor: "#110e0b",
    // Don't show an empty frame while the backend is still being waited on.
    show: false,
    titleBarStyle: "hiddenInset",
    // Centred against the header, not against a title bar that isn't there.
    //
    // `hiddenInset` puts the lights where a standard toolbar would want them, which is a few
    // pixels above the middle of ours: the header is 10px of padding, a 32px control row and
    // 10px again, so its centre is at 26 and a 12px light wants its top at 20. Left alone they
    // sat high enough to read as misaligned with the icons beside them — which on a window with
    // no title bar is the only vertical rhythm there is.
    //
    // `x` is the inset Electron already used. If it moves, `--window-controls-inset` in
    // `index.css` has to move with it: that variable is what stops the header's first button
    // being drawn underneath them.
    trafficLightPosition: { x: 13, y: 20 },
    webPreferences: {
      // One receive-only channel for the app's event stream, and nothing else on it.
      preload: PRELOAD,
      // Stated rather than assumed. These ARE the defaults; writing them down
      // means a future edit has to disagree in public rather than by omission.
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInSubFrames: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      // `plugins` is deliberately left at its default of false. The file viewer shows a
      // PDF in Chromium's built-in reader, which older Electron did gate behind this
      // flag — it no longer does, and that was checked here rather than assumed:
      // a PDF renders complete with toolbar and page thumbnails with plugins off.
      // Turning it on to be safe would widen what the renderer can load for nothing.
      // Required, not a tuning knob. Closing the window HIDES it, and Chromium throttles
      // timers and network in hidden windows.
      //
      // This was here for the activity feed's `EventSource`, which is no longer in the page —
      // the shell holds that stream and pushes to hidden windows deliberately. What still needs
      // it is the turn itself: `POST /api/chat` is a fetch belonging to the document, and a turn
      // running while the app sits in the tray is the ordinary case rather than the exception.
      backgroundThrottling: false,
    },
  });

  if (maximized) window.maximize();
  hardenNavigation(window);
  trackWindowState(window);

  window.on("close", (event) => {
    // Hide instead of closing, so the window comes back instantly with its React
    // state and its live activity stream still connected.
    //
    // The guard matters: an unconditional preventDefault() here makes the app
    // impossible to quit, because app.quit() closes windows first and would be
    // cancelled every time.
    if (!quitting) {
      event.preventDefault();
      window.hide();
    }
  });

  window.on("closed", () => {
    mainWindow_ = null;
  });

  /* Tell the page when the traffic lights are not there.
   *
   * Full screen hides them, and the header goes on reserving the 78px of space they occupied —
   * so the first control sits a thumb's width in from the edge with nothing in the gap, on the
   * one layout where every pixel of width was the point of going full screen. Safari does the
   * opposite and slides its controls left into the space.
   *
   * A one-way injection rather than a preload: main already reaches into the document this way
   * (see the chrome probe in `main.ts`), and adding a contextBridge to publish one boolean
   * would be a permanent hole opened for a padding rule. The renderer cannot ask for this and
   * does not need to — it is told, and CSS does the rest.
   */
  const setFullScreen = (on: boolean) => {
    if (window.isDestroyed()) return;
    window.webContents
      .executeJavaScript(
        on
          ? 'document.documentElement.dataset.desktopFullscreen = "1";'
          : "delete document.documentElement.dataset.desktopFullscreen;",
      )
      .catch(() => {
        // The page may be mid-navigation or gone. A padding rule is not worth a crash.
      });
  };
  window.on("enter-full-screen", () => setFullScreen(true));
  window.on("leave-full-screen", () => setFullScreen(false));
  // A window restored into full screen never fires `enter-full-screen`, so the page would come
  // up reserving room for controls that are not on screen.
  window.webContents.on("did-finish-load", () => setFullScreen(window.isFullScreen()));

  mainWindow_ = window;
  return window;
}

/** Bring the window back, creating it again if it has genuinely gone. */
export function showMainWindow(): void {
  const existing = mainWindow_;
  if (existing && !existing.isDestroyed()) {
    if (existing.isMinimized()) existing.restore();
    existing.show();
    existing.focus();
    return;
  }
  const window = createMainWindow();
  recoverFromBackendRestarts(window);
  void loadWhenReady(window);
  window.once("ready-to-show", () => window.show());
}

/**
 * Keep the window pinned to the local app, and send anything else to the browser.
 *
 * This is load-bearing beyond ordinary hygiene: task deliverables carry
 * agent-authored URLs, and the UI opens them. Anything handed to the operating
 * system is therefore attacker-influenced in the ordinary case, so the scheme is
 * checked against an allowlist instead of trusting the string.
 */
function hardenNavigation(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(({ url }) => {
    openExternally(url);
    return { action: "deny" };
  });

  window.webContents.on("will-navigate", (event, url) => {
    if (isLocalApp(url)) return;
    event.preventDefault();
    openExternally(url);
  });

  // will-navigate only covers the main frame. Without this, a navigation inside
  // any sub-frame bypasses the check entirely.
  window.webContents.on("will-frame-navigate", (event) => {
    if (isLocalApp(event.url)) return;
    if (!event.isMainFrame && isBuiltInPdfViewer(event.url)) return;
    event.preventDefault();
    openExternally(event.url);
  });
}

/**
 * Right-click, for text, lives in the renderer (`ui/src/components/context-menu.tsx`), not
 * here — tried a native `Menu.popup()` first, and it works, but a native menu is OS chrome:
 * it cannot be styled, so it looks like a different, plainer app dropped on top of this one's
 * own rounded-corner, warm-paper design for the one interaction that happens to route through
 * the main process. The renderer's version calls `event.preventDefault()` on the DOM
 * `contextmenu` event, which stops Electron from ever emitting `webContents`' `context-menu`
 * here at all — so there is nothing left for the main process to do.
 */

/** Chromium's own PDF reader, which renders inside a sub-frame of its own.
 *
 * The file viewer shows a PDF by handing a blob to an `<embed>`; Chromium then navigates a
 * sub-frame to this extension to do the actual rendering. The blob passes the check above —
 * its origin really is ours — but the extension frame does not, and blocking it produced a
 * failure that pointed nowhere: the reader's toolbar painted, the page never arrived, and
 * the title read as the blob's UUID because there was no document to take a name from.
 * Nothing appeared on screen to connect that to a navigation guard, and the one line it
 * wrote went to a terminal nobody running the app will ever see.
 *
 * The ID is fixed and built into Chromium — it is not an installed extension and cannot be
 * one, since Electron loads none. Allowed by exact prefix and only in a sub-frame, so this
 * permits the reader to draw and nothing else: `chrome-extension:` stays refused everywhere
 * else, including for the main frame.
 */
const BUILT_IN_PDF_VIEWER = "chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/";

function isBuiltInPdfViewer(url: string): boolean {
  return url.startsWith(BUILT_IN_PDF_VIEWER);
}

function isLocalApp(url: string): boolean {
  try {
    return new URL(url).origin === new URL(BACKEND_ORIGIN).origin;
  } catch {
    return false;
  }
}

function openExternally(rawUrl: string): void {
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    console.warn("[kith] refusing to open unparseable url");
    return;
  }
  // Allowlist, not denylist. file: would read the user's disk, and custom schemes
  // can hand off to arbitrary installed applications.
  if (!EXTERNAL_SCHEMES.has(parsed.protocol)) {
    console.warn(`[kith] refusing to open ${parsed.protocol} url`);
    return;
  }
  void shell.openExternal(parsed.toString());
}
