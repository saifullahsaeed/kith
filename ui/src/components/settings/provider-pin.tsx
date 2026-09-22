import { type ReactNode, useEffect, useState } from "react";
import { AlertTriangle, Check, Loader2 } from "lucide-react";

import {
  fetchModelEndpoints,
  formatContext,
  formatPrice,
  type ModelEndpoint,
  type ModelEndpoints,
} from "@/lib/backend";
import { cn } from "@/lib/utils";

/**
 * Pinning an OpenRouter host, as a list of the hosts there are rather than a text box.
 *
 * The setting is old; what it never had was a way to answer it. It takes a provider slug —
 * `deepinfra`, `morph/fp8` — and no screen in Kith has ever printed one, so using it meant
 * knowing the slug from OpenRouter's own site and spelling it right first time. A wrong slug
 * does not fail: `provider.order` with an unrecognised name is ignored, fallbacks serve the
 * round, and the field reads back exactly as typed while doing nothing at all. That is the
 * worst shape a setting can have — it looks applied.
 *
 * So every column here exists to make one comparison possible that the price sort cannot make:
 *
 * - **Effective price**, not the headline rate. Around four fifths of this install's prompt
 *   tokens are cache *reads*, and hosts quoting an identical prompt rate differ five-fold on
 *   the read rate. The blended figure is the one the router ranks on, so it is the one shown.
 * - **Quantization**, which nothing ranks on and which is usually the entire difference
 *   between the cheapest row and a good one. The same model name at the same context length
 *   can be bf16 on one host and int4 on another, and only this column says so.
 * - **Measured throughput**, because among hosts OpenRouter calls a tie on price there has
 *   been a thirteen-fold spread in speed.
 *
 * Rows are in the order the router would *actually* try them, so a pin is visibly a choice to
 * override that and not a choice made in a vacuum.
 */
export function ProviderPin({
  value,
  disabled,
  onChange,
}: {
  /** The slug currently held — the draft if edited, otherwise what is saved. "" is automatic. */
  value: string;
  disabled?: boolean;
  onChange: (slug: string) => void;
}) {
  const [state, setState] = useState<ModelEndpoints | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState("");

  useEffect(() => {
    let live = true;
    setLoading(true);
    fetchModelEndpoints()
      .then((got) => live && setState(got))
      .catch((err: unknown) => live && setFailed(err instanceof Error ? err.message : String(err)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  if (loading) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 px-4 py-4 text-[11.5px]">
        <Loader2 className="size-3 animate-spin" />
        Reading which hosts serve this model…
      </div>
    );
  }

  if (failed || !state) {
    return <Note>Couldn’t read the host list. {failed}</Note>;
  }

  if (!state.supported) {
    return (
      <Note>
        This connection isn’t OpenRouter, so there is nothing to pin — one endpoint serves every
        round already.
      </Note>
    );
  }

  if (!state.rows.length) {
    return (
      <Note>
        OpenRouter returned no host table for <Slug>{state.model}</Slug>. Routing falls back to
        its own balancing until it does; the pin below would have nothing to match.
      </Note>
    );
  }

  return (
    <div className="px-4 pt-1 pb-4">
      {/* A pin naming a host that does not serve the current model is the silent failure this
          screen was built to end, so it is said before the list rather than left to be noticed
          by its absence from the list. */}
      {!state.pinnedServesModel ? (
        <div className="border-destructive/30 bg-destructive/5 text-destructive mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-[11.5px] leading-snug">
          <AlertTriangle className="mt-px size-3.5 shrink-0" />
          <span>
            <Slug>{state.pinned}</Slug> doesn’t serve <Slug>{state.model}</Slug>, so this pin is
            doing nothing — rounds are being routed automatically. Pick a host below, or clear it.
          </span>
        </div>
      ) : null}

      <div className="border-border/60 overflow-hidden rounded-lg border">
        <Row
          selected={!value}
          disabled={disabled}
          onClick={() => onChange("")}
          name="Automatic"
          detail={
            state.rankedBy
              ? `Whichever host is best by ${state.rankedBy}, re-checked when a conversation starts.`
              : "Left to OpenRouter’s own balancing."
          }
        />
        {state.rows.map((host) => (
          <Row
            key={host.slug}
            selected={value === host.slug}
            disabled={disabled}
            onClick={() => onChange(host.slug)}
            name={host.name}
            slug={host.slug}
            host={host}
          />
        ))}
      </div>

      <p className="text-muted-foreground/70 mt-2 text-[11px] leading-snug">
        Price is the prompt, cache-read and completion rates blended at the share each actually
        arrives in — not the headline rate, which on a cache-heavy agent is the wrong number.
        Speed and uptime are OpenRouter’s own measurements over the last half hour. A pin keeps
        fallbacks on, so a host going down costs a slower round rather than a failed one.
      </p>
    </div>
  );
}

function Row({
  selected,
  disabled,
  onClick,
  name,
  slug,
  detail,
  host,
}: {
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
  name: string;
  slug?: string;
  detail?: string;
  host?: ModelEndpoint;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "border-border/60 flex w-full items-center gap-3 px-3 py-2 text-left transition-colors [&+&]:border-t",
        selected ? "bg-accent/60" : "hover:bg-accent/30",
        disabled ? "cursor-not-allowed opacity-55" : "",
      )}
    >
      <Check
        className={cn("size-3.5 shrink-0", selected ? "text-kith" : "opacity-0")}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="truncate text-[12.5px]">{name}</span>
          {/* Unhealthy hosts are shown rather than hidden: they still serve rounds as
              fallbacks, and a list that quietly omitted one would be lying about what can
              happen. They simply never lead. */}
          {host && !host.healthy ? (
            <span className="text-destructive/80 text-[10px]">deranked</span>
          ) : null}
          {host?.quantization ? (
            <span className="text-muted-foreground/70 font-mono text-[10px]">
              {host.quantization}
            </span>
          ) : null}
        </div>
        {detail ? (
          <div className="text-muted-foreground/70 mt-0.5 text-[11px] leading-snug">{detail}</div>
        ) : slug ? (
          <div className="text-muted-foreground/50 mt-0.5 truncate font-mono text-[10px]">
            {slug}
          </div>
        ) : null}
      </div>
      {host ? (
        <div className="flex shrink-0 items-baseline gap-4 font-mono text-[11px] tabular-nums">
          <Cell label="/Mtok" value={formatPrice(host.effectivePerMtok)} />
          <Cell label="tok/s" value={host.throughput ? Math.round(host.throughput).toString() : "—"} />
          <Cell label="ctx" value={formatContext(host.contextLength)} />
          <Cell label="up" value={`${host.uptime.toFixed(0)}%`} />
        </div>
      ) : null}
    </button>
  );
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="w-[4.5rem] text-right leading-tight">
      <div className="text-muted-foreground/40 text-[9px] tracking-[0.1em] uppercase">{label}</div>
      <div>{value}</div>
    </div>
  );
}

function Note({ children }: { children: ReactNode }) {
  return (
    <p className="text-muted-foreground max-w-[58ch] px-4 py-3 text-[11.5px] leading-[1.45]">
      {children}
    </p>
  );
}

function Slug({ children }: { children: ReactNode }) {
  return <code className="font-mono text-[11px]">{children}</code>;
}
