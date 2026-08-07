/**
 * Tell the Kith server that this shell can render pages for it.
 *
 * The two processes start independently — the server is usually already running —
 * so the shell announces itself rather than being configured. The port and token
 * are minted per launch, which is why this is a call at startup and not a setting
 * anyone edits.
 */

import { apiHeaders } from "../server/api-token";
import { BACKEND_ORIGIN } from "../config";
import type { RenderEndpoint } from "./render-service";

const REGISTER_URL = `${BACKEND_ORIGIN}/api/renderer`;
const TIMEOUT_MS = 5_000;

export async function registerRenderer(endpoint: RenderEndpoint): Promise<void> {
  const response = await post(JSON.stringify(endpoint), "POST");
  if (!response.ok) throw new Error(`server refused renderer registration (${response.status})`);
  console.log("[kith] registered our Chromium as Kith's renderer");
}

/**
 * Withdraw the offer on the way out.
 *
 * Not tidiness: the registration outlives the port it points at, so leaving it
 * behind makes the next `browse_page` wait out a connection timeout before it
 * falls back to the sandbox. Best-effort by design — the app is quitting, and
 * blocking that on an HTTP call would be worse than a stale entry.
 */
export async function unregisterRenderer(): Promise<void> {
  try {
    await post("", "DELETE");
  } catch {
    /* quitting anyway; the server drops the stale entry on first failed use */
  }
}

async function post(body: string, method: "POST" | "DELETE"): Promise<Response> {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), TIMEOUT_MS);
  try {
    return await fetch(REGISTER_URL, {
      method,
      headers: apiHeaders({ "Content-Type": "application/json" }),
      ...(body ? { body } : {}),
      signal: abort.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}
