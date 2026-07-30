import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Globe,
  Loader2,
  MinusCircle,
  Server,
} from "lucide-react";

import {
  probeSearch,
  type SearchKind,
  type SearchOption,
  type SearchProbeOutcome,
} from "@/lib/backend";

const ICONS: Record<SearchKind, typeof Globe> = {
  searxng: Server,
  openrouter: Globe,
  none: MinusCircle,
};

/** Long enough not to fire on every character of a typed address. */
const PROBE_DEBOUNCE_MS = 500;

/**
 * How he searches. Free, metered, or not at all.
 *
 * Search is the one capability whose supply sits outside the app: a SearXNG instance
 * leans on public engines that rate-limit it without warning, and OpenRouter's plugin
 * costs real money per call. Those are different kinds of cost, so this is a decision
 * rather than a default — and the previous behaviour, silently falling back from free
 * to metered, is exactly the thing someone who chose free would not want.
 *
 * The chosen option is checked by running a real search, because "the URL is valid" and
 * "search works" are not the same claim, and only the second one is useful.
 */
export function SearchStep({
  options,
  selected,
  searxUrl,
  onSelect,
  onSearxUrl,
  onProbe,
}: {
  options: SearchOption[];
  selected: SearchKind | null;
  searxUrl: string;
  onSelect: (kind: SearchKind) => void;
  onSearxUrl: (url: string) => void;
  /** Reported upward so the footer can refuse to advance on a dead instance. */
  onProbe: (outcome: SearchProbeOutcome | null) => void;
}) {
  const [probe, setProbe] = useState<SearchProbeOutcome | null>(null);
  const [probing, setProbing] = useState(false);
  const inFlight = useRef<AbortController | null>(null);
  const chosen = options.find((option) => option.kind === selected) ?? null;

  useEffect(() => {
    if (!selected) return;
    const timer = setTimeout(() => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      setProbing(true);

      probeSearch({ kind: selected, searxUrl: searxUrl.trim() || undefined }, controller.signal)
        .then((outcome) => {
          setProbe(outcome);
          onProbe(outcome);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          const failed = {
            working: false,
            detail: err instanceof Error ? err.message : String(err),
            hits: 0,
          };
          setProbe(failed);
          onProbe(failed);
        })
        .finally(() => {
          if (!controller.signal.aborted) setProbing(false);
        });
    }, PROBE_DEBOUNCE_MS);

    return () => clearTimeout(timer);
    // onProbe is stable (a setState); including it would re-probe on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, searxUrl]);

  useEffect(() => () => inFlight.current?.abort(), []);

  return (
    <div className="space-y-3">
      <div className="grid gap-2">
        {options.map((option) => (
          <OptionRow
            key={option.kind}
            option={option}
            active={option.kind === selected}
            onSelect={() => option.available && onSelect(option.kind)}
          />
        ))}
      </div>

      {chosen?.needsUrl ? (
        <label className="block pt-1">
          <span className="mb-1.5 block text-sm font-medium">Address</span>
          <input
            className="w-full rounded-md border bg-transparent px-3 py-2 font-mono text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
            placeholder={chosen.defaultUrl}
            value={searxUrl}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => onSearxUrl(event.target.value)}
          />
          <span className="text-muted-foreground mt-1.5 block text-xs">
            Leave it blank to use {chosen.defaultUrl}.
          </span>
        </label>
      ) : null}

      <Status probing={probing} probe={probe} selected={selected} />
    </div>
  );
}

function Status({
  probing,
  probe,
  selected,
}: {
  probing: boolean;
  probe: SearchProbeOutcome | null;
  selected: SearchKind | null;
}) {
  if (!selected) {
    return <p className="text-muted-foreground/60 min-h-5 text-sm">Pick one to check it.</p>;
  }
  if (probing) {
    return (
      <p className="text-muted-foreground flex min-h-5 items-center gap-2 text-sm">
        <Loader2 className="size-4 animate-spin" /> Trying a search…
      </p>
    );
  }
  if (!probe) return <p className="min-h-5" />;

  return (
    <p
      className={`animate-in fade-in flex min-h-5 items-start gap-2 text-sm duration-200 ${
        probe.working ? "text-roam" : "text-destructive"
      }`}
    >
      <span className="mt-px shrink-0">
        {probe.working ? <CheckCircle2 className="size-4" /> : <AlertCircle className="size-4" />}
      </span>
      <span>{probe.detail}</span>
    </p>
  );
}

function OptionRow({
  option,
  active,
  onSelect,
}: {
  option: SearchOption;
  active: boolean;
  onSelect: () => void;
}) {
  const Icon = ICONS[option.kind] ?? Globe;
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={!option.available}
      aria-pressed={active}
      className={`flex gap-3 rounded-xl border p-3 text-left transition-all duration-150 ${
        option.available
          ? "hover:border-kith/50 hover:bg-card focus-visible:border-kith focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          : "cursor-not-allowed opacity-55"
      } ${active ? "border-kith bg-kith-soft/40" : "bg-card/60"}`}
    >
      <span
        className={`mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg ${
          active ? "bg-kith-soft text-kith" : "bg-muted text-muted-foreground"
        }`}
      >
        <Icon className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="text-sm font-semibold">{option.label}</span>
          {active ? <Check className="size-4 text-kith" /> : null}
        </span>
        <span className="text-muted-foreground mt-0.5 block text-xs">{option.blurb}</span>
        <span className="text-muted-foreground/85 mt-1.5 block text-xs leading-relaxed">
          {option.tradeoff}
        </span>
        {option.unavailableBecause ? (
          <span className="text-muted-foreground/70 mt-1.5 block text-[11px] italic">
            {option.unavailableBecause}
          </span>
        ) : null}
      </span>
    </button>
  );
}
