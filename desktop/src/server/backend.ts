/**
 * Knowing whether the Kith server is up, and waiting for it if it is starting.
 *
 * The shell does not start or supervise the backend — it runs as its own process
 * (Docker today) and keeps the agent working whether this window is open or not.
 * All this module does is answer "can I load the UI yet?".
 */

import { BACKEND_ORIGIN, BACKEND_POLL_MS, BACKEND_WAIT_MS, HEALTH_URL } from "../config";

/** One probe. Resolves false rather than throwing — "not up yet" is expected. */
export async function isBackendUp(timeoutMs = 1_500): Promise<boolean> {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeoutMs);
  try {
    const response = await fetch(HEALTH_URL, { signal: abort.signal });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Poll until the backend answers, or the deadline passes.
 *
 * Returns whether it came up, and never throws — a missing backend is a state the
 * UI should explain, not a crash. See BACKEND_WAIT_MS for why loading the window
 * early is worse than waiting.
 */
export async function waitForBackend(
  onProgress?: (elapsedMs: number) => void,
): Promise<boolean> {
  const startedAt = Date.now();
  for (;;) {
    if (await isBackendUp()) return true;

    const elapsed = Date.now() - startedAt;
    if (elapsed >= BACKEND_WAIT_MS) return false;
    onProgress?.(elapsed);
    await sleep(BACKEND_POLL_MS);
  }
}

/**
 * Load the UI, waiting for the backend if it is not there yet, and keep trying.
 *
 * `loadURL` on its own is a single attempt that rejects if the server is not
 * listening at that exact moment — and the caller then never reaches `window.show()`,
 * so the app appears not to launch at all. Nothing in the window says why, because
 * there is no window.
 *
 * The gap is real even with a health check in front of it: the server can be restarted
 * while the app is open (it is a separate process, by design), and a page load attempted
 * in that moment fails permanently. Retrying is what makes the shell survive its backend
 * coming and going, which for a packaged app that starts both at once is the normal case
 * rather than the exception.
 */
export async function loadWhenReady(window: Electron.BrowserWindow): Promise<boolean> {
  for (;;) {
    if (window.isDestroyed()) return false;
    if (await waitForBackend()) {
      try {
        await window.loadURL(BACKEND_ORIGIN);
        return true;
      } catch (error) {
        // Between the probe and the load. Only worth a line; the retry handles it.
        console.warn("[kith] load failed, retrying:", error);
      }
    } else {
      return false;
    }
    await sleep(BACKEND_POLL_MS);
  }
}

/**
 * Reload the window whenever a page load fails, once the backend is back.
 *
 * Restart the server with the app open and Chromium lands on its own error page and
 * stays there — the SPA is gone, so nothing in the app can recover it. This is the only
 * place that can.
 */
export function recoverFromBackendRestarts(window: Electron.BrowserWindow): void {
  let recovering = false;
  window.webContents.on("did-fail-load", (_event, code, description, url, isMainFrame) => {
    // Sub-resources and cancelled navigations (-3) are not the case this exists for.
    if (!isMainFrame || code === -3) return;
    if (recovering) return;
    recovering = true;
    console.warn(`[kith] page load failed (${code} ${description}) at ${url} — waiting for backend`);
    void loadWhenReady(window).finally(() => {
      recovering = false;
    });
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
