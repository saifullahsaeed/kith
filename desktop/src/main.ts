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
 * Step 2 existed because of one specific failure: the SPA opened an EventSource for the activity
 * feed as soon as it mounted, and a failed EventSource handshake closes the stream permanently —
 * the browser does not retry. Loading half a second early therefore cost the live feed for the
 * entire session while the rest of the UI looked healthy.
 *
 * The shell owns that stream now and reconnects with backoff (`server/events.ts`), so that
 * particular bug is gone. The wait remains because the document itself comes from the server, and
 * a window that opens on a connection error is a blank window.
 */

import { app, dialog } from "electron";

import { loadWhenReady, recoverFromBackendRestarts, waitForBackend } from "./server/backend";
import { APP_ICON, BACKEND_ORIGIN, BACKEND_WAIT_MS } from "./config";
import {
  enableSandbox,
  identifyToRenderer,
  installApplicationMenu,
  lockDownPermissions,
} from "./hardening";
import { startEvents, stopEvents } from "./server/events";
import { registerRenderer, unregisterRenderer } from "./render/renderer-registration";
import { startRenderService } from "./render/render-service";
import { createTray, destroyTray } from "./window/tray";
import { ensureServer, stopServer } from "./server/server-process";
import { createMainWindow, markQuitting, showMainWindow } from "./window/window";

// A second instance would fight the first over the same backend and the same
// window; hand focus to the original instead.
if (!app.requestSingleInstanceLock()) {
  app.exit(0);
} else {
  // Both must happen before whenReady to take effect.
  //
  // The name first, and it is not cosmetic. Unset, Electron calls itself "Electron"
  // everywhere it matters: the menu bar, the About panel, the Cmd-Tab switcher, and —
  // the one that bites — macOS Notification Center, which groups and labels notifications
  // by app identity. Notifications from Kith arriving as "Electron", under a setting the
  // user would have to know to look for, is the difference between the feature working and
  // appearing not to.
  app.setName("Kith");
  app.setAboutPanelOptions({
    applicationName: "Kith",
    applicationVersion: app.getVersion(),
    credits: "A mind that lives on this machine.",
  });
  enableSandbox();
  identifyToRenderer();
  app.on("second-instance", () => showMainWindow());
  void start();
}

async function start(): Promise<void> {
  await app.whenReady();

  // `setName` alone was not enough. It fixes what Notification Center *calls* this app;
  // the icon it shows is still whatever the actually-running executable's is, and in
  // development that executable is genuinely Electron.app, not Kith.app — no name change
  // alters that. A packaged build needs none of this: the icon is already in the bundle
  // itself, which is what macOS reads from in that case.
  if (!app.isPackaged && process.platform === "darwin") {
    app.dock?.setIcon(APP_ICON);
  }

  // Both must happen before any content loads: an unconfigured session grants
  // every permission it is asked for.
  lockDownPermissions();
  installApplicationMenu();

  const window = createMainWindow();
  createTray(markQuitting);

  // Start the server ourselves. Until now the shell only ever *waited* for one, which meant
  // a packaged app opened and sat there unless the user happened to have a Python server
  // running — see server-process.ts. Attaches instead of starting when one is already
  // answering, so a developer's own terminal server is not duplicated.
  const server = await ensureServer();
  console.log(`[kith] backend: ${server}`);

  const up = await waitForBackend((elapsed) => {
    if (elapsed % 5_000 < 300) {
      console.log(`[kith] waiting for backend at ${BACKEND_ORIGIN}…`);
    }
  });

  if (!up) {
    reportBackendMissing(server === "no-binary");
    return;
  }

  // Offer our Chromium for rendering pages, so he doesn't need a second browser
  // in his sandbox. Best-effort: a shell that can't do this is still a fine shell.
  try {
    await registerRenderer(await startRenderService());
  } catch (error) {
    console.warn("[kith] render service unavailable, sandbox will render instead:", error);
  }

  // Start listening before the page loads, and keep listening for as long as the app runs.
  //
  // Before the window on purpose: the stream belongs to the app, not to the document. A reload,
  // a hide, a crashed renderer — the page comes and goes underneath a connection that holds its
  // `Last-Event-ID` cursor throughout, which is what lets the interface stop polling. See
  // `server/events.ts`.
  startEvents();

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
function reportBackendMissing(noBinary = false): void {
  const seconds = Math.round(BACKEND_WAIT_MS / 1_000);
  dialog.showErrorBox(
    "Kith isn't running",
    [
      `Couldn't reach the Kith server at ${BACKEND_ORIGIN} after ${seconds} seconds.`,
      "",
      ...(noBinary
        ? [
            "This build has no server in it. That is a packaging fault, not something",
            "you did — a release should carry its own server.",
            "",
            "From a checkout you can build one:",
            "",
            "    cd server && .venv/bin/pyinstaller kith-server.spec --noconfirm",
          ]
        : ["It started but stopped answering. Its log is at ~/.kith/server.log."]),
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
  // Let the stream go before the server is asked to stop, so its retry loop does not spend the
  // shutdown reconnecting to a port that is closing.
  stopEvents();
  // An orphaned server keeps the agent ticking and spending, with no window to see it in,
  // and holds the port so the next launch attaches to a copy nothing controls.
  stopServer();
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
