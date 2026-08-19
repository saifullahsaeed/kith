import { Activity, PanelRightClose } from "lucide-react";

import { WorkingOn } from "@/components/assistant-ui/working-on";
import { BackgroundTasks } from "@/components/chat/background-tasks";
import { ContextSection } from "@/components/chat/context-section";
import { Errands, isErrandLine } from "@/components/chat/errands";
import { Button } from "@/components/ui/button";
import { formatTokens } from "@/lib/tokens";
import type { useActivity } from "@/hooks/use-activity";
import type { ActivityItem } from "@/lib/backend/activity";

type Activity = ReturnType<typeof useActivity>;

/**
 * What chat cannot tell you, beside the chat.
 *
 * This was a round-by-round feed of tool calls — "opened the skill running-a-project", "looked over
 * his tasks" — which is the same list the thread already renders, one panel away from where you read
 * it. Four hundred pixels of second copy, and a column of nothing whenever a conversation was quiet.
 *
 * Gone, and what is left is the three things a conversation genuinely cannot say:
 *
 * * **what he is working on**, and how far through its checklist;
 * * **what a sub-agent is finding out for him** — the one tool feed that is not a second copy,
 *   because a worker's searching is discarded by design and reaches the thread nowhere;
 * * **what is in his head**, itemised, with the button that shrinks it;
 * * **what is running in the background** while he does something else.
 *
 * Each is its own file. This is the column they sit in, and the counters underneath.
 */
export function WorkPanel({
  activity,
  conversationId = "",
  width,
  onClose,
}: {
  activity: Activity;
  /** Which conversation is on screen. The sections about one conversation take it; background tasks
   *  and the lifetime token count are about the machine. */
  conversationId?: string;
  width: number;
  onClose: () => void;
}) {
  const { status, activity: lines } = activity;
  // Rounds this session, counted off the activity lines. The feed used to group them into blocks and
  // count those; there are no blocks any more.
  const steps = lines.filter((item) => !isControlLine(item) && !isErrandLine(item)).length;
  const lifetime = (status?.tokensUncached ?? 0) + (status?.tokensOut ?? 0);
  // Whether the counters row has anything to say. It used to render regardless and fill itself with
  // "no steps yet" — an empty bar is better removed than captioned.
  const hasCounters = steps > 0 || lifetime > 0;

  return (
    <aside
      style={{ width }}
      className="flex h-full shrink-0 flex-col border-l border-border/60 bg-background/45 backdrop-blur-md"
    >
      {/* header */}
      <div className="flex items-center gap-2.5 border-b border-border/60 px-4 py-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted/70 text-kith">
          <Activity className="size-4" />
        </span>
        <div className="min-w-0 flex-1 leading-tight">
          {/* "Work", not "Mind". His actual mind — memories, notes, journal — is the group of that
              name in the control panel, and having both called Mind meant the word told you nothing
              about which one you were looking at. */}
          <div className="flex items-center gap-2 text-sm font-semibold">Work</div>
          <div className="truncate text-[11px] text-muted-foreground">
            {steps > 0 ? `${steps} round${steps === 1 ? "" : "s"} this session` : "nothing yet"}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 text-muted-foreground hover:text-foreground"
          onClick={onClose}
          aria-label="Collapse the work panel"
        >
          <PanelRightClose className="size-4" />
        </Button>
      </div>

      {/* What he is on right now, and how far through it. It lived above the composer, which put
          "what is he doing" in the middle of the thing you type into. */}
      <WorkingOn conversationId={conversationId} />

      {/* Who he has sent to find something out, and how far they have got. Directly under
          what he is working on, because while an errand is out it *is* what is happening —
          the thread shows one spinning row and nothing else until it comes back. */}
      <Errands lines={lines} conversationId={conversationId} />

      {/* What is in his head, itemised, and the Fold button. */}
      <ContextSection conversationId={conversationId} />

      {/* What is running while he does something else. Renders nothing when there is nothing — a
          header over an empty list is furniture. */}
      <BackgroundTasks conversationId={conversationId} />

      {/* The counters sit directly under the sections rather than being pushed to the bottom of the
          column. Stretching to fill drew a border a thousand pixels below the last real thing, which
          framed the emptiness instead of leaving it alone — and "a column of nothing" was half of
          what was wrong with the feed this replaced. */}
      {hasCounters ? (
        <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
          <div className="flex-1" />
          <span
            className="text-[11px] tabular-nums text-muted-foreground"
            title={
              status?.tokensIn
                ? `${formatTokens(status.tokensIn)} shown, ${formatTokens(status.tokensIn - (status.tokensUncached ?? 0))} of it served from cache`
                : undefined
            }
          >
            {[
              steps ? `${steps} step${steps === 1 ? "" : "s"}` : "",
              // Since the server started, and only what a provider actually had to read.
              lifetime ? `${formatTokens(lifetime)} tokens` : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </div>
      ) : null}
    </aside>
  );
}

/** A line about the machine rather than about work. Counted out of "rounds", because a session that
 *  woke and went straight back to sleep read as thirteen steps. */
function isControlLine(item: ActivityItem): boolean {
  return item.kind === "status";
}
