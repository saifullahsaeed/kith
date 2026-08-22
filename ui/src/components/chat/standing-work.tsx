import { useQuery } from "@tanstack/react-query";
import { Repeat } from "lucide-react";

import { useNow } from "@/hooks/use-now";
import { fetchBrain, type Schedule } from "@/lib/backend/brain";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

/**
 * What is going to interrupt this conversation, before it does.
 *
 * A standing schedule wakes a chat on its own clock, forever, and nothing said one existed. The
 * only trace was the wake itself arriving every half hour — so a conversation that kept returning
 * to CI status read as him looping rather than as a job doing exactly what it was set to do.
 * Measured on 2026-08-22: one schedule, `every_minutes = 30`, forty-one wakes into the same
 * conversation since the day before.
 *
 * The functionality is right; it was only invisible. So this says what is standing and when it
 * next fires, next to the background processes, which is the same question asked about a different
 * clock: what is going to happen here that I did not just ask for.
 *
 * Scoped to this conversation, and to the ones bound to nothing — a schedule pointed at another
 * chat will not interrupt this one, and listing it here would be someone else's alarm on your
 * wall. Renders nothing when there is nothing, like `BackgroundTasks`: a header over an empty list
 * is furniture.
 */
export function StandingWork({ conversationId }: { conversationId?: string }) {
  const { data: brain } = useQuery({ queryKey: keys.brain(), queryFn: fetchBrain });

  // A minute is the finest a schedule can be set to, so a clock any faster than this is spending
  // renders to show the same words. See hooks/use-now.ts on why a clock is not a poll.
  const now = useNow(30_000);

  const standing = (brain?.schedules ?? []).filter(
    (one) =>
      one.status === "active" &&
      (!one.conversation_id || one.conversation_id === conversationId),
  );
  if (standing.length === 0) return null;

  return (
    <div className="border-border/60 flex flex-col gap-2 border-b px-4 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium">Standing</span>
        <span className="text-muted-foreground/60 font-mono text-[10px] tabular-nums">
          {standing.length}
        </span>
      </div>
      <ul className="flex flex-col gap-1.5">
        {standing.map((one) => (
          <li key={one.id} className="flex items-start gap-2 text-[11px]">
            <Repeat className="text-muted-foreground/50 mt-0.5 size-3 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">
              {/* The note, in one line. These are written as instructions to him and run to a
                  paragraph — the whole `gh run list --jq …` incantation in the case this came
                  from — and the panel is answering "what is standing", not "what exactly will it
                  say". The full text is on hover. */}
              <span className="block truncate font-medium" title={one.note}>
                {one.note}
              </span>
              <span className="text-muted-foreground/60 block truncate font-mono text-[10px]">
                {cadence(one)}
              </span>
            </span>
            <span
              className={cn(
                "shrink-0 font-mono text-[10px] tabular-nums",
                // Amber inside a minute: it is about to take the conversation over, which is
                // worth a beat's warning if you were mid-sentence.
                dueIn(one.next_fire, now) <= 60_000
                  ? "text-kith"
                  : "text-muted-foreground/50",
              )}
              title={one.next_fire ? new Date(one.next_fire).toLocaleString() : undefined}
            >
              {until(one.next_fire, now)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** "every 30m", "daily at 09:00" — how it was set, in the words it was set in. */
function cadence(schedule: Schedule): string {
  if (schedule.daily_at) return `daily at ${schedule.daily_at}`;
  const minutes = schedule.every_minutes ?? 0;
  if (!minutes) return "standing";
  if (minutes < 60) return `every ${minutes}m`;
  if (minutes % 60 === 0) return `every ${minutes / 60}h`;
  return `every ${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function dueIn(nextFire: string, now: number): number {
  const at = new Date(nextFire).getTime();
  return Number.isNaN(at) ? Number.POSITIVE_INFINITY : at - now;
}

/**
 * How long until it fires.
 *
 * "due" rather than a negative number for one already past: the scheduler checks every thirty
 * seconds, so a moment in the past means it is about to happen, not that something went wrong.
 */
function until(nextFire: string, now: number): string {
  const left = dueIn(nextFire, now);
  if (!Number.isFinite(left)) return "";
  if (left <= 0) return "due";
  const minutes = Math.round(left / 60_000);
  if (minutes < 1) return "< 1m";
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `in ${hours}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`;
}
