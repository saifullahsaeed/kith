import { useEffect, useState } from "react";
import { useAuiState } from "@assistant-ui/react";
import { ChevronRight, Layers } from "lucide-react";

import { latestUsage } from "@/components/assistant-ui/thread";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { foldNow } from "@/lib/commands";
import { formatTokens } from "@/lib/tokens";
import type { ContextLedger } from "@/lib/backend/types";

/**
 * What is in his head right now, itemised, with the button that does something about it.
 *
 * The compact meter under the composer said `54.5k (5%)` and kept everything else in a native
 * `title` — so the breakdown, which is the only part you can act on, sat behind a hover on a
 * fourteen-pixel bar, in a tooltip the OS paints where it likes. A number you cannot decompose says
 * the window is filling and nothing about what to do, and that is the whole question: 300k of stale
 * tool results and 300k of conversation are the same percentage and completely different problems.
 *
 * Collapsed to the bar by default. Expanded is the itemised list, which is also what makes the
 * colours legible — the palette clears every contrast gate on the dark surface and warns on the
 * light one, and a warn there obliges labels rather than colour alone.
 *
 * Nothing renders at all when the window is unknown. A local model reports `window === 0`, and a
 * percentage of an unknown total is worse than no percentage.
 */

/** The threshold the loop folds at, from `agent_loop`. Named so the section can say what is about to
 *  happen rather than only how full it is — 78% and 82% look alike and are not. */
const FOLDS_AT = 0.8;

/**
 * What each of the ledger's eleven categories *is*, and the colour that says so.
 *
 * Grouped rather than eleven hues, for two reasons. A categorical palette runs out at eight, and a
 * ninth hue is never generated — it folds. And the groups are the actual decisions: tool schemas are
 * shrunk by removing tools, the conversation by folding it, what he has read by not re-reading it.
 * Eleven colours would name eleven rows and answer none of that.
 *
 * **The hue belongs to the group, never to its size.** These were assigned by rank at first, which
 * meant the bar repainted itself whenever two categories swapped places — the one thing a
 * categorical scale must never do, since it makes the colour mean "currently third largest" instead
 * of "the conversation".
 *
 * Slots 1-5 of a palette validated against Kith's own surfaces in both modes: worst adjacent CVD
 * ΔE 9.1 light / 8.4 dark, worst adjacent normal-vision ΔE 19.6 / 19.3.
 */
const GROUPS = [
  { key: "talk", label: "Conversation", swatch: "bg-[#2a78d6] dark:bg-[#3987e5]" },
  { key: "read", label: "What he has read", swatch: "bg-[#eb6834] dark:bg-[#d95926]" },
  { key: "tools", label: "Tool schemas", swatch: "bg-[#1baf7a] dark:bg-[#199e70]" },
  { key: "place", label: "Where he is", swatch: "bg-[#eda100] dark:bg-[#c98500]" },
  { key: "self", label: "Who he is", swatch: "bg-[#e87ba4] dark:bg-[#d55181]" },
] as const;

const GROUP_OF: Record<string, (typeof GROUPS)[number]["key"]> = {
  messages: "talk",
  tool_results: "read",
  code: "read",
  skills: "read",
  images: "read",
  built_in_tools: "tools",
  mcp_tools: "tools",
  custom_tools: "tools",
  live: "place",
  persona: "self",
  system: "self",
};

