import { useCallback, useEffect, useState } from "react";

import { fetchSetup, type SetupSnapshot } from "@/lib/backend";

export type SetupStatus = "loading" | "onboarding" | "ready" | "error";

/**
 * Whether Kith is set up, asked before anything else is loaded.
 *
 * This is the gate: the workspace assumes a working connection everywhere, so
 * discovering there isn't one has to happen before it mounts, not inside it.
 * `enter()` is what onboarding calls when it finishes — a state change rather than a
 * page reload, so the app is never seen tearing itself down and rebuilding.
 */
export function useSetup() {
  const [status, setStatus] = useState<SetupStatus>("loading");
  const [snapshot, setSnapshot] = useState<SetupSnapshot | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetchSetup(controller.signal)
      .then((next) => {
        setSnapshot(next);
        setStatus(next.onboarded ? "ready" : "onboarding");
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        setStatus("error");
      });
    return () => controller.abort();
  }, []);

  const enter = useCallback(() => setStatus("ready"), []);

  return { status, snapshot, error, enter };
}
