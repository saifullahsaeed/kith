import { WorkingOn } from "@/components/assistant-ui/working-on";
import { BackgroundTasks } from "@/components/chat/background-tasks";
import { StandingWork } from "@/components/chat/standing-work";
import { ContextSection } from "@/components/chat/context-section";
import { Errands, isErrandLine } from "@/components/chat/errands";
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
}: {
  activity: Activity;
  /** Which conversation is on screen. The sections about one conversation take it; background tasks
   *  and the lifetime token count are about the machine. */
  conversationId?: string;
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
    // No width and no border of its own: both were left over from the days when this was a
    // column pinned to the right of the chat, setting its own width and drawing the line
    // between itself and the thread. A pane decides how wide things are now and the layout
    // draws the dividers, so a width here would fight the panel it sits in and the border
    // would double the one already beside it.
    <aside className="flex h-full w-full flex-col bg-background/45 backdrop-blur-md">
      {/* **No header of its own.** There was one — an icon, a live round count, and a collapse
          button — and all three were already on screen. The panel is rendered inside a companion
          column or a tab, and both draw their own bar with the title and the close control right
          above this; the round count says the same number as the counters row at the foot. So it
          was a third close button under a second title, costing 49px of a column whose whole
          problem is that five sections have to fit in it.

          The sections start at the top now. */}
      {/* Everything between the header and the counters scrolls, and until now nothing did.
          The column is `h-full` with five stacked sections in it, so whatever did not fit was
          simply unreachable — one task with a nine-step checklist was enough to put the errands,
          the context and the background work below the fold with no way to get to them.

          `min-h-0` is the half that is easy to leave out and the half that makes it work: a flex
          child defaults to `min-height: auto`, which refuses to shrink below its content, so
          `overflow-y-auto` on its own finds nothing to scroll and silently does nothing. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
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

        {/* And what is going to start on its own. The same question as the one above asked about a
            different clock — what will happen here that you did not just ask for — and until now
            the only sign a standing schedule existed was it firing. */}
        <StandingWork conversationId={conversationId} />
      </div>

      {/* The counters sit directly under the sections rather than being pushed to the bottom of the
          column. Stretching to fill drew a border a thousand pixels below the last real thing, which
          framed the emptiness instead of leaving it alone — and "a column of nothing" was half of
          what was wrong with the feed this replaced. */}
      {hasCounters ? (
        <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-4 py-2.5">
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
