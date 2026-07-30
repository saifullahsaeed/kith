import { useCallback, useEffect, useState } from "react";

import {
  FALLBACK_CONFIG,
  fetchServerConfig,
  forgetLocalConfig,
  type ServerConfig,
} from "@/lib/backend";

export type ConnectionStatus = "loading" | "ready" | "error";

/**
 * The server's config, and the connection status while fetching it.
 *
 * The server is the only source of truth. It used to be "the browser's saved copy if
 * there is one, else the server", which meant a model picked in an old session outranked
 * the one in Settings forever — and got sent back as a per-request override that the
 * server obeyed. Settings displayed one model, every turn used another.
 *
 * `reload` exists so saving in Settings refreshes what is on screen straight away; the
 * header shows the model, and a header that lags is how this hid in the first place.
 */
export function useBackendConfig() {
  const [status, setStatus] = useState<ConnectionStatus>("loading");
  const [error, setError] = useState("");
  const [config, setConfig] = useState<ServerConfig>(FALLBACK_CONFIG);

  const load = useCallback((signal?: AbortSignal) => {
    fetchServerConfig(signal)
      .then((server) => {
        setConfig(server);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (signal?.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        setStatus("error");
      });
  }, []);

  useEffect(() => {
    forgetLocalConfig();
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const reload = useCallback(() => load(), [load]);

  // Settings already persisted this to the server before calling us; hold it locally so
  // the interface updates without a round trip.
  const updateConfig = useCallback((next: ServerConfig) => setConfig(next), []);

  return { status, error, config, updateConfig, reload };
}
