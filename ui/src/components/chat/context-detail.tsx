import { useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, Copy, Layers, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DroppedSince, MessageExplorer } from "@/components/chat/context-messages";
import {
  fetchCategoryText,
  fetchContextDetail,
  type ContextDetail,
  type ContextItem,
  type ContextLine,
  type LastRound as LastRoundFigures,
} from "@/lib/backend";
import { foldNow } from "@/lib/commands";
import { copyText } from "@/lib/files";
import { byGroup, FOLDS_AT, GROUP_OF, type GroupReading } from "@/lib/context-groups";
import { formatCompact, formatTokens } from "@/lib/tokens";
import { cn } from "@/lib/utils";

/**
 * What is in the window, and which of it is there twice.
 *
 * The meter in the rail answers "how full", in five colours and about sixty pixels. That is the
 * right size for a thing you glance at every turn, and the wrong size for the question you
 * actually open it with, which is what to do about it. "Code he has read: 831k" is a number, not
 * an answer — 831k of files he needed once and 831k of one file read sixty times are the same
 * figure and completely different problems, and only the second is something you can act on.
 *
 * **The shape of the page is three questions, not one scroll.** It used to stack eight sections
 * down a single column, with the message explorer — a 62vh two-pane — sitting in the middle of
 * them, so everything past it (the repeats, the breakdown, what he had read) was below a screen
 * and a half of list and effectively invisible. Now an always-on overview answers "how full and
 * made of what", and three tabs answer the three real questions a person opens this with: what is
 * being sent (the prompt), where the weight went (the breakdown), and what is paid for twice
 * (repeats). Each tab owns the height instead of fighting the others for it.
 *
 * Fetched when the screen opens, never held. See `lib/backend/context` for why this is a request
 * of its own rather than more fields on the reading every turn already streams.
 */
