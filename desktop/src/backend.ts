/**
 * Knowing whether the Kith server is up, and waiting for it if it is starting.
 *
 * The shell does not start or supervise the backend — it runs as its own process
 * (Docker today) and keeps the agent working whether this window is open or not.
 * All this module does is answer "can I load the UI yet?".
 */

import { BACKEND_POLL_MS, BACKEND_WAIT_MS, HEALTH_URL } from "./config";

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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
