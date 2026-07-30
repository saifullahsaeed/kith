import { useCallback, useEffect, useRef, useState } from "react";

import {
  completeSetup,
  fetchSetup,
  probeConnection,
  saveSearch,
  type ModelOption,
  type ProbeOutcome,
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

/** Long enough that probing doesn't fire on every keystroke of a pasted key,
 *  short enough that it feels like a reaction rather than a wait. */
const PROBE_DEBOUNCE_MS = 450;

const IDLE: ProbeOutcome = {
  reachable: false,
  usable: false,
  detail: "",
  models: [],
  suggested: [],
  keyState: "missing",
  keyDetail: "",
};

/**
 * The whole flow's state, kept out of the components so they stay about layout.
 *
 * Two things here are less obvious than they look. Probing is debounced *and*
 * cancellable: a key gets pasted, edited, re-pasted, and each in-flight request must
 * be abandoned or a stale reply can overwrite a fresh one. And the credential is
 * tracked apart from reachability, because a provider with a public catalogue answers
 * before there is any key to check — the model list can be shown while "Continue"
 * stays disabled.
 */
export function useOnboarding(
  providers: ProviderCard[],
  searchOptions: SearchOption[],
  onFinished: () => void,
) {
  const [step, setStep] = useState<OnboardingStep>("choose");
  const [kind, setKind] = useState<ProviderKind | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");

  const [probing, setProbing] = useState(false);
  const [probe, setProbe] = useState<ProbeOutcome>(IDLE);
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
  const inFlight = useRef<AbortController | null>(null);

  // Probe whenever the credentials change and we're on the step that needs them.
  // The effect owns cancellation, so an abandoned attempt can never land late.
  useEffect(() => {
    if (step !== "connect" || !kind || !provider) return;

    const ready = provider.needsBaseUrl ? baseUrl.trim().length > 0 : true;
    const hasKey = apiKey.trim().length > 0;
    // Nothing to ask about yet: an endpoint we don't have, or a key-only provider
    // with neither a key nor a public catalogue.
    if (!ready || (!hasKey && !provider.listsWithoutKey)) {
      setProbe(IDLE);
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
          setProbe(IDLE);
        })
        .finally(() => {
          if (!controller.signal.aborted) setProbing(false);
        });
    }, PROBE_DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [step, kind, provider, baseUrl, apiKey]);

  useEffect(() => () => inFlight.current?.abort(), []);

  const choose = useCallback((next: ProviderKind) => {
    setKind(next);
    setBaseUrl("");
    setApiKey("");
    setModel("");
    setProbe(IDLE);
    setError("");
    setStep("connect");
  }, []);

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
    // The server ranks these; taking its first is a better default than alphabetical.
    setModel((current) => current || probe.suggested[0] || "");
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
    error,
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
    setBaseUrl,
    setApiKey,
    choose,
    chooseModel,
    toModels,
    back,
    finish,
    enter: onFinished,
    progress: (ORDER.indexOf(step) + 1) / ORDER.length,
  };
}