export function ContextDetailScreen({
  conversationId,
  onClose,
}: {
  conversationId: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<ContextDetail | null>(null);
  const [folding, setFolding] = useState(false);
  const [note, setNote] = useState("");
  const [tab, setTab] = useState<TabKey>("prompt");

  const load = useMemo(
    () => () => fetchContextDetail(conversationId).then(setDetail),
    [conversationId],
  );
  useEffect(() => {
    void load();
  }, [load]);

  // Esc closes, as it does on every other thing that covers the thread.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const fold = async () => {
    setFolding(true);
    setNote("Folding…");
    const result = await foldNow(conversationId);
    setNote(result.note);
    // Re-read rather than patching what is on screen: a fold rewrites the conversation, so every
    // item is now a different item and adjusting the totals in place would leave the breakdown
    // describing reads that are no longer in the window.
    await load();
    setFolding(false);
  };

  const groups = detail ? byGroup(detail.lines, detail.used) : [];
  const repeated = detail ? detail.items.filter((item) => item.wasted > 0) : [];

  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-background text-foreground">
      <div className="kith-ambient opacity-70" />

      <header className="window-drag-region window-controls-gap relative z-10 flex items-center gap-3 border-b border-border/60 bg-background/70 px-4 py-2.5 backdrop-blur-xl">
        <span className="bg-kith-soft text-kith ring-kith/20 relative flex size-8 shrink-0 items-center justify-center rounded-xl ring-1">
          <Layers className="size-4" />
        </span>
        <div className="min-w-0 leading-tight">
          <div className="text-sm font-semibold tracking-tight">Context</div>
          <div className="text-muted-foreground hidden text-[11px] sm:block">
            every message his next turn sends, and what each one costs
          </div>
        </div>
        <div className="flex-1" />
        {detail?.reading ? (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground gap-1.5"
            onClick={fold}
            disabled={folding}
            title="Summarise the older turns now, instead of waiting for the window to fill"
          >
            {folding ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Layers className="size-3.5" />
            )}
            Fold now
          </Button>
        ) : null}
        <Button
          variant="ghost"
          size="icon"
          className="size-8 hover:text-destructive"
          onClick={onClose}
          aria-label="Close"
        >
          <X className="size-4" />
        </Button>
      </header>

      {detail === null ? (
        <div className="relative z-10 flex-1 overflow-y-auto px-6 py-8 xl:px-10">
          <Waiting />
        </div>
      ) : !detail.reading ? (
        <div className="relative z-10 flex-1 overflow-y-auto px-6 py-8 xl:px-10">
          <NothingMeasuredYet items={detail.items} />
        </div>
      ) : (
        <>
          {/* Always on. The glance the whole screen used to open with, kept in view while you
              read the tabs below it — so "how full, and made of what" never scrolls away while
              you are three hundred rows into the prompt. */}
          <Overview
            detail={detail}
            groups={groups}
            note={note}
            onPickGroup={() => setTab("where")}
          />

          <Tabs
            tab={tab}
            onTab={setTab}
            counts={{
              prompt: detail.sent.messages.length,
              where: detail.used,
              reuse: detail.wasted,
            }}
          />

          {/* The one scroll region on the page. The overview and the tab strip hold their
              height; only the answer to the selected question scrolls. */}
          <main className="relative z-10 min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto flex max-w-[112rem] flex-col gap-6 px-6 py-6 xl:px-10">
              {tab === "prompt" ? (
                <>
                  {detail.sent.foldPending ? <FoldPending /> : null}
                  {/* A caveat on the list rather than a section of it: these were sent and are
                      not among the rows below. */}
                  <Directives texts={detail.directives} />
                  <MessageExplorer conversationId={conversationId} sent={detail.sent} />
                  {/* The other half of context management. A screen that only ever grows would
                      explain how a window fills and nothing about how it is kept from filling. */}
                  <DroppedSince sent={detail.sent} />
                </>
              ) : null}

              {tab === "where" ? (
                <Breakdown
                  conversationId={conversationId}
                  groups={groups}
                  lines={detail.lines}
                />
              ) : null}

              {tab === "reuse" ? (
                <>
                  <Repeats detail={detail} repeated={repeated} />
                  <WhatHeRead items={detail.items} />
                </>
              ) : null}
            </div>
          </main>
        </>
      )}
    </div>
  );
}

type TabKey = "prompt" | "where" | "reuse";

/**
 * The three questions the page answers, as a strip you navigate by.
 *
 * Each tab carries its own headline figure, so the strip is also a summary: you can see that the
 * prompt is 22 messages and that 843k is being paid for twice without opening either. Reuse only
 * earns a figure when there is waste to name — a "0" there would read as a finding rather than a
 * clean conversation.
 */
function Tabs({
  tab,
  onTab,
  counts,
}: {
  tab: TabKey;
  onTab: (tab: TabKey) => void;
  counts: { prompt: number; where: number; reuse: number };
}) {
  const items: { key: TabKey; label: string; figure: string | null; tone?: boolean }[] = [
    { key: "prompt", label: "The prompt", figure: `${counts.prompt} msg` },
    { key: "where", label: "Where it went", figure: formatTokens(counts.where) },
    {
      key: "reuse",
      label: "Repeats & reuse",
      figure: counts.reuse > 0 ? formatCompact(counts.reuse) : null,
      tone: counts.reuse > 0,
    },
  ];
  return (
    <nav className="relative z-10 border-b border-border/60 bg-background/40">
      <div className="mx-auto flex max-w-[112rem] gap-1 px-6 xl:px-10">
        {items.map((item) => {
        const active = tab === item.key;
        return (
          <button
            key={item.key}
            type="button"
            onClick={() => onTab(item.key)}
            aria-current={active}
            className={cn(
              "-mb-px flex items-center gap-2 border-b-2 px-3 py-2.5 text-[13px] transition-colors",
              active
                ? "border-kith text-foreground"
                : "border-transparent text-muted-foreground/70 hover:text-foreground",
            )}
          >
            <span className="font-medium">{item.label}</span>
            {item.figure ? (
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 font-mono text-[10px] tabular-nums",
                  item.tone
                    ? "bg-destructive/15 text-destructive/80"
                    : active
                      ? "bg-kith-soft text-kith"
                      : "bg-muted text-muted-foreground/70",
                )}
              >
                {item.figure}
              </span>
            ) : null}
          </button>
          );
        })}
      </div>
    </nav>
  );
}

