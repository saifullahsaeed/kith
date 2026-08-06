import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { useAuiState } from "@assistant-ui/react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import {
  MIN_QUERY,
  clearHighlights,
  findRanges,
  paintRanges,
  scrollToRange,
} from "@/lib/thread-find";
import { cn } from "@/lib/utils";

/**
 * Find a word in this conversation.
 *
 * The history panel's search finds the right *conversation* — deliberately, one hit each and
 * then it moves on, because a list of ten matches from one chat buries the nine other chats
 * that also had one. Nothing found the right *place* inside a long one, which is what this is.
 *
 * Entirely local. The client is already holding the whole transcript (`/api/conversations/<id>`
 * returns `timeline()` with no clamp), so there is nothing to ask the server and no round trip
 * between typing and seeing.
 *
 * Only what was said, never reasoning or tool calls — the rule `_spoken` sets on the server,
 * enforced here by walking an allowlist of prose containers. See `lib/thread-find.ts`.
 */
export function ThreadFind({
  focusSignal,
  onClose,
}: {
  /** Changes whenever ⌘F is pressed, including the press that opened this. Focus is driven by a
   *  changing value rather than by mount, so pressing ⌘F again while the bar is already open
   *  puts the caret back in it instead of doing nothing. */
  focusSignal: number;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Range[]>([]);
  const [current, setCurrent] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  // A turn streaming in changes what there is to find. Subscribing to the array is enough —
  // it is replaced on every update, so this re-runs without needing to inspect it.
  const messages = useAuiState((s) => s.thread.messages);

  useEffect(() => {
    // Selected, not just focused: a second ⌘F on an existing query should let you type over it,
    // which is what every other find bar does.
    input.current?.focus();
    input.current?.select();
  }, [focusSignal]);

  // Global for the document's lifetime, so it outlives this component unless taken off.
  useEffect(() => clearHighlights, []);

  /* Debounced for the same reason the history panel's is: "sad" on the way to "sadeef" is not
   * a search anyone asked for. Cheaper here than there — a tree walk rather than reading every
   * transcript off disk — but it also re-runs on every streamed token while a turn is landing,
   * which is where the waste would actually add up. */
  useEffect(() => {
    const timer = setTimeout(() => {
      const root = document.querySelector<HTMLElement>('[data-slot="aui_message-group"]');
      const found = findRanges(root, query);
      setMatches(found);
      // Clamped rather than reset. Typing another letter usually narrows the set you are
      // already walking, and throwing you back to the first match every keystroke would make
      // refining a query mean losing your place.
      setCurrent((was) => (found.length === 0 ? 0 : Math.min(was, found.length - 1)));
    }, 200);
    return () => clearTimeout(timer);
  }, [query, messages]);

  /* Paint, then move. Ranges are rebuilt wholesale by the effect above rather than patched,
   * because a re-rendered message leaves the old ones pointing at nodes that are no longer in
   * the document — a stale `Range` paints nothing and scrolls nowhere. */
  useEffect(() => {
    paintRanges(matches, current);
    const one = matches[current];
    if (one) scrollToRange(one);
  }, [matches, current]);

  const total = matches.length;
  const step = useCallback(
    (by: 1 | -1) => {
      // Wraps at both ends: seventeen of seventeen going forward is one of seventeen, not a
      // dead key. `+ total` because `%` keeps the sign of its left operand in JS.
      setCurrent((was) => (total ? (was + by + total) % total : was));
    },
    [total],
  );

  const short = query.trim().length > 0 && query.trim().length < MIN_QUERY;
  const empty = !short && query.trim().length >= MIN_QUERY && matches.length === 0;

  return (
    <div className="flex min-w-0 flex-1 items-center gap-1.5">
      <div className="relative min-w-0 flex-1">
        <Search className="text-muted-foreground/50 pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
        <input
          ref={input}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Find in this conversation…"
          aria-label="Find in this conversation"
          className={cn(
            "border-border/60 bg-card/60 focus-visible:border-ring h-6 w-full rounded-lg border py-0 ps-7 pe-2 text-xs outline-none",
            empty && "border-destructive/50",
          )}
          onKeyDown={(event) => {
            /* Enter and Shift-Enter rather than Enter-only: this is a find bar, so there is no
             * "submit" for Enter to mean instead, and walking backwards is half of walking. */
            if (event.key === "Enter") {
              event.preventDefault();
              step(event.shiftKey ? -1 : 1);
            } else if (event.key === "Escape") {
              /* Clears a query first and closes on the second press — the same two-stage
               * Escape the history panel uses, so the key means "back out one level" in both
               * places rather than "close" in one and "clear" in the other. */
              event.preventDefault();
              event.stopPropagation();
              if (query) setQuery("");
              else onClose();
            }
          }}
        />
      </div>

      {/* Only once the query is long enough to have run. A "0/0" sitting there while you type
          the first letter reads as a failed search rather than an unstarted one. */}
      {short ? null : (
        <span
          aria-live="polite"
          className={cn(
            "shrink-0 font-mono text-[11px] tabular-nums select-none",
            empty ? "text-destructive/80" : "text-muted-foreground/60",
          )}
        >
          {matches.length === 0 ? "0/0" : `${current + 1}/${matches.length}`}
        </span>
      )}

      <TooltipIconButton
        tooltip="Previous match"
        side="bottom"
        type="button"
        variant="ghost"
        size="icon"
        className="size-6 shrink-0 rounded-full"
        disabled={matches.length === 0}
        onClick={() => step(-1)}
      >
        <ChevronUp className="size-3.5" />
      </TooltipIconButton>
      <TooltipIconButton
        tooltip="Next match"
        side="bottom"
        type="button"
        variant="ghost"
        size="icon"
        className="size-6 shrink-0 rounded-full"
        disabled={matches.length === 0}
        onClick={() => step(1)}
      >
        <ChevronDown className="size-3.5" />
      </TooltipIconButton>
      <TooltipIconButton
        tooltip="Close find"
        side="bottom"
        type="button"
        variant="ghost"
        size="icon"
        className="size-6 shrink-0 rounded-full"
        onClick={onClose}
      >
        <X className="size-3.5" />
      </TooltipIconButton>
    </div>
  );
}
