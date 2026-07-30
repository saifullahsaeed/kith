/**
 * Kith desktop — application entry point.
 *
 * The shell is deliberately thin. It owns the window, the tray, and the app
 * lifecycle; it does not own the UI (that is the same React build the browser
 * loads) and it does not own the agent (that is the Kith server, a separate
 * process that keeps working whether this app is running or not).
 *
 * Startup order matters and is not arbitrary:
 *
 *   1. Take the single-instance lock, so a second launch focuses the first window
 *      instead of racing it for the same backend.
 *   2. Wait for the backend to answer /api/health.
 *   3. Only then load the UI.
 *
 * Step 2 exists because of one specific failure: the SPA opens an EventSource for
 * the activity feed as soon as it mounts, and a failed EventSource handshake closes
 * the stream permanently — the browser does not retry. Loading half a second early
 * therefore costs the live feed for the entire session, while the rest of the UI
 * looks perfectly healthy. Waiting is cheap; that bug is invisible and confusing.
 */

import { app, dialog } from "electron";

import { loadWhenReady, recoverFromBackendRestarts, waitForBackend } from "./backend";
import { BACKEND_ORIGIN, BACKEND_WAIT_MS } from "./config";
import {
  enableSandbox,
  identifyToRenderer,
  installApplicationMenu,
  lockDownPermissions,
} from "./hardening";
import { registerRenderer, unregisterRenderer } from "./renderer-registration";
import { startRenderService } from "./render-service";
import { createTray, destroyTray } from "./tray";
import { createMainWindow, markQuitting, showMainWindow } from "./window";

// A second instance would fight the first over the same backend and the same
// window; hand focus to the original instead.
if (!app.requestSingleInstanceLock()) {
  app.exit(0);
} else {
  // Both must happen before whenReady to take effect.
  enableSandbox();
  identifyToRenderer();
  app.on("second-instance", () => showMainWindow());
  void start();
}

async function start(): Promise<void> {
  await app.whenReady();

  // Both must happen before any content loads: an unconfigured session grants
  // every permission it is asked for.
  lockDownPermissions();
  installApplicationMenu();

  const window = createMainWindow();
  createTray(markQuitting);

  const up = await waitForBackend((elapsed) => {
    if (elapsed % 5_000 < 300) {
      console.log(`[kith] waiting for backend at ${BACKEND_ORIGIN}…`);
    }
  });

  if (!up) {
    reportBackendMissing();
    return;
  }

  // Offer our Chromium for rendering pages, so he doesn't need a second browser
  // in his sandbox. Best-effort: a shell that can't do this is still a fine shell.
  try {
    await registerRenderer(await startRenderService());
  } catch (error) {
    console.warn("[kith] render service unavailable, sandbox will render instead:", error);
  }

  // Retrying, so a server that restarts underneath us costs a blink rather than the
  // whole window: without this, one failed load left the app with nothing on screen.
  recoverFromBackendRestarts(window);
  if (!(await loadWhenReady(window))) {
    reportBackendMissing();
    return;
  }
  window.show();
  window.focus();

  if (process.argv.includes("--dev")) await reportChrome(window);
}

/**
 * Log what the page actually decided about window chrome.
 *
 * The desktop-only layout is driven by a user-agent token, which means it can fail
 * silently: get the token wrong and the traffic lights sit on top of the header
 * with nothing in any log to say so. This reads the computed result back out of the
 * live window, so `npm run dev` answers the question instead of the eye.
 */
async function reportChrome(window: Electron.BrowserWindow): Promise<void> {
  try {
    const probe = await window.webContents.executeJavaScript(`(async () => {
      const root = document.documentElement;
      // loadURL resolves when the document has loaded, which is BEFORE React has
      // mounted anything — so wait for the header to exist rather than reporting
      // its absence as a layout failure.
      const deadline = Date.now() + 5000;
      let bar = null;
      while (!(bar = document.querySelector('header.window-drag-region')) && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 100));
      }
      return {
        sawToken: / KithDesktop\\//.test(navigator.userAgent),
        desktop: root.dataset.desktop ?? null,
        platform: root.dataset.desktopPlatform ?? null,
        inset: getComputedStyle(root).getPropertyValue('--window-controls-inset').trim(),
        headerPaddingLeft: bar ? getComputedStyle(bar).paddingLeft : 'NO DRAG REGION FOUND',
        dragRegion: bar ? getComputedStyle(bar).webkitAppRegion || '(unset)' : null,
      };
    })()`);
    console.log("[kith] window chrome:", probe);
    if (!probe.sawToken || probe.platform !== "mac") {
      console.warn("[kith] window-control inset NOT applied — traffic lights will overlap the header");
    }
  } catch (error) {
    console.warn("[kith] could not probe window chrome:", error);
  }
}

/**
 * Say plainly what is wrong and what to do about it.
 *
 * A blank window with a console error is the worst outcome here: the backend not
 * running is the single most likely reason this app fails to start, and it is
 * entirely fixable by the person looking at the screen.
 */
function reportBackendMissing(): void {
  const seconds = Math.round(BACKEND_WAIT_MS / 1_000);
  dialog.showErrorBox(
    "Kith isn't running",
    [
      `Couldn't reach the Kith server at ${BACKEND_ORIGIN} after ${seconds} seconds.`,
      "",
      "Start it, then open Kith again:",
      "",
      "    cd ~/Desktop/personal/ai-fun/kith && make server",
    ].join("\n"),
  );
  markQuitting();
  app.quit();
}

/**
 * before-quit, not will-quit: will-quit only fires once every window has closed,
 * and the window's own close handler cancels closing unless we have already said
 * we are quitting. Doing teardown here breaks that circle.
 */
app.on("before-quit", () => {
  markQuitting();
  destroyTray();
  // Tell the backend to stop offering our renderer — the port dies with us, and a
  // stale registration would cost every later browse a timeout before falling back.
  void unregisterRenderer();
});

// On macOS an app with no windows is normal and should stay alive — the tray is
// the whole point. Note this event does NOT fire on Cmd+Q, which is why quitting
// is handled by the quit flag rather than by counting windows.
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// Clicking the dock icon. showMainWindow already handles both cases — an
// existing hidden window, and one that has genuinely gone.
app.on("activate", () => showMainWindow());
