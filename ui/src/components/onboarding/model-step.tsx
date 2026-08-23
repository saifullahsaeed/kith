import { useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  Gauge,
  PackageOpen,
  Search,
  Sparkles,
  Trophy,
  Wallet,
} from "lucide-react";

import {
  formatContext,
  formatPrice,
  type ModelOption,
  type Pick as ModelPick,
  type Tier,
} from "@/lib/backend";
import {
  applyFilters,
  capabilitiesOf,
  FILTERS,
  RANKINGS,
  rank,
  turnCost,
  value,
} from "@/lib/models";
import { cn } from "@/lib/utils";

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
 * Underneath is a leaderboard, and it exists because the list underneath the cards was
 * a flat catalogue with one number on it. Everything needed to answer "what should I try
 * today" is already in OpenRouter's catalogue and most of it was being thrown away on
 * arrival: a coding index and a general intelligence index beside the agentic one, Design
 * Arena's head-to-head ranks, release dates, knowledge cutoffs, max output, cache and
 * search pricing, and the provider's own description. So it is all kept now (see
 * `providers/openrouter.py`), the axis is a control rather than a decision made for you
 * (`lib/models.ts`), and the row opens to the rest — which is the part that means nobody
 * has to go and read a model page somewhere else to decide.
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
  const [ranking, setRanking] = useState(RANKINGS[0]);
  /* Requirements, not an axis — see `FILTERS`. Held as a set because they AND together: each one
   * you add is another thing the model must do, which is the whole reason they are worth having
   * beside a ranking rather than folded into it. */
  const [required, setRequired] = useState<Set<string>>(() => new Set());
  const [opened, setOpened] = useState("");

  const picks = useMemo(
    () =>
      suggested
        .map((pick) => ({ pick, model: models.find((entry) => entry.id === pick.modelId) }))
        .filter((entry): entry is { pick: ModelPick; model: ModelOption } => Boolean(entry.model)),
    [suggested, models],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    // Requirements first, then the words, then the order. Filtering before searching means the
    // "N more" count below is a count of things you could actually use.
    const capable = applyFilters(models, required);
    const pool = needle
      ? capable.filter(
          (entry) =>
            entry.id.toLowerCase().includes(needle) || entry.name.toLowerCase().includes(needle),
        )
      : capable;
    const ordered = rank(pool, ranking);
    return { rows: ordered.slice(0, MAX_ROWS), total: ordered.length, capable: capable.length };
  }, [query, models, ranking, required]);

  const toggle = (key: string) =>
    setRequired((was) => {
      const next = new Set(was);
      if (!next.delete(key)) next.add(key);
      return next;
    });

  // Only where the provider publishes measurements. On a plain OpenAI-compatible
  // endpoint there is nothing to rank by, and a leaderboard of dashes is worse than the
  // list it replaced.
  const measured = useMemo(() => models.some((one) => one.codingIndex !== null), [models]);

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
            className="focus-visible:border-ring focus-visible:ring-ring/40 w-full rounded-md border bg-transparent py-2 pr-3 pl-9 text-sm shadow-xs outline-none transition-colors focus-visible:ring-2"
            placeholder={`Search all ${models.length} models…`}
            value={query}
            spellCheck={false}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        {measured ? (
          <>
            {/* The axis, as a control. Which of these you want is the actual question —
                "best" means a different model depending on the answer, and picking one
                on your behalf is what made this a list rather than a leaderboard. */}
            <div className="mt-2.5 flex flex-wrap items-center gap-1">
              <span className="text-muted-foreground/60 me-1 text-[11px]">Rank by</span>
              {RANKINGS.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setRanking(option)}
                  className={cn(
                    "rounded-md px-2 py-1 text-[11px] transition-colors",
                    option.key === ranking.key
                      ? "bg-kith-soft text-kith font-medium"
                      : "text-muted-foreground hover:text-foreground hover:bg-accent/60",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <p className="text-muted-foreground/70 mt-1.5 text-[11px] leading-relaxed">
              {ranking.hint}
            </p>
          </>
        ) : null}

        {/* Requirements, which chain. Separate row and separate shape from Rank by on purpose:
            one of those is a choice between axes and only one can be true, these are conditions
            and any number can. Making them look alike is how a person learns the wrong thing
            about which are exclusive. */}
        <div className="mt-2.5 flex flex-wrap items-center gap-1">
          <span className="text-muted-foreground/60 me-1 text-[11px]">Must</span>
          {FILTERS.map((one) => {
            const on = required.has(one.key);
            return (
              <button
                key={one.key}
                type="button"
                title={one.hint}
                aria-pressed={on}
                onClick={() => toggle(one.key)}
                className={cn(
                  "rounded-md border px-2 py-1 text-[11px] transition-colors",
                  on
                    ? "border-kith/40 bg-kith-soft text-kith font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/60 border-transparent",
                )}
              >
                {one.label}
              </button>
            );
          })}
          {required.size > 0 ? (
            <button
              type="button"
              onClick={() => setRequired(new Set())}
              className="text-muted-foreground/60 hover:text-foreground ms-1 text-[11px] underline underline-offset-2"
            >
              clear
            </button>
          ) : null}
        </div>
        {required.size > 0 ? (
          <p className="text-muted-foreground/70 mt-1.5 text-[11px]">
            {filtered.capable} of {models.length} models do all of that.
          </p>
        ) : null}

        <div className="mt-2 overflow-hidden rounded-md border">
          {measured ? (
            <div className="text-muted-foreground/50 bg-muted/40 flex items-center gap-3 border-b px-3 py-1.5 text-[10px] font-medium tracking-wide uppercase">
              <span className="w-6 shrink-0" />
              <span className="min-w-0 flex-1">Model</span>
              <span className="w-10 shrink-0 text-right">code</span>
              <span className="w-10 shrink-0 text-right">agent</span>
              <span className="hidden w-14 shrink-0 text-right sm:inline">
                {ranking.column.label}
              </span>
              <span className="w-32 shrink-0 text-right">in / out</span>
              <span className="w-4 shrink-0" />
            </div>
          ) : null}

          <div className="max-h-[32rem] overflow-y-auto">
            {filtered.rows.length === 0 ? (
              <p className="text-muted-foreground p-4 text-center text-sm">
                {required.size > 0 && query
                  ? `Nothing matching “${query}” does all of that.`
                  : required.size > 0
                    ? "No model does all of that."
                    : `Nothing matches “${query}”.`}
              </p>
            ) : (
              filtered.rows.map((option, index) => (
                <Row
                  key={option.id}
                  option={option}
                  place={query ? null : index + 1}
                  column={ranking.column}
                  measured={measured}
                  active={option.id === selected}
                  open={opened === option.id}
                  onToggle={() => setOpened((was) => (was === option.id ? "" : option.id))}
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
        <p className="text-muted-foreground/70 mt-2 text-[11px]">
          Scores are Artificial Analysis and Design Arena, carried in OpenRouter's catalogue. Prices
          are per million tokens, from the provider.
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
      className={`hover:border-kith/50 flex flex-col rounded-xl border p-3 text-left transition-all duration-150 hover:bg-card ${
        active ? "border-kith bg-kith-soft/40 ring-ring/40 ring-2" : "bg-card/60"
      }`}
    >
      <span className="flex items-center gap-2">
        <Icon className={`size-4 shrink-0 ${active ? "text-kith" : "text-muted-foreground"}`} />
        <span className="text-sm font-semibold">{pick.headline}</span>
        {active ? <Check className="text-kith ml-auto size-4 shrink-0" /> : null}
      </span>

      <span className="mt-2 truncate text-xs font-medium" title={model.id}>
        {shortName(model.id)}
      </span>
      <Prices model={model} />

      <span className="mt-2 flex flex-wrap gap-1">
        {model.agenticIndex !== null ? (
          <Badge title="Artificial Analysis agentic index — sustained multi-step tool use">
            agentic {model.agenticIndex.toFixed(1)}
          </Badge>
        ) : null}
        {model.codingIndex !== null ? (
          <Badge title="Artificial Analysis coding index">
            coding {model.codingIndex.toFixed(1)}
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

/**
 * What a model costs, all four numbers.
 *
 * Input alone was the headline for a while and it is not the price of anything: a turn
 * bills output at four to five times input, and Kith caches every request, so most of a
 * warm round's prompt is billed at the read rate instead of the input rate. On
 * claude-opus-5 those are $5 in, $25 out, $0.50 cache read, $6.25 cache write — quoting
 * the $5 makes the dearest model in the list look like the cheapest thing on the card.
 *
 * Cache prices are omitted rather than zeroed when a provider does not quote them, which
 * is the honest reading: several cache automatically and charge nothing for it, and a
 * "$0.00" there would be a claim nobody made.
 */
export function Prices({ model }: { model: ModelOption }) {
  return (
    <span className="text-muted-foreground mt-0.5 flex flex-wrap font-mono text-[11px] leading-relaxed">
      <span className="whitespace-nowrap">
        {formatPrice(model.promptPerMTok)}
        <span className="text-muted-foreground/60"> in</span>
      </span>
      <span className="text-muted-foreground/40 px-1">·</span>
      <span className="whitespace-nowrap">
        {formatPrice(model.completionPerMTok)}
        <span className="text-muted-foreground/60"> out</span>
      </span>
      {model.cacheReadPerMTok !== null ? (
        <>
          <span className="text-muted-foreground/40 px-1">·</span>
          <span className="whitespace-nowrap" title="What a cached prompt token costs to read back">
            {formatPrice(model.cacheReadPerMTok)}
            <span className="text-muted-foreground/60"> cached</span>
          </span>
        </>
      ) : null}
      {model.cacheWritePerMTok !== null ? (
        <>
          <span className="text-muted-foreground/40 px-1">·</span>
          <span className="whitespace-nowrap" title="What it costs to put a prompt into the cache">
            {formatPrice(model.cacheWritePerMTok)}
            <span className="text-muted-foreground/60"> to cache</span>
          </span>
        </>
      ) : null}
    </span>
  );
}

/** The full price on hover, for the list rows where only in/out fit. */
function priceTitle(option: ModelOption): string {
  const parts = [
    `${formatPrice(option.promptPerMTok)} in`,
    `${formatPrice(option.completionPerMTok)} out`,
  ];
  if (option.cacheReadPerMTok !== null) {
    parts.push(`${formatPrice(option.cacheReadPerMTok)} cached read`);
  }
  if (option.cacheWritePerMTok !== null) {
    parts.push(`${formatPrice(option.cacheWritePerMTok)} cache write`);
  }
  return `${parts.join(" · ")} — per million tokens`;
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
  place,
  column,
  measured,
  active,
  open,
  onToggle,
  onSelect,
}: {
  option: ModelOption;
  /** Its position on the current ranking, or null while searching — a "#3" beside a
   *  search hit would be a rank among the matches, which is a number about your query. */
  place: number | null;
  column: { label: string; of: (model: ModelOption) => string };
  measured: boolean;
  active: boolean;
  open: boolean;
  onToggle: () => void;
  onSelect: () => void;
}) {
  return (
    <div className={cn("border-b last:border-b-0", active && "bg-kith-soft/50")}>
      <div className="hover:bg-accent/60 flex items-center gap-3 px-3 text-sm transition-colors">
        {/* Two targets in one row: the name picks the model, the chevron opens it. The
            row used to be one button, which meant reading about a model and choosing it
            were the same click. */}
        <button
          type="button"
          onClick={onSelect}
          className="flex min-w-0 flex-1 items-center gap-3 py-2 text-left"
        >
          <span className="text-muted-foreground/40 w-6 shrink-0 text-right font-mono text-[11px] tabular-nums">
            {place ?? ""}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate" title={option.id}>
              {option.name || option.id}
            </span>
            <span className="text-muted-foreground/50 flex items-center gap-1.5 text-[10px]">
              <span className="truncate font-mono">{option.id}</span>
              {/* What it can be given and what it can do, on the row rather than one model at a
                  time behind the chevron. The catalogue has carried all of this since before the
                  picker was written; comparing two models on it meant opening both. */}
              {option.supportsTools === false ? (
                <span className="text-destructive shrink-0" title="Cannot call tools — Kith is a loop around tool calls, so this model cannot do the job">
                  no tools
                </span>
              ) : null}
              {capabilitiesOf(option)
                .filter((can) => can !== "tools")
                .map((can) => (
                  <span
                    key={can}
                    className="border-border/50 text-muted-foreground/60 shrink-0 rounded border px-1 leading-[1.3]"
                    title={CAN_MEANS[can]}
                  >
                    {can}
                  </span>
                ))}
              {option.context ? (
                <span className="shrink-0 tabular-nums" title="Context window">
                  {formatContext(option.context)} ctx
                </span>
              ) : null}
              {option.retiresOn ? (
                <span className="shrink-0 text-orange-400" title="The provider intends to withdraw it on this date">
                  retires {option.retiresOn}
                </span>
              ) : null}
            </span>
          </span>
        </button>

        {measured ? (
          <>
            <Score value={option.codingIndex} title="Coding index" />
            <Score value={option.agenticIndex} title="Agentic index" />
            <span className="text-muted-foreground hidden w-14 shrink-0 text-right font-mono text-xs tabular-nums sm:inline">
              {column.of(option)}
            </span>
          </>
        ) : null}
        <span
          className="text-muted-foreground w-32 shrink-0 text-right font-mono text-xs whitespace-nowrap"
          title={priceTitle(option)}
        >
          {formatPrice(option.promptPerMTok)}
          <span className="text-muted-foreground/50"> / </span>
          {formatPrice(option.completionPerMTok)}
        </span>
        <button
          type="button"
          onClick={onToggle}
          aria-label={open ? "Hide the details" : "Show the details"}
          aria-expanded={open}
          className="text-muted-foreground/40 hover:text-foreground -me-1 shrink-0 p-1 transition-colors"
        >
          <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
        </button>
      </div>

      {open ? <Detail option={option} active={active} onSelect={onSelect} /> : null}
    </div>
  );
}

function Score({ value: score, title }: { value: number | null; title: string }) {
  return (
    <span
      title={title}
      className={cn(
        "w-10 shrink-0 text-right font-mono text-xs tabular-nums",
        score === null ? "text-muted-foreground/30" : "text-muted-foreground",
      )}
    >
      {score === null ? "—" : score.toFixed(1)}
    </span>
  );
}

/** Everything else the provider says about it — the part that was being fetched and
 *  dropped, and the reason this panel can answer a question without a browser. */
function Detail({
  option,
  active,
  onSelect,
}: {
  option: ModelOption;
  active: boolean;
  onSelect: () => void;
}) {
  const cost = turnCost(option);
  const points = value(option);
  const facts: [string, string][] = [
    ["Context", formatContext(option.context)],
    ["Max reply", option.maxOutput ? formatContext(option.maxOutput) : "—"],
    ["Knows up to", option.knowledgeCutoff || "—"],
    ["Released", option.releasedOn ?? "—"],
    // The blended figure the value ranking is built on, shown wherever that ranking
    // could have sent someone — a score you cannot see the arithmetic of is a claim.
    ["A turn costs", cost === null ? "—" : `${formatPrice(cost)}/Mtok`],
    ["Coding per $", points === null ? "—" : points.toFixed(1)],
  ];

  return (
    <div className="bg-muted/25 space-y-2.5 border-t px-3 py-2.5">
      {option.description ? (
        <p className="text-muted-foreground text-[11px] leading-relaxed">{option.description}</p>
      ) : null}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
        {facts.map(([label, fact]) => (
          <div key={label} className="flex items-baseline justify-between gap-2">
            <dt className="text-muted-foreground/60 text-[10px]">{label}</dt>
            <dd className="font-mono text-[11px] tabular-nums">{fact}</dd>
          </div>
        ))}
      </dl>

      {option.arena ? (
        <p className="text-muted-foreground flex items-center gap-1.5 text-[11px]">
          <Trophy className="size-3 shrink-0 text-orange-400" />
          Design Arena: ranked #{option.arena.rank} at {option.arena.category}
          {option.arena.winRate !== null
            ? ` — wins ${option.arena.winRate.toFixed(0)}% of them`
            : ""}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-1">
        <Prices model={option} />
        {option.webSearchPerCall ? (
          <Badge title="Charged per search, on top of tokens">
            {formatPrice(option.webSearchPerCall)}/search
          </Badge>
        ) : null}
        {option.supportsReasoning ? <Badge>reasoning</Badge> : null}
        {option.supportsImages ? <Badge>images</Badge> : null}
        {option.supportsFiles ? <Badge>files</Badge> : null}
      </div>

      <button
        type="button"
        onClick={onSelect}
        disabled={active}
        className={cn(
          "rounded-md border px-2 py-1 text-[11px] transition-colors",
          active
            ? "border-kith/40 text-kith cursor-default"
            : "hover:border-kith/50 hover:bg-card text-muted-foreground hover:text-foreground",
        )}
      >
        {active ? "Chosen" : "Use this model"}
      </button>
    </div>
  );
}

/** "google/gemini-3.5-flash-lite" → "gemini-3.5-flash-lite": the vendor prefix is
 *  the least useful part when three cards sit side by side. */
function shortName(id: string): string {
  const slash = id.indexOf("/");
  return slash === -1 ? id : id.slice(slash + 1);
}

/** What each capability word means, for the hover. The words are short so a row stays scannable;
 *  the sentence is here so short does not mean cryptic. */
const CAN_MEANS: Record<string, string> = {
  images: "Takes pictures as input — a screenshot, a diagram, a photo",
  files: "Takes files directly, rather than needing their text pasted in",
  reasoning: "Has a thinking budget the effort control can actually move",
  open: "Weights are published, so it can outlive whoever serves it today",
};
