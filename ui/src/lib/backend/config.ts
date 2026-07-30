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
  patch: Partial<Pick<ServerConfig, "model" | "numCtx" | "numPredict" | "think" | "baseUrl">> & { apiKey?: string },
): Promise<ServerConfig> {
  const response = await fetch("/api/config", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) throw new Error(`saving config failed (${response.status})`);
  return (await response.json()) as ServerConfig;
}

/** The user's saved overrides from a previous session, or null if none. */
export function loadLocalConfig(): ServerConfig | null {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? (JSON.parse(saved) as ServerConfig) : null;
  } catch {
    return null;
  }
}

export function saveLocalConfig(config: ServerConfig): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
}
