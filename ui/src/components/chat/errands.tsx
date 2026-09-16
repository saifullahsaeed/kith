import { Check, Loader2 } from "lucide-react";
import { useState } from "react";

import { describeCall } from "@/lib/tool-language";
import type { ActivityItem } from "@/lib/backend/activity";

/**
 * What someone else is finding out for him, while they find it out.
 *
 * The panel this sits in used to be a round-by-round list of every tool call, and that was
 * deleted on purpose: it was the same list the thread renders, four hundred pixels from where
 * you read it. This is the exception the deletion leaves room for, and the test of it is
 * whether the information exists anywhere else. For an errand it does not — a sub-agent's
 * greps and reads are discarded by design and never reach the thread, so a panel that stays
 * quiet during one means nobody ever sees the work. From the outside, three scouts running
 * for forty seconds and the interface having hung look identical.
 *
 * Running errands are open, with their last few steps under them. Finished ones collapse to a
 * single dim line — kept rather than dropped, because "it looked at eleven things and then
 * told you that" is the context for a report you are about to read in the thread.
 *
 * Scoped to this conversation, same as everything else in the column.
 */

/** How many steps of one running errand to show. Enough to see it moving; not so many that a
 *  thorough scout pushes the rest of the panel off the screen. */
const RECENT_STEPS = 5;

/** How much of an objective is shown before it is folded.
 *
 *  It had no limit at all, and a builder is what found that out. A scout's objective is a
 *  question — one line — but `send_builder`'s own description tells it to say exactly what to
 *  change, in which files, and what done looks like, because anything it leaves out the worker
 *  invents. So a *good* builder objective is three hundred words, and the panel rendered every
 *  one of them down a 320px column: two errands filled the screen and the steps under them,
 *  which are the thing this panel exists to show, were pushed off the bottom.
 *
 *  Folded rather than truncated, because the text is worth reading — just not first, and not
 *  all at once. Click opens it. */
const FOLDED_LINES = "line-clamp-2";

interface Errand {
  id: string;
  objective: string;
  steps: { tool: string; args: Record<string, string> }[];
  running: boolean;
  /** `scout` or `builder`. Decides the icon and the label — see `ErrandHead`. */
  role: string;
  /** `grep x3, read_file x8` — what the closing line carried. */
  tally: string;
}

/** The feed, folded into one entry per errand, oldest first.
 *
 * Exported for its test. A reducer over a flat event log is the kind of thing that looks
 * obviously right and silently drops the last step, and the panel is the worst place to find
 * that out.
 */
export function foldErrands(lines: ActivityItem[], conversationId: string): Errand[] {
  const byId = new Map<string, Errand>();
  for (const line of lines) {
    const mark = line.errand;
    if (!mark) continue;
    // A line with no conversation is about the machine, not about one piece of work. An errand
    // is always about a piece of work, so an unlabelled one belongs to nothing on screen.
    if (conversationId && line.conversation !== conversationId) continue;
    const found = byId.get(mark.id) ?? {
      id: mark.id,
      objective: mark.objective,
      steps: [],
      running: true,
      // Defaulted rather than required: a line published before roles existed has none, and a
      // worker that reads is the safer of the two to be wrong about.
      role: mark.role ?? "scout",
      tally: "",
    };
    if (mark.state === "step" && line.tool) {
      found.steps.push({ tool: line.tool, args: line.args ?? {} });
    }
    if (mark.state === "done") {
      found.running = false;
      found.tally = line.text;
    }
    byId.set(mark.id, found);
  }
  return [...byId.values()];
}

/** One errand's first line: what it is, what kind it is, and how far down it has got.
 *
 *  Its own component only because it holds state. A single `open` id on the list would collapse
 *  whichever one you were reading the moment you opened another, and two builders running at
 *  once is exactly when you want both briefs visible.
 */
function ErrandHead({ errand }: { errand: Errand }) {
  const [open, setOpen] = useState(false);
  const builder = errand.role === "builder";

  return (
    <div className="flex items-start gap-2 text-[11px]">
      {/* The spinner stays the spinner. A role-specific glyph read better in isolation and lost
          the one thing this icon is for — saying the errand is still moving, which is what makes
          three scouts running distinguishable from the interface having hung. Role is carried by
          the tint and, for anyone who cannot use the tint, by the word. */}
      {errand.running ? (
        <Loader2
          className={`mt-0.5 size-3 shrink-0 animate-spin ${builder ? "text-amber-400/80" : "text-teal-400/80"}`}
          aria-hidden
        />
      ) : (
        <Check className="text-muted-foreground/50 mt-0.5 size-3 shrink-0" aria-hidden />
      )}
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        title={open ? "Fold" : errand.objective}
        aria-expanded={open}
        className={`min-w-0 flex-1 cursor-pointer text-left leading-relaxed ${
          errand.running ? "" : "text-muted-foreground/70"
        } ${open ? "whitespace-pre-wrap" : errand.running ? FOLDED_LINES : "truncate"}`}
      >
        {/* Named, not only coloured. A builder is editing a copy of your repository and a scout
            is not, and that difference must survive someone who cannot tell teal from amber. */}
        {builder ? (
          <span className="mr-1 font-medium text-amber-400/80">builder</span>
        ) : null}
        {errand.objective}
      </button>
    </div>
  );
}

export function Errands({
  lines,
  conversationId = "",
}: {
  lines: ActivityItem[];
  conversationId?: string;
}) {
  const errands = foldErrands(lines, conversationId);
  // Nothing to say beats a header over an empty list — the same rule the background section
  // follows, and half of what was wrong with the feed this panel replaced.
  if (!errands.length) return null;
  const running = errands.filter((one) => one.running).length;

  return (
    <div className="border-border/60 flex flex-col gap-2 border-b px-4 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium">Errands</span>
        <span className="text-muted-foreground/60 font-mono text-[10px] tabular-nums">
          {running ? `${running} out` : errands.length}
        </span>
      </div>
      <ul className="flex flex-col gap-2">
        {errands.map((errand) => (
          <li key={errand.id} className="flex flex-col gap-1">
            <ErrandHead errand={errand} />
            {/* Only while it is out. Once the report is in the thread, the step-by-step is
                noise — what survives is the tally, which is the one thing the report itself
                cannot tell you. */}
            {errand.running ? (
              <ul className="border-border/40 ml-[1.375rem] flex flex-col gap-0.5 border-l pl-2">
                {errand.steps.slice(-RECENT_STEPS).map((step, i) => {
                  const call = describeCall(step.tool, step.args);
                  return (
                    <li
                      key={`${errand.id}-${errand.steps.length - Math.min(errand.steps.length, RECENT_STEPS) + i}`}
                      className="text-muted-foreground/60 truncate text-[10px]"
                      title={`${call.verb} ${call.subject}`}
                    >
                      {call.verb}
                      {call.subject ? <span className="font-mono"> {call.subject}</span> : null}
                    </li>
                  );
                })}
              </ul>
            ) : errand.tally ? (
              <span className="text-muted-foreground/50 ml-[1.375rem] truncate font-mono text-[10px]">
                {errand.tally}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** An errand line, for the panel's round counter to skip. A worker's tool calls are not the
 *  turn's rounds, and counting them made a single delegation read as nine steps. */
export function isErrandLine(item: ActivityItem): boolean {
  return item.kind === "errand";
}
