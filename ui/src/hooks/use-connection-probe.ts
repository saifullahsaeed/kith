import { useCallback, useEffect, useRef, useState } from "react";

import {
  probeConnection,
  type ProbeOutcome,
  type ProviderCard,
  type ProviderKind,
} from "@/lib/backend";

/** Long enough that probing doesn't fire on every keystroke of a pasted key,
 *  short enough that it feels like a reaction rather than a wait. */
const PROBE_DEBOUNCE_MS = 450;

const IDLE_PROBE: ProbeOutcome = {
  reachable: false,
  usable: false,
  detail: "",
  models: [],
  suggested: [],
  keyState: "missing",
  keyDetail: "",
};

/**
 * A provider, a credential, and a live answer about whether they work.
 *
 * Shared by onboarding and by Settings because it is the same job in both: someone
 * edits a key and wants to know within a moment whether it is good. Only the
 * surrounding flow differs — a wizard step in one case, a saveable form in the other —
 * so that is the part left to the caller.
 *
 * Two things here are less obvious than they look. The probe is debounced *and*
 * cancellable: a key gets pasted, edited, re-pasted, and each in-flight request must
 * be abandoned or a stale reply can overwrite a fresh one. And `enabled` exists so a
 * caller can hold it back — onboarding must not probe while someone is still on the
 * provider-choice screen.
 */
export function useConnectionProbe({
  provider,
  enabled = true,
}: {
  provider: ProviderCard | null;
  enabled?: boolean;
}) {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [probe, setProbe] = useState<ProbeOutcome>(IDLE_PROBE);
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef<AbortController | null>(null);

  const kind: ProviderKind | null = provider?.kind ?? null;

  useEffect(() => {
    if (!enabled || !kind || !provider) return;

    const hasUrl = provider.needsBaseUrl ? baseUrl.trim().length > 0 : true;
    const hasKey = apiKey.trim().length > 0;
    // Nothing to ask about yet: an endpoint we don't have, or a key-only provider
    // with neither a key nor a public catalogue.
    if (!hasUrl || (!hasKey && !provider.listsWithoutKey)) {
      setProbe(IDLE_PROBE);
      return;
    }

    const timer = setTimeout(() => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      setProbing(true);
      setError("");

      probeConnection(
        { kind, baseUrl: baseUrl.trim() || undefined, apiKey: apiKey.trim() || undefined },
        controller.signal,
      )
        .then(setProbe)
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          setError(err instanceof Error ? err.message : String(err));
          setProbe(IDLE_PROBE);
        })
        .finally(() => {
          if (!controller.signal.aborted) setProbing(false);
        });
    }, PROBE_DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [enabled, kind, provider, baseUrl, apiKey]);

  useEffect(() => () => inFlight.current?.abort(), []);

  /** Start over on a different provider. Clears the credential deliberately: a key
   *  for one endpoint is meaningless at another, and carrying it across would send it
   *  somewhere it was never meant to go. */
  const reset = useCallback(() => {
    setBaseUrl("");
    setApiKey("");
    setProbe(IDLE_PROBE);
    setError("");
  }, []);

  return {
    baseUrl,
    apiKey,
    probe,
    probing,
    error,
    setBaseUrl,
    setApiKey,
    setError,
    reset,
  };
}
