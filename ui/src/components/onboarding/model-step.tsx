import { useMemo, useState } from "react";
import { Check, Search, Sparkles } from "lucide-react";

import { formatContext, formatPrice, type ModelOption } from "@/lib/backend";

/** A catalogue can run to hundreds of entries. Rendering them all costs a visible
 *  frame drop on the first keystroke, and nobody scrolls past forty. */
const MAX_ROWS = 40;

/**
 * Choosing a model out of a list that might be three long or four hundred.
 *
 * The suggestions on top are a price ladder from the server, not a quality ranking —
 * a catalogue reports price, context and tool support, none of which measure how well
 * a model reasons. Showing three spread across the range is the honest version, and it
 * means the common case is one click without touching the search box.
 */
export function ModelStep({
  models,
  suggested,
  selected,
  onSelect,
}: {
  models: ModelOption[];
  suggested: string[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");

  const picks = useMemo(
    () =>
      suggested
        .map((id) => models.find((entry) => entry.id === id))
        .filter(Boolean) as ModelOption[],
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
        <div>
          <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Sparkles className="size-3.5 text-kith" />A few to start with, cheapest first
          </p>
          <div className="grid gap-2 sm:grid-cols-3">
            {picks.map((option) => (
              <SuggestionCard
                key={option.id}
                option={option}
                active={option.id === selected}
                onSelect={() => onSelect(option.id)}
              />
            ))}
          </div>
        </div>
      ) : null}

      <div>
        <div className="relative">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
          <input
            className="w-full rounded-md border bg-transparent py-2 pr-3 pl-9 text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
            placeholder={`Search ${models.length} models…`}
            value={query}
            spellCheck={false}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        <div className="mt-2 max-h-64 overflow-y-auto rounded-md border">
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
      </div>
    </div>
  );
}

function SuggestionCard({
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
      className={`flex flex-col rounded-lg border p-3 text-left transition-all duration-150 hover:border-kith/50 hover:bg-card ${
        active ? "border-kith bg-kith-soft/40 ring-2 ring-ring/40" : "bg-card/60"
      }`}
    >
      <span className="flex items-start justify-between gap-2">
        <span className="truncate text-sm font-medium" title={option.id}>
          {shortName(option.id)}
        </span>
        {active ? <Check className="mt-0.5 size-4 shrink-0 text-kith" /> : null}
      </span>
      <span className="text-muted-foreground mt-1 font-mono text-[11px]">
        {formatPrice(option.promptPerMTok)}
        <span className="text-muted-foreground/60"> in</span> ·{" "}
        {formatPrice(option.completionPerMTok)}
        <span className="text-muted-foreground/60"> out /Mtok</span>
      </span>
      <span className="text-muted-foreground/70 mt-0.5 text-[11px]">
        {formatContext(option.context)} context
      </span>
    </button>
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