/**
 * How full, what it is made of, and what the last round actually cost — the glance, in one band.
 *
 * The old screen answered these in three separate stacked blocks (a big percentage, a legend-less
 * bar, and a receipt card) that between them owned most of the first screenful. They are one
 * thought — "here is the state of the window" — so they are one band now, and it stays put while
 * the tabs scroll under it.
 *
 * The composition bar finally carries a key. For the whole life of this screen the five-colour
 * bar had no labels at all, which is the one thing a stacked bar cannot do without: a colour with
 * no name is decoration. Each entry names its group, its size and its share, and clicking one
 * opens the full breakdown — the colour is now a way in, not a riddle.
 */
function Overview({
  detail,
  groups,
  note,
  onPickGroup,
}: {
  detail: ContextDetail;
  groups: GroupReading[];
  note: string;
  onPickGroup: () => void;
}) {
  const percent = Math.round(detail.share * 100);
  const nearFold = detail.share >= FOLDS_AT;

  return (
    <section className="relative z-10 border-b border-border/60 bg-background/50 px-6 py-4 backdrop-blur-sm xl:px-10">
      <div className="mx-auto flex max-w-[112rem] flex-col gap-3.5">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          <div className="flex items-baseline gap-2.5">
            <span
              className={cn(
                "text-3xl font-semibold tracking-tight tabular-nums",
                nearFold && "text-kith",
              )}
            >
              {percent}%
            </span>
            <span className="text-muted-foreground/80 font-mono text-[12px] tabular-nums">
              {formatTokens(detail.used)} of {formatTokens(detail.window)}
              <span className="text-muted-foreground/40">
                {" "}
                · {formatTokens(detail.free)} free
              </span>
            </span>
          </div>
          <div className="flex-1" />
          <Receipt round={detail.lastRound} />
        </div>

        {/* The composition, at a size worth reading. A 2px gap between segments because adjacent
            fills of similar lightness read as one block without one — the secondary encoding the
            palette's light-mode contrast warn obliges. No fold marker: the bar's width is what is
            *used*, not the window, so a threshold measured against the window has nowhere to sit
            on it. */}
        <button
          type="button"
          onClick={onPickGroup}
          aria-label="Open the full breakdown"
          className="focus-visible:ring-ring/50 flex h-3 w-full gap-0.5 overflow-hidden rounded-full focus-visible:ring-2 focus-visible:outline-none"
        >
          {groups.map((group) => (
            <span
              key={group.key}
              className={cn(group.swatch, "first:rounded-s-full last:rounded-e-full")}
              style={{ width: `${group.part * 100}%` }}
            />
          ))}
        </button>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          {groups.map((group) => (
            <button
              key={group.key}
              type="button"
              onClick={onPickGroup}
              title={group.hint}
              className="group/leg flex items-baseline gap-1.5 text-[11px]"
            >
              <span aria-hidden className={cn("size-2 shrink-0 translate-y-px rounded-[2px]", group.swatch)} />
              <span className="text-muted-foreground group-hover/leg:text-foreground transition-colors">
                {group.label}
              </span>
              <span className="font-mono tabular-nums">{formatTokens(group.tokens)}</span>
              <span className="text-muted-foreground/45 font-mono tabular-nums">
                {Math.round(group.part * 100)}%
              </span>
            </button>
          ))}
          <span className="text-muted-foreground/50 ms-auto text-[11px]">
            {note ||
              (detail.folded
                ? "Earlier steps were folded into notes to make room."
                : nearFold
                  ? "Near the fold — earlier steps will be summarised to make room."
                  : `Folds at ${Math.round(FOLDS_AT * 100)}%.`)}
          </span>
        </div>
      </div>
    </section>
  );
}