export function ContextSection({ conversationId }: { conversationId?: string }) {
  const messages = useAuiState((s) => s.thread.messages);
  const running = useAuiState((s) => s.thread.isRunning);
  const usage = latestUsage(messages);
  const [open, setOpen] = useState(false);
  const [folding, setFolding] = useState(false);
  // The reading a fold leaves behind. A fold takes no reading of its own, so until the next turn
  // reports in, the thread's latest usage still describes the conversation as it was *before* the
  // fold — which is what made `/fold` look like it had done nothing. Dropped the moment a turn
  // starts, because that turn measures the window itself and its figure is the true one.
  const [afterFold, setAfterFold] = useState<ContextLedger>();
  const [note, setNote] = useState("");
  useEffect(() => {
    if (running) {
      setAfterFold(undefined);
      setNote("");
    }
  }, [running]);

  const context = afterFold ?? usage?.context ?? usage?.baseline;
  if (!context?.window) return null;

  const percent = Math.round(context.share * 100);
  const nearFold = context.share >= FOLDS_AT;
  const tone = afterFold
    ? "text-muted-foreground"
    : usage?.folded
      ? "text-red-600/80 dark:text-red-400/80"
      : nearFold
        ? "text-amber-600/80 dark:text-amber-400/80"
        : "text-muted-foreground";

  // Fixed order, so a segment stays where it was and the bar does not reshuffle between turns.
  //
  // `part` is the share of what is *used*, not of the window, and that is what makes the bar worth
  // colouring. Drawn against the window it was honest and useless: at 5% of a million tokens all
  // five categories are crushed into a fiftieth of the width, so the composition — the only thing
  // the colours are for — was unreadable at exactly the usage level a conversation spends most of
  // its life at. How full it is has two other places to say so, the percentage and the figures.
  const byGroup = GROUPS.map((group) => {
    const mine = context.lines.filter((line) => GROUP_OF[line.key] === group.key);
    const tokens = mine.reduce((sum, line) => sum + line.tokens, 0);
    return { ...group, tokens, part: context.used ? tokens / context.used : 0 };
  }).filter((group) => group.tokens > 0);

  const fold = async () => {
    if (!conversationId || folding) return;
    setFolding(true);
    setNote("Folding…");
    const result = await foldNow(conversationId);
    setNote(result.note);
    if (result.reading) setAfterFold(result.reading);
    setFolding(false);
  };

  return (
    <div className="border-border/60 flex flex-col gap-2 border-b px-4 py-3">
      <Collapsible open={open} onOpenChange={setOpen}>
        <div className="flex items-center gap-1.5">
          <CollapsibleTrigger className="group flex min-w-0 flex-1 items-center gap-1.5 text-left">
            <span className="text-[11px] font-medium">Context</span>
            <ChevronRight
              className="text-muted-foreground/60 size-3 transition-transform group-data-[state=open]:rotate-90"
              aria-hidden
            />
            <span className={`ms-auto font-mono text-[11px] tabular-nums ${tone}`}>{percent}%</span>
          </CollapsibleTrigger>
          {/* The one thing you can do about a full window, next to the number that says it is
              full. It was only reachable as `/fold` typed into the composer, which is the wrong
              place for it: the composer is for talking to him, and this is maintenance on the
              conversation. Disabled mid-turn — a fold rewrites the history the turn is reading. */}
          <Button
            size="xs"
            variant="ghost"
            className="text-muted-foreground/70 hover:text-foreground -me-1 h-6 shrink-0 px-1.5"
            onClick={fold}
            disabled={!conversationId || folding || running}
            title={
              running
                ? "Wait for the turn to finish — a fold rewrites the history it is reading."
                : "Summarise the older turns now, instead of waiting for the window to fill"
            }
          >
            <Layers className="size-3" />
            <span className="text-[11px]">Fold</span>
          </Button>
        </div>

        {/* The bar is what the collapsed state is for, so it lives outside the content. A 2px gap
            between segments, because adjacent fills of similar lightness read as one block without
            one — and it is the secondary encoding the palette's light-mode contrast warn asks for. */}
        <span aria-hidden className="mt-2 flex h-1.5 gap-0.5 overflow-hidden rounded-full">
          {byGroup.map((group) => (
            <span
              key={group.key}
              className={`${group.swatch} first:rounded-s-full last:rounded-e-full`}
              style={{ width: `${group.part * 100}%` }}
            />
          ))}
        </span>

        <CollapsibleContent>
          <div className="flex flex-col gap-2 pt-2.5">
            {/* The counts, not only the percent. A whole point of a million-token window is that
                tens of thousands of tokens move the count and leave the percent unchanged. */}
            <p className="text-muted-foreground/70 font-mono text-[10px] tabular-nums">
              {formatTokens(context.used)} of {formatTokens(context.window)} ·{" "}
              {formatTokens(context.free)} free
            </p>

            <ul className="flex flex-col gap-1">
              {byGroup.map((group) => (
                <li key={group.key} className="flex items-center gap-2 text-[11px]">
                  <span aria-hidden className={`size-2 shrink-0 rounded-[2px] ${group.swatch}`} />
                  <span className="text-muted-foreground min-w-0 flex-1 truncate">
                    {group.label}
                  </span>
                  <span className="text-muted-foreground/60 shrink-0 font-mono text-[10px] tabular-nums">
                    {formatTokens(group.tokens)}
                  </span>
                  {/* Of what is in the window, matching the bar above — not of the window, which
                      would read 1% against every row and say nothing. */}
                  <span className="text-muted-foreground/40 w-8 shrink-0 text-end font-mono text-[10px] tabular-nums">
                    {Math.round(group.part * 100)}%
                  </span>
                </li>
              ))}
            </ul>

            {/* Every category, under the group that owns it — the grouping is what makes the bar
                readable, and this is where you find out which read is the expensive one. */}
            <ul className="text-muted-foreground/45 flex flex-col gap-0.5 ps-4 text-[10px]">
              {[...context.lines]
                .filter((line) => line.tokens > 0)
                .sort((a, b) => b.tokens - a.tokens)
                .map((line) => (
                  <li key={line.key} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate">{line.label}</span>
                    <span className="shrink-0 font-mono tabular-nums">
                      {formatTokens(line.tokens)}
                    </span>
                  </li>
                ))}
            </ul>
          </div>
        </CollapsibleContent>
      </Collapsible>

      {/* What happens next, rather than leaving you to know that 80% is the number that matters. */}
      <p className="text-muted-foreground/50 text-[10px]">
        {note ||
          (afterFold
            ? "Folded. This is the window it left."
            : usage?.folded
              ? "Earlier steps of this turn were folded into notes to make room."
              : nearFold
                ? "Near the fold — earlier steps will be summarised to make room."
                : `Folds at ${Math.round(FOLDS_AT * 100)}%.`)}
      </p>
    </div>
  );
}
