import type { ServerConfig } from "./types";

const STORAGE_KEY = "kith-config";

/** Used only until the real defaults arrive from the server. */
export const FALLBACK_CONFIG: ServerConfig = {
  model: "qwen3:4b",
  numCtx: 40960,
  numPredict: 8192,
  system: "",
  think: true,
  baseUrl: "",
  apiKeySet: false,
};

/** Fetch the server's effective defaults (model, sizes, persona). */
export async function fetchServerConfig(signal?: AbortSignal): Promise<ServerConfig> {
  const response = await fetch("/api/config", { signal });
  if (!response.ok) throw new Error(`Server /api/config returned ${response.status}`);
  return (await response.json()) as ServerConfig;
}

/** Persist settings to the SERVER (so autonomy uses them too, not just chat).
 * `apiKey` is write-only — send it to set/change the cloud key; omit to leave it. */
export async function patchServerConfig(
  patch: Partial<Pick<ServerConfig, "model" | "numCtx" | "numPredict" | "think" | "baseUrl">> & {
    apiKey?: string;
  },
): Promise<ServerConfig> {
  const response = await fetch("/api/config", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) throw new Error(`saving config failed (${response.status})`);
  return (await response.json()) as ServerConfig;
}

/**
 * Throw away the browser's copy of the config.
 *
 * There used to be one, and it won: the app started with `loadLocalConfig() ?? server`,
 * so a model chosen in some earlier session shadowed the one in Settings indefinitely —
 * and it was sent to /api/chat as a per-request override, which the server honoured. The
 * settings page showed one model while every turn ran on another. It took OpenRouter's
 * own logs to catch, because nothing in the app ever displayed what was actually in use.
 *
 * Everything here is persisted server-side (see patchServerConfig), so a second copy in
 * the browser could only ever agree or be wrong. Cleared rather than ignored, so it
 * cannot come back on a downgrade.
 */
export function forgetLocalConfig(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* private browsing, or no storage — nothing to forget either way */
  }
}
