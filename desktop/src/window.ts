/**
 * The main window: creation, close behaviour, and where links are allowed to go.
 *
 * No preload and no contextBridge, on purpose. Every security-relevant
 * `webPreferences` default in current Electron is already the safe one
 * (contextIsolation on, nodeIntegration off, sandbox on), and the renderer only
 * ever needs `fetch` to its own origin — exactly what it already does in a browser.
 * Adding a preload would mean widening the attack surface to gain nothing.
 */

import { BrowserWindow, shell } from "electron";

import { loadWhenReady, recoverFromBackendRestarts } from "./backend";
import { BACKEND_ORIGIN, EXTERNAL_SCHEMES, WINDOW } from "./config";
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
    webPreferences: {
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
      // Required, not a tuning knob. Closing the window HIDES it, and Chromium
      // throttles timers and network in hidden windows — which would quietly
      // starve the SSE activity feed exactly when the app is meant to be sitting
      // in the tray watching him work.
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