/**
 * What the provider actually billed for the last round.
 *
 * The only figures on this screen that are measured rather than derived: everything else is
 * `message_chars` over a ratio, and these came back from the provider. Kept as its own cluster so
 * a receipt never gets mistaken for an estimate.
 */
function Receipt({ round }: { round: LastRoundFigures | null }) {
  if (!round) return null;
  const cachedShare = round.promptTokens
    ? Math.round((round.cachedTokens / round.promptTokens) * 100)
    : 0;
  return (
    <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1.5">
      <Figure label="last round" value={formatTokens(round.promptTokens)} unit="in" />
      <Figure
        label="from cache"
        value={formatTokens(round.cachedTokens)}
        unit={cachedShare ? `${cachedShare}%` : "none"}
      />
      <Figure label="written" value={formatTokens(round.responseTokens)} unit="out" />
      <Figure label="cost" value={`$${round.costUsd.toFixed(5)}`} unit="" />
      <div className="leading-tight">
        <div className="text-muted-foreground/40 text-[9px] tracking-[0.12em] uppercase">model</div>
        <div className="max-w-[16rem] truncate font-mono text-[11px]" title={round.model || "—"}>
          {round.model || "—"}
        </div>
      </div>
    </div>
  );
}

function Figure({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div className="leading-tight">
      <div className="text-muted-foreground/40 text-[9px] tracking-[0.12em] uppercase">{label}</div>
      <div className="flex items-baseline gap-1">
        <span className="font-mono text-[13px] tabular-nums">{value}</span>
        {unit ? (
          <span className="text-muted-foreground/50 font-mono text-[10px]">{unit}</span>
        ) : null}
      </div>
    </div>
  );
}

/**
 * What the harness said to the turn, which the list cannot show.
 *
 * A directive — the landing nudge, a round that died, one that came back empty, the budget
 * running out — is appended to the round's own message list and to nothing else. So it is really
 * sent, and it is not in the transcript the list is rebuilt from. Nothing at all when the last
 * turn ran clean, which is most of them — an empty panel headed "Directives" would imply the
 * absence is a finding.
 */
function Directives({ texts }: { texts: string[] }) {
  if (texts.length === 0) return null;
  return (
    <section className="border-border/60 bg-muted/30 space-y-2 rounded-lg border px-4 py-3">
      <h3 className="text-[12px] font-medium">
        The harness also said {texts.length === 1 ? "this" : `these ${texts.length} things`} to the
        last turn
      </h3>
      <p className="text-muted-foreground max-w-prose text-[11px]">
        Sent as system messages inside the turn and not kept in the transcript, so they are not in
        the list below — they are counted under “Turn directives” in Where it went.
      </p>
      <ul className="space-y-1.5">
        {texts.map((text, index) => (
          <li
            key={index}
            className="text-muted-foreground border-border/50 border-l-2 py-0.5 pl-3 font-mono text-[11px] leading-relaxed"
          >
            {text}
          </li>
        ))}
      </ul>
    </section>
  );
}

function FoldPending() {
  return (
    <p className="border-border/60 bg-muted/30 text-muted-foreground rounded-lg border px-4 py-2.5 text-[12px]">
      This conversation is past the fold threshold, so the next turn will summarise its older
      turns before sending. The list below is what would go without that — reading it does not
      trigger a fold, because a screen should not spend money to draw itself.
    </p>
  );
}

function Waiting() {
  return (
    <div className="text-muted-foreground/60 flex items-center gap-2 py-16 text-sm">
      <Loader2 className="size-4 animate-spin" />
      Counting what is in the window…
    </div>
  );
}

