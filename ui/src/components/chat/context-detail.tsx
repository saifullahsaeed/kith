import { useEffect, useMemo, useState } from "react";
import { ChevronRight, Layers, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DroppedSince, MessageExplorer } from "@/components/chat/context-messages";
import {
  fetchContextDetail,
  type ContextDetail,
  type ContextItem,
  type ContextLine,
  type LastRound as LastRoundFigures,
} from "@/lib/backend";
import { foldNow } from "@/lib/commands";
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
 * So the screen leads with the repeats. On the conversation this was built against: 2,541 items,
 * 843k tokens of provable repeats, one file read 51 times and one skill re-read 41 times. None of
 * that was visible anywhere in the app before, because the ledger adds a tool result's characters
 * to a bucket and drops everything about which call produced them.
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

      <div className="relative z-10 min-h-0 flex-1 overflow-y-auto">
        {/* Full width, because the thing on this page is a message list beside the message it
            opens, and a reading-measure column gives the pane about forty characters — a system
            prompt wrapped at forty characters is not a system prompt you can read. Prose blocks
            keep their own `max-w-prose`; nothing else on the page is prose. */}
        <div className="flex flex-col gap-8 px-6 py-8 xl:px-10">
          {detail === null ? (
            <Waiting />
          ) : !detail.reading ? (
            <NothingMeasuredYet items={detail.items} />
          ) : (
            <>
              <Window detail={detail} groups={groups} note={note} />
              <LastRound round={detail.lastRound} />
              {detail.sent.foldPending ? <FoldPending /> : null}
              {/* Led with, because it is the thing itself. Everything below it is analysis of
                  this list, and analysis is what the rail's meter already does in sixty
                  pixels — what it cannot do is show you the message. */}
              <MessageExplorer conversationId={conversationId} sent={detail.sent} />
              {/* The other half of context management. A screen that only ever grows explains
                  how a window fills and nothing about how it is kept from filling. */}
              <DroppedSince sent={detail.sent} />
              <Breakdown groups={groups} lines={detail.lines} />
              <Repeats detail={detail} />
              <WhatHeRead items={detail.items} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * What the provider actually billed for the last round.
 *
 * The only figures on this screen that are measured rather than derived: everything else is
 * `message_chars` over a ratio, and these came back from the provider. Worth separating for that
 * reason alone — an estimate and a receipt should not sit in the same row looking alike.
 */
function LastRound({ round }: { round: LastRoundFigures | null }) {
  if (!round) return null;
  const cachedShare = round.promptTokens
    ? Math.round((round.cachedTokens / round.promptTokens) * 100)
    : 0;
  return (
    <section className="border-border/60 bg-card/30 flex flex-wrap items-baseline gap-x-6 gap-y-2 rounded-xl border px-5 py-4">
      <Figure label="last round" value={formatTokens(round.promptTokens)} unit="prompt tokens" />
      <Figure
        label="served from cache"
        value={formatTokens(round.cachedTokens)}
        unit={cachedShare ? `${cachedShare}%` : "none"}
      />
      <Figure label="written" value={formatTokens(round.responseTokens)} unit="tokens" />
      <Figure label="cost" value={`$${round.costUsd.toFixed(5)}`} unit="" />
      {/* Beside the figures it belongs to, not pinned to the far edge. Pushed right with
          `ms-auto` this sat about fourteen hundred pixels from the numbers it describes on a
          wide window, which reads as two unrelated things rather than one row. */}
      <div>
        <div className="text-muted-foreground/40 text-[10px] tracking-[0.12em] uppercase">
          model
        </div>
        <div className="font-mono text-[12px]">{round.model || "—"}</div>
      </div>
    </section>
  );
}

function Figure({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div>
      <div className="text-muted-foreground/40 text-[10px] tracking-[0.12em] uppercase">
        {label}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-[15px] tabular-nums">{value}</span>
        {unit ? (
          <span className="text-muted-foreground/50 font-mono text-[11px]">{unit}</span>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Said out loud, because the alternative is a screen that quietly lies.
 *
 * The preview never pays for a fold — a summarisation call to draw a page is not a trade anyone
 * would take. On a conversation that is over the threshold, the next real turn will summarise
 * before it sends, so what is listed below is the prompt that would go if it did not.
 */
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

/** How full, what it is made of, and what happens next. */
function Window({
  detail,
  groups,
  note,
}: {
  detail: ContextDetail;
  groups: GroupReading[];
  note: string;
}) {
  const percent = Math.round(detail.share * 100);
  const nearFold = detail.share >= FOLDS_AT;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-baseline gap-3">
        <span className="text-4xl font-semibold tracking-tight tabular-nums">{percent}%</span>
        <span className="text-muted-foreground font-mono text-[13px] tabular-nums">
          {formatTokens(detail.used)} of {formatTokens(detail.window)} · {formatTokens(detail.free)}{" "}
          free
        </span>
      </div>

      {/* The composition, at a size worth reading. A 2px gap between segments because adjacent
          fills of similar lightness read as one block without one — and it is the secondary
          encoding the palette's light-mode contrast warn obliges.

          No fold marker on this bar, deliberately. Its full width is what is *used*, not the
          window, so a threshold measured against the window has nowhere to sit on it: the first
          version pinned one at 80% of used, which put a line at the right-hand edge on a window
          that was 7% full and read as "the fold is imminent" when it was nowhere near. How full
          it is has two places to say so already — the percentage and the figures. */}
      <span aria-hidden className="flex h-3 gap-0.5 overflow-hidden rounded-full">
        {groups.map((group) => (
          <span
            key={group.key}
            className={cn(group.swatch, "first:rounded-s-full last:rounded-e-full")}
            style={{ width: `${group.part * 100}%` }}
          />
        ))}
      </span>

      <p className="text-muted-foreground/70 text-[12px]">
        {note ||
          (detail.folded
            ? "Earlier steps of this turn were folded into notes to make room."
            : nearFold
              ? "Near the fold — earlier steps will be summarised to make room."
              : `Folds at ${Math.round(FOLDS_AT * 100)}%.`)}
      </p>
    </section>
  );
}

/**
 * The headline, and the reason this screen exists.
 *
 * Led with rather than buried under the categories, because it is the only figure here that is a
 * decision rather than a fact. Everything else describes what the window costs; this describes
 * what it costs for nothing.
 */
function Repeats({ detail }: { detail: ContextDetail }) {
  const repeated = detail.items.filter((item) => item.wasted > 0);
  if (repeated.length === 0) return null;

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
function Breakdown({ groups, lines }: { groups: GroupReading[]; lines: ContextLine[] }) {
  return (
    <section className="flex flex-col gap-5">
      <div>
        <h2 className="text-muted-foreground/60 text-[11px] font-semibold tracking-[0.14em] uppercase">
          Where it went
        </h2>
        <p className="text-muted-foreground/50 mt-1 text-[11px]">
          what the last turn actually sent — the same figures as the meter in the rail
        </p>
      </div>
      {groups.map((group) => (
        <div key={group.key} className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span aria-hidden className={cn("size-2.5 shrink-0 rounded-[3px]", group.swatch)} />
            <span className="text-[13px] font-medium">{group.label}</span>
            <span className="text-muted-foreground/60 ms-auto shrink-0 font-mono text-[12px] tabular-nums">
              {formatTokens(group.tokens)}
            </span>
            <span className="text-muted-foreground/40 w-9 shrink-0 text-end font-mono text-[12px] tabular-nums">
              {Math.round(group.part * 100)}%
            </span>
          </div>
          <p className="text-muted-foreground/50 ps-4.5 text-[11px]">{group.hint}</p>
          <div className="ps-4.5">
            {lines
              .filter((line) => groupKeyOf(line.key) === group.key)
              .sort((a, b) => b.tokens - a.tokens)
              .map((line) => (
                <Category key={line.key} label={line.label} tokens={line.tokens} items={[]} />
              ))}
          </div>
        </div>
      ))}
    </section>
  );
}

/** One of the ledger's eleven lines. Opens only when it has calls to name — persona, the system
 *  prompt and the tool schemas come from config rather than the conversation, so there is nothing
 *  underneath them and a disclosure arrow would be a promise the screen cannot keep. */
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
