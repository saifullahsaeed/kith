import { useCallback, useState } from "react";

import { useConnectionProbe } from "@/hooks/use-connection-probe";
import {
  completeSetup,
  fetchSetup,
  saveSearch,
  type ModelOption,
  type ProviderCard,
  type ProviderKind,
  type ReadinessCheck,
  type SearchKind,
  type SearchOption,
  type SearchProbeOutcome,
} from "@/lib/backend";

/** Where someone is in the flow. Linear: pick a provider, connect it, choose a
 *  model, choose how he searches, see what else is ready. */
export type OnboardingStep = "choose" | "connect" | "model" | "search" | "ready";

const ORDER: OnboardingStep[] = ["choose", "connect", "model", "search", "ready"];

/**
 * The flow's state machine, kept out of the components so they stay about layout.
 *
 * The credential half of this lives in `useConnectionProbe`, shared with Settings —
 * editing a key and being told whether it works is the same job in both places. What
 * is here is only what makes this a *wizard*: which step, what has been chosen so far,
 * and the two writes (connection, then search) that have to happen in that order.
 */
export function useOnboarding(
  providers: ProviderCard[],
  searchOptions: SearchOption[],
  onFinished: () => void,
) {
  const [step, setStep] = useState<OnboardingStep>("choose");
  const [kind, setKind] = useState<ProviderKind | null>(null);
  const [model, setModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [checks, setChecks] = useState<ReadinessCheck[]>([]);
  const [search, setSearch] = useState<SearchKind | null>(null);
  const [searxUrl, setSearxUrl] = useState("");
  const [searchProbe, setSearchProbe] = useState<SearchProbeOutcome | null>(null);
  //  Which options exist depends on the connection, so this is re-read after saving it.
  const [searchOffer, setSearchOffer] = useState<SearchOption[]>(searchOptions);

  const provider = providers.find((entry) => entry.kind === kind) ?? null;
  // Held back until the connect step: probing while someone is still looking at the
  // provider cards would fire a request for a provider they haven't chosen.
  const connection = useConnectionProbe({ provider, enabled: step === "connect" });
  const { baseUrl, apiKey, probe, probing } = connection;

  const choose = useCallback(
    (next: ProviderKind) => {
      setKind(next);
      setModel("");
      setError("");
      connection.reset();
      setStep("connect");
    },
    [connection],
  );

  const back = useCallback(() => {
    setError("");
    setStep((current) => ORDER[Math.max(0, ORDER.indexOf(current) - 1)]);
  }, []);

  /** Pick a model and go on. Suggested models are pre-selected, so this is usually
   *  one click rather than a search. */
  const chooseModel = useCallback((id: string) => {
    setModel(id);
    setError("");
  }, []);

  const toModels = useCallback(() => {
    // Pre-select the "best value" pick rather than the strongest: it's the one most
    // people should start on, and the frontier tier is the expensive default to walk
    // into by accident. Falls back to whatever came first if that tier is missing.
    const value = probe.suggested.find((pick) => pick.tier === "value");
    setModel((current) => current || value?.modelId || probe.suggested[0]?.modelId || "");
    setStep("model");
  }, [probe.suggested]);

  /** Save the connection and move on to search.
   *
   * The connection is committed here rather than at the end because the search step
   * depends on it: OpenRouter's plugin is only offered once he actually thinks through
   * OpenRouter, and the server decides that from what is stored.
   */
  const saveConnection = useCallback(async () => {
    if (!kind || !model) return;
    setSaving(true);
    setError("");
    try {
      const saved = await completeSetup({
        kind,
        baseUrl: baseUrl.trim() || undefined,
        apiKey: apiKey.trim() || undefined,
        model,
      });
      setWarnings(saved.warnings);
      // Re-read the options: which search providers are possible changed the moment
      // the connection was written.
      const fresh = await fetchSetup().catch(() => null);
      if (fresh) {
        setSearchOffer(fresh.search.options);
        setSearch(fresh.search.current.kind);
        setSearxUrl(fresh.search.current.searxUrl ?? "");
      }
      setStep("search");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [kind, model, baseUrl, apiKey]);

  /** Save how he searches, then read back what he can actually do. */
  const finish = useCallback(async () => {
    if (!search) return;
    setSaving(true);
    setError("");
    try {
      await saveSearch({ kind: search, searxUrl: searxUrl.trim() || undefined });
      // The readiness copy is about the choice that was just made, so it has to be
      // fetched after the write, not before.
      const fresh = await fetchSetup().catch(() => null);
      setChecks(fresh?.checks ?? []);
      setStep("ready");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [search, searxUrl]);

  const chosenModel: ModelOption | null = probe.models.find((entry) => entry.id === model) ?? null;

  return {
    step,
    provider,
    providers,
    baseUrl,
    apiKey,
    model,
    chosenModel,
    probe,
    probing,
    saving,
    error: error || connection.error,
    warnings,
    checks,
    search,
    searxUrl,
    searchProbe,
    searchOffer,
    setSearch,
    setSearxUrl,
    setSearchProbe,
    saveConnection,
    setBaseUrl: connection.setBaseUrl,
    setApiKey: connection.setApiKey,
    choose,
    chooseModel,
    toModels,
    back,
    finish,
    enter: onFinished,
    progress: (ORDER.indexOf(step) + 1) / ORDER.length,
  };
}
