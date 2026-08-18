import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuiState } from "@assistant-ui/react";
import { ChevronRight, Layers, Maximize2 } from "lucide-react";

import { latestUsage } from "@/components/assistant-ui/thread";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { foldNow } from "@/lib/commands";
import { byGroup, FOLDS_AT } from "@/lib/context-groups";
import { pathForContext } from "@/lib/router";
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

export function ContextSection({ conversationId }: { conversationId?: string }) {
  const navigate = useNavigate();
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
  // Shared with the detail screen this opens — see `lib/context-groups` for why the grouping and
  // the palette live in one place rather than two.
  const groups = byGroup(context.lines, context.used);

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
          {/* The rail can say how full and what of, in sixty pixels. It cannot say *which* of it
              is there twice, which is the question you open a context reading with and the one
              thing you can act on — so that gets a screen, and this is the way in. */}
          <Button
            size="xs"
            variant="ghost"
            className="text-muted-foreground/70 hover:text-foreground -me-1 size-6 shrink-0 px-0"
            onClick={() => navigate(pathForContext())}
            aria-label="Open the full context breakdown"
            title="Open the full breakdown — every call, and which of them are repeats"
          >
            <Maximize2 className="size-3" />
          </Button>
        </div>

        {/* The bar is what the collapsed state is for, so it lives outside the content. A 2px gap
            between segments, because adjacent fills of similar lightness read as one block without
            one — and it is the secondary encoding the palette's light-mode contrast warn asks for. */}
        <span aria-hidden className="mt-2 flex h-1.5 gap-0.5 overflow-hidden rounded-full">
          {groups.map((group) => (
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
              {groups.map((group) => (
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
