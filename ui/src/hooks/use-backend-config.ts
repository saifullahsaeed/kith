import { useCallback, useEffect, useState } from "react";

import {
  FALLBACK_CONFIG,
  fetchServerConfig,
  loadLocalConfig,
  saveLocalConfig,
  type ServerConfig,
} from "@/lib/backend";

export type ConnectionStatus = "loading" | "ready" | "error";

/**
 * Loads the server's config on mount and tracks connection status. A user's
 * saved overrides (from a previous session) win over the server defaults;
 * `updateConfig` persists changes back to localStorage.
 */
export function useBackendConfig() {
  const [status, setStatus] = useState<ConnectionStatus>("loading");
  const [error, setError] = useState("");
  const [config, setConfig] = useState<ServerConfig>(FALLBACK_CONFIG);
  const [serverDefaults, setServerDefaults] = useState<ServerConfig>(FALLBACK_CONFIG);

  useEffect(() => {
    const controller = new AbortController();
    fetchServerConfig(controller.signal)
      .then((server) => {
        setServerDefaults(server);
        setConfig(loadLocalConfig() ?? server);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        setStatus("error");
      });
    return () => controller.abort();
  }, []);

  const updateConfig = useCallback((next: ServerConfig) => {
    setConfig(next);
    saveLocalConfig(next);
  }, []);

  return { status, error, config, serverDefaults, updateConfig };
}