/**
 * A conversation no turn has measured. Deliberately not a chart at 0% — that reads as an answer
 * ("the window is empty") when the truth is that nothing has looked yet. A reading is taken by a
 * turn, so the first one belongs to the next thing you send.
 */
function NothingMeasuredYet({ items }: { items: ContextItem[] }) {
  return (
    <div className="flex flex-col gap-4 py-12">
      <p className="text-sm font-medium">Nothing has measured this conversation yet.</p>
      <p className="text-muted-foreground max-w-prose text-[13px] leading-relaxed">
        A reading of the window is taken by a turn, on its way past — so the first one arrives with
        the next thing you send. What he has already read is below either way.
      </p>
      {items.length > 0 ? <ItemList items={items} /> : null}
    </div>
  );
}

/**
 * The headline of the reuse tab, and the reason this screen exists.
 *
 * Led with rather than buried under the categories, because it is the only figure here that is a
 * decision rather than a fact. Everything else describes what the window costs; this describes
 * what it costs for nothing.
 */
function Repeats({ detail, repeated }: { detail: ContextDetail; repeated: ContextItem[] }) {
  if (repeated.length === 0) {
    return (
      <section className="border-border/60 bg-card/40 rounded-xl border p-5">
        <p className="text-[13px] font-medium">Nothing is being paid for twice.</p>
        <p className="text-muted-foreground/70 mt-1 max-w-prose text-[12px] leading-relaxed">
          No file, skill, or search in this conversation has been read more than once — so every
          token below is carrying something the window does not already hold.
        </p>
      </section>
    );
  }

  // Of what he has read, never of the window. They are different measurements — the window is
  // the folded request, this is the whole transcript — and dividing one by the other is what
  // printed "121% of the window" on a conversation whose history is four times its window.
  const share = detail.itemsTotal ? Math.round((detail.wasted / detail.itemsTotal) * 100) : 0;

  return (
    <section className="border-border/60 bg-card/40 flex flex-col gap-3 rounded-xl border p-5">
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-semibold tracking-tight tabular-nums">
          {formatCompact(detail.wasted)}
        </span>
        <span className="text-[13px] font-medium">tokens re-read</span>
        {share > 0 ? (
          <span className="text-muted-foreground/70 font-mono text-[12px] tabular-nums">
            {share}% of everything he has read
          </span>
        ) : null}
      </div>
      <p className="text-muted-foreground max-w-prose text-[12px] leading-relaxed">
        The same thing looked at more than once, across this conversation. Every copy but the newest
        describes it as it was before whatever happened since. Folding may already have cleared some
        of these from the window — the habit is the point, since it is what refills it.
      </p>
      <ItemList items={repeated.slice(0, 12)} />
      {repeated.length > 12 ? (
        <p className="text-muted-foreground/50 text-[11px]">
          and {repeated.length - 12} more repeated {repeated.length - 12 === 1 ? "call" : "calls"}
        </p>
      ) : null}
    </section>
  );
}

/**
 * Every call he made, grouped by what kind of thing it was.
 *
 * Its own section rather than nested under the window's categories, because it is not a breakdown
 * of them — see `lib/backend/context`. Same reason it does not show a percentage: a share of the
 * window is exactly the number that was wrong.
 */
function WhatHeRead({ items }: { items: ContextItem[] }) {
  if (items.length === 0) return null;
  const kinds = [
    { key: "code", label: "Code and searches" },
    { key: "skills", label: "Skills" },
    { key: "tool_results", label: "Everything else" },
  ];

  return (
    <section className="flex flex-col gap-5">
      <div>
        <h2 className="text-muted-foreground/60 text-[11px] font-semibold tracking-[0.14em] uppercase">
          What he has read
        </h2>
        <p className="text-muted-foreground/50 mt-1 text-[11px]">
          across this whole conversation, not only what is still in the window
        </p>
      </div>
      {kinds.map((kind) => {
        const mine = items.filter((item) => item.key === kind.key);
        if (mine.length === 0) return null;
        return (
          <Category
            key={kind.key}
            label={kind.label}
            tokens={mine.reduce((sum, item) => sum + item.tokens, 0)}
            items={mine}
          />
        );
      })}
    </section>
  );
}

