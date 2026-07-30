import { useMemo, useState } from "react";
import { Check, Gauge, PackageOpen, Search, Sparkles, Wallet } from "lucide-react";

import {
  formatContext,
  formatPrice,
  type ModelOption,
  type Pick as ModelPick,
  type Tier,
} from "@/lib/backend";

/** A catalogue can run to hundreds of entries. Rendering them all costs a visible
 *  frame drop on the first keystroke, and nobody scrolls past forty. */
const MAX_ROWS = 40;

const TIER_ICONS: Record<Tier, typeof Sparkles> = {
  frontier: Gauge,
  value: Wallet,
  open: PackageOpen,
};

/**
 * Choosing a model out of a list that might be three long or four hundred.
 *
 * The three cards on top are not a price ladder, which is what they used to be. They
 * are three different reasons to pick a model — the strongest, the best per dollar,
 * and the strongest with published weights — and each carries the number it was chosen
 * on, so the recommendation can be checked rather than taken on faith.
 *
 * The number that matters is the agentic index: how well a model sustains multi-step
 * tool use, which is the entirety of what Kith does and the one thing price does not
 * predict. Where a provider publishes no measurements the cards say so plainly instead
 * of dressing price up as quality.
 */
export function ModelStep({
  models,
  suggested,
  selected,
  onSelect,
}: {
  models: ModelOption[];
  suggested: ModelPick[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");

  const picks = useMemo(
    () =>
      suggested
        .map((pick) => ({ pick, model: models.find((entry) => entry.id === pick.modelId) }))
        .filter((entry): entry is { pick: ModelPick; model: ModelOption } => Boolean(entry.model)),
    [suggested, models],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const pool = needle
      ? models.filter(
          (entry) =>
            entry.id.toLowerCase().includes(needle) || entry.name.toLowerCase().includes(needle),
        )
      : models;
    return { rows: pool.slice(0, MAX_ROWS), total: pool.length };
  }, [query, models]);

  return (
    <div className="space-y-4">
      {picks.length > 0 && !query ? (
        <div className="grid gap-2 sm:grid-cols-3">
          {picks.map(({ pick, model }) => (
            <PickCard
              key={pick.modelId}
              pick={pick}
              model={model}
              active={pick.modelId === selected}
              onSelect={() => onSelect(pick.modelId)}
            />
          ))}
        </div>
      ) : null}

      <div>
        <div className="relative">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
          <input
            className="w-full rounded-md border bg-transparent py-2 pr-3 pl-9 text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
            placeholder={`Search all ${models.length} models…`}
            value={query}
            spellCheck={false}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        <div className="mt-2 max-h-56 overflow-y-auto rounded-md border">
          {filtered.rows.length === 0 ? (
            <p className="text-muted-foreground p-4 text-center text-sm">
              Nothing matches “{query}”.
            </p>
          ) : (
            filtered.rows.map((option) => (
              <Row
                key={option.id}
                option={option}
                active={option.id === selected}
                onSelect={() => onSelect(option.id)}
              />
            ))
          )}
          {filtered.total > filtered.rows.length ? (
            <p className="text-muted-foreground/70 border-t px-3 py-2 text-center text-xs">
              {filtered.total - filtered.rows.length} more — narrow the search to see them
            </p>
          ) : null}
        </div>
        <p className="text-muted-foreground/70 mt-2 text-xs">
          Ranked by how well each model handles multi-step work, best first.
        </p>
      </div>
    </div>
  );
}

function PickCard({
  pick,
  model,
  active,
  onSelect,
}: {
  pick: ModelPick;
  model: ModelOption;
  active: boolean;
  onSelect: () => void;
}) {
  const Icon = TIER_ICONS[pick.tier] ?? Sparkles;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex flex-col rounded-xl border p-3 text-left transition-all duration-150 hover:border-kith/50 hover:bg-card ${
        active ? "border-kith bg-kith-soft/40 ring-2 ring-ring/40" : "bg-card/60"
      }`}
    >
      <span className="flex items-center gap-2">
        <Icon className={`size-4 shrink-0 ${active ? "text-kith" : "text-muted-foreground"}`} />
        <span className="text-sm font-semibold">{pick.headline}</span>
        {active ? <Check className="ml-auto size-4 shrink-0 text-kith" /> : null}
      </span>

      <span className="mt-2 truncate text-xs font-medium" title={model.id}>
        {shortName(model.id)}
      </span>
      <span className="text-muted-foreground mt-0.5 font-mono text-[11px]">
        {formatPrice(model.promptPerMTok)}
        <span className="text-muted-foreground/60"> in</span> ·{" "}
        {formatPrice(model.completionPerMTok)}
        <span className="text-muted-foreground/60"> out</span>
      </span>

      <span className="mt-2 flex flex-wrap gap-1">
        {model.agenticIndex !== null ? (
          <Badge title="Artificial Analysis agentic index — sustained multi-step tool use">
            agentic {model.agenticIndex.toFixed(1)}
          </Badge>
        ) : null}
        <Badge>{formatContext(model.context)} ctx</Badge>
        {model.openWeights ? <Badge>open weights</Badge> : null}
      </span>

      <span className="text-muted-foreground/85 mt-2 flex-1 text-[11px] leading-relaxed">
        {pick.reason}
      </span>
    </button>
  );
}

function Badge({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 font-mono text-[10px]"
    >
      {children}
    </span>
  );
}

function Row({
  option,
  active,
  onSelect,
}: {
  option: ModelOption;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full items-center gap-3 border-b px-3 py-2 text-left text-sm transition-colors last:border-b-0 hover:bg-accent/60 ${
        active ? "bg-kith-soft/50" : ""
      }`}
    >
      <span className="min-w-0 flex-1 truncate" title={option.id}>
        {option.id}
      </span>
      {/* Only worth saying when it's a no: unknown tool support is the normal case on
          a generic endpoint, and flagging that would cry wolf. */}
      {option.supportsTools === false ? (
        <span className="shrink-0 rounded bg-destructive/10 px-1.5 py-0.5 text-[10px] text-destructive">
          no tools
        </span>
      ) : null}
      {option.openWeights ? (
        <span className="text-muted-foreground/60 shrink-0 text-[10px]">open</span>
      ) : null}
      <span
        className="text-muted-foreground w-14 shrink-0 text-right font-mono text-xs"
        title="agentic index"
      >
        {option.agenticIndex !== null ? option.agenticIndex.toFixed(1) : "—"}
      </span>
      <span className="text-muted-foreground shrink-0 font-mono text-xs">
        {formatContext(option.context)}
      </span>
      <span className="text-muted-foreground w-16 shrink-0 text-right font-mono text-xs">
        {formatPrice(option.promptPerMTok)}
      </span>
      <span className="w-4 shrink-0">{active ? <Check className="size-4 text-kith" /> : null}</span>
    </button>
  );
}

/** "google/gemini-3.5-flash-lite" → "gemini-3.5-flash-lite": the vendor prefix is
 *  the least useful part when three cards sit side by side. */
function shortName(id: string): string {
  const slash = id.indexOf("/");
  return slash === -1 ? id : id.slice(slash + 1);
}