/**
 * Every category in the window, under the group that owns it.
 *
 * Flat, with nothing to expand. The first version opened each category into the calls that filled
 * it, which was a promise it could not keep: the calls are the whole transcript and the categories
 * are the folded request, so the rows underneath added up to four times the row above them.
 */
function Breakdown({
  conversationId,
  groups,
  lines,
}: {
  conversationId: string;
  groups: GroupReading[];
  lines: ContextLine[];
}) {
  // Every group open to begin with, so nothing is hidden on the first look — the collapse is
  // there to quiet the parts you have finished reading, not to make you hunt for the parts you
  // have not. Same gesture as the turns in the prompt tab, so the two tabs read the same way.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const allOpen = collapsed.size === 0;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
        <div>
          <h2 className="text-muted-foreground/60 text-[11px] font-semibold tracking-[0.14em] uppercase">
            Where it went
          </h2>
          <p className="text-muted-foreground/50 mt-1 text-[11px]">
            what the last turn actually sent — the same figures as the meter in the rail
          </p>
        </div>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="xs"
          className="text-muted-foreground/70 hover:text-foreground h-6 gap-1.5 px-2 text-[11px]"
          onClick={() =>
            setCollapsed(allOpen ? new Set(groups.map((group) => group.key)) : new Set())
          }
        >
          {allOpen ? "Collapse all" : "Expand all"}
        </Button>
      </div>
      <div className="border-border/60 divide-border/50 flex flex-col divide-y rounded-xl border">
        {groups.map((group) => {
          const open = !collapsed.has(group.key);
          const children = lines
            .filter((line) => groupKeyOf(line.key) === group.key)
            .sort((a, b) => b.tokens - a.tokens);
          return (
            <div key={group.key} className="flex flex-col">
              <button
                type="button"
                aria-expanded={open}
                onClick={() =>
                  setCollapsed((was) => {
                    const next = new Set(was);
                    if (next.has(group.key)) next.delete(group.key);
                    else next.add(group.key);
                    return next;
                  })
                }
                className="hover:bg-accent/30 flex items-center gap-2 px-3 py-2.5 text-left transition-colors"
              >
                <ChevronRight
                  aria-hidden
                  className={cn(
                    "text-muted-foreground/40 size-3.5 shrink-0 transition-transform",
                    open && "rotate-90",
                  )}
                />
                <span aria-hidden className={cn("size-2.5 shrink-0 rounded-[3px]", group.swatch)} />
                <span className="text-[13px] font-medium">{group.label}</span>
                {/* The count of what folds under it — so a collapsed group still says how much
                    it is hiding, the same way a collapsed turn shows its message count. */}
                <span className="text-muted-foreground/35 shrink-0 text-[11px] tabular-nums">
                  {children.length} {children.length === 1 ? "line" : "lines"}
                </span>
                <span className="text-muted-foreground/70 ms-auto shrink-0 font-mono text-[12px] tabular-nums">
                  {formatTokens(group.tokens)}
                </span>
                <span className="text-muted-foreground/40 w-9 shrink-0 text-end font-mono text-[12px] tabular-nums">
                  {Math.round(group.part * 100)}%
                </span>
              </button>
              {open ? (
                <div className="px-3 pb-3">
                  <p className="text-muted-foreground/50 ps-6 pb-1.5 text-[11px]">{group.hint}</p>
                  <div className="ps-6">
                    {children.map((line) => (
                      <TextCategory
                        key={line.key}
                        conversationId={conversationId}
                        textKey={line.key}
                        label={line.label}
                        tokens={line.tokens}
                      />
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/**
 * One breakdown line, opening to the literal text it is made of.
 *
 * The number said how much a category weighs; this says what the weight *is* — the persona in
 * full, the tool declarations, the block rewritten each turn. Fetched only on open, because
 * "messages" is the whole conversation and nobody wants a megabyte to draw a row they may not
 * expand. The text is the same rebuild the count came from, so what you read matches the figure
 * above it; the tool schemas are the current declarations, which are stable text a person opens
 * to see what a tool is.
 */
function TextCategory({
  conversationId,
  textKey,
  label,
  tokens,
}: {
  conversationId: string;
  textKey: string;
  label: string;
  tokens: number;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const openIt = async () => {
    const next = !open;
    setOpen(next);
    if (next && text === null && !loading) {
      setLoading(true);
      const result = await fetchCategoryText(conversationId, textKey);
      setText(result.text);
      setTruncated(result.truncated);
      setLoading(false);
    }
  };

  const copy = async () => {
    if (!text) return;
    await copyText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="border-border/40 border-b last:border-b-0">
      <button
        type="button"
        onClick={openIt}
        aria-expanded={open}
        className="hover:bg-accent/40 -mx-2 flex w-[calc(100%+1rem)] items-center gap-2 rounded-md px-2 py-1.5 transition-colors"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "text-muted-foreground/40 size-3 shrink-0 transition-transform",
            open && "rotate-90",
          )}
        />
        <span className="text-muted-foreground min-w-0 flex-1 truncate text-start text-[12px]">
          {label}
        </span>
        <span className="text-muted-foreground/60 w-16 shrink-0 text-end font-mono text-[11px] tabular-nums">
          {formatTokens(tokens)}
        </span>
      </button>
      {open ? (
        <div className="pb-2 ps-5">
          {loading ? (
            <div className="text-muted-foreground/50 flex items-center gap-2 py-2 text-[11px]">
              <Loader2 className="size-3 animate-spin" />
              Reading it…
            </div>
          ) : text ? (
            <div className="border-border/50 bg-card/40 relative rounded-lg border">
              {/* The copy is the whole point of being able to see it — you read it here, you
                  take it elsewhere. Sits over the text rather than in a header bar, which would
                  cost a row on every one of these. */}
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={copy}
                title="Copy the text"
                className="text-muted-foreground/60 hover:text-foreground absolute inset-e-1.5 top-1.5 z-10"
              >
                {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
              </Button>
              <pre className="text-muted-foreground/90 max-h-112 overflow-auto px-3 py-2.5 pe-9 font-mono text-[11px] leading-relaxed whitespace-pre-wrap wrap-break-word">
                {text}
              </pre>
              {truncated ? (
                <p className="text-muted-foreground/40 border-border/40 border-t px-3 py-1.5 text-[10px] italic">
                  Shown up to the display limit — Copy takes the whole thing.
                </p>
              ) : null}
            </div>
          ) : (
            <p className="text-muted-foreground/40 py-2 text-[11px] italic">
              Nothing to show — this line has no text of its own.
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** One of the ledger's lines, opening to the calls that filled it (What he read). Persona, the
 *  system prompt and the tool schemas come from config rather than the conversation, so there is
 *  nothing underneath them and a disclosure arrow would be a promise the screen cannot keep. */
function Category({
  label,
  tokens,
  items,
}: {
  label: string;
  tokens: number;
  items: ContextItem[];
}) {
  const [open, setOpen] = useState(false);
  const openable = items.length > 0;

  const row = (
    <>
      <ChevronRight
        aria-hidden
        className={cn(
          "text-muted-foreground/40 size-3 shrink-0 transition-transform",
          !openable && "invisible",
          open && "rotate-90",
        )}
      />
      <span className="text-muted-foreground min-w-0 flex-1 truncate text-start text-[12px]">
        {label}
      </span>
      {openable ? (
        <span className="text-muted-foreground/40 shrink-0 text-[11px] tabular-nums">
          {items.length} {items.length === 1 ? "call" : "calls"}
        </span>
      ) : null}
      <span className="text-muted-foreground/60 w-16 shrink-0 text-end font-mono text-[11px] tabular-nums">
        {formatTokens(tokens)}
      </span>
    </>
  );

  return (
    <div className="border-border/40 border-b last:border-b-0">
      {openable ? (
        <button
          type="button"
          onClick={() => setOpen((was) => !was)}
          aria-expanded={open}
          className="hover:bg-accent/40 -mx-2 flex w-[calc(100%+1rem)] items-center gap-2 rounded-md px-2 py-1.5 transition-colors"
        >
          {row}
        </button>
      ) : (
        <div className="flex items-center gap-2 px-0 py-1.5">{row}</div>
      )}
      {open ? (
        <div className="pb-2 ps-5">
          {/* Capped, and the cap is stated. One real conversation itemises to 2,541 calls, of
              which 1,056 land in a single category — rendering all of them is a long freeze for
              a list nobody reads past the top of, since it is sorted worst-first. A cap that
              said nothing would be worse than the freeze: the section would look complete. */}
          <ItemList items={items.slice(0, ITEM_CAP)} />
          {items.length > ITEM_CAP ? (
            <p className="text-muted-foreground/40 py-1 text-[11px]">
              showing the {ITEM_CAP} largest of {formatTokens(items.length)} calls
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The calls themselves.
 *
 * A subject is shortened from the *front*: the end of a path is what identifies the file, and
 * forty identical characters of home directory followed by "…" names nothing.
 *
 * Done in JavaScript rather than with `dir="rtl"`, which is the usual trick and was wrong here.
 * Under RTL a leading "/" is a neutral character, so the bidi algorithm moves it to the visual
 * end — every absolute path rendered as `Users/…/src/App.tsx/`, with the slash migrated to the
 * far side and the real one missing. A path that is quietly not the path is worse than a
 * truncated one, because nothing about it looks wrong.
 */
function ItemList({ items }: { items: ContextItem[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-col">
      {items.map((item, index) => (
        <li
          key={`${item.tool}:${item.subject}:${index}`}
          className="flex items-center gap-2 py-1 text-[11px]"
        >
          <span className="text-muted-foreground/70 w-32 shrink-0 truncate font-mono">
            {item.tool}
          </span>
          <span
            className="text-muted-foreground/50 min-w-0 flex-1 truncate font-mono"
            title={item.subject}
          >
            {item.subject ? fromTheEnd(item.subject) : "—"}
          </span>
          {item.calls > 1 ? (
            <span className="text-muted-foreground/70 bg-muted/60 shrink-0 rounded px-1.5 py-0.5 font-mono tabular-nums">
              ×{item.calls}
            </span>
          ) : null}
          <span className="text-muted-foreground/50 w-14 shrink-0 text-end font-mono tabular-nums">
            {formatCompact(item.tokens)}
          </span>
          <span
            className={cn(
              "w-14 shrink-0 text-end font-mono tabular-nums",
              item.wasted > 0 ? "text-destructive/70" : "text-transparent",
            )}
            title={item.wasted > 0 ? "could go without losing anything" : undefined}
          >
            {item.wasted > 0 ? `−${formatCompact(item.wasted)}` : "—"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function groupKeyOf(key: string): string | undefined {
  return GROUP_OF[key];
}

/** How many calls a category lists before it says how many it is not listing. */
const ITEM_CAP = 50;

/** The tail of a long subject, which is the identifying part. Whole when it already fits. */
function fromTheEnd(subject: string, keep = 58): string {
  return subject.length <= keep ? subject : `…${subject.slice(-(keep - 1))}`;
}
