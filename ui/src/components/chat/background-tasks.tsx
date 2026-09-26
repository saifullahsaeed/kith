import { useQuery } from "@tanstack/react-query";

import { ago, useNow } from "@/hooks/use-now";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

/**
 * What is running in the background, while it runs.
 *
 * A half-hour test suite used to be invisible: he started it, the turn ended, and the only way to
 * know anything about it was for him to check — which is a whole round at whatever the prompt costs,
 * and 371 of those calls over two days. He finds out now because finishing wakes the conversation.
 * This is so *you* find out without asking either.
 *
 * Scoped to this conversation. Two projects are two chats, and listing every session's work would
 * show you a suite you cannot explain, stop, or take credit for.
 *
 * Read-only. Starting and stopping stay his, through the tools: a process he started and you killed
 * is a turn carrying on against a world that changed under it. (The design draws Stop and Open on
 * this card; there is no route for either, and adding one is that decision reopened, not styling.)
 */

export interface Task {
  name: string;
  command: string;
  alive: boolean;
  /** The server's own sentence. Kept as the fallback for `startedAt`. */
  for: string;
  /** Unix seconds. What lets the elapsed time be counted here instead of re-fetched. */
  startedAt?: number;
}

/** This conversation's processes, from `/api/processes`. Shared with the companion's tab row,
 *  which lights Activity while one is alive, so both read one cache entry. */
export async function fetchProcesses(conversationId: string): Promise<Task[]> {
  const response = await fetch(`/api/processes?conversation=${encodeURIComponent(conversationId)}`);
  if (!response.ok) return [];
  const body = (await response.json()) as { running?: Task[] } | null;
  return body?.running ?? [];
}

export function BackgroundTasks({ conversationId }: { conversationId?: string }) {
  // Starting and finishing both publish a `process` change, so the list appears and empties as it
  // happens rather than on a timer.
  const { data: tasks = [] } = useQuery({
    queryKey: keys.processes(conversationId ?? ""),
    queryFn: () => fetchProcesses(conversationId ?? ""),
  });

  /* The elapsed time is the one thing here that goes stale with nothing having changed, and it
   * used to be why this refetched every thirty seconds — a poll for a *duration*. The server sends
   * `startedAt` now, so the data is pushed and the counting is local. A clock, not a poll: see
   * hooks/use-now.ts. */
  const now = useNow(30_000);

  if (!tasks.length) return null;

  return (
    // One card, a row per process: a light, the name you gave it, and the command under it with
    // how long it has been up. The same card the design draws for the webapp a turn left running.
    <section
      aria-label="Running in the background"
      className="flex shrink-0 flex-col rounded-[14px] border border-border bg-card"
    >
      {tasks.map((task) => (
        <div
          key={task.name}
          className="flex min-w-0 items-center gap-2.5 border-foreground/7 px-3.5 py-2.5 [&+&]:border-t"
        >
          <span
            aria-hidden
            className={cn(
              "size-[7px] shrink-0 rounded-full",
              task.alive ? "bg-roam" : "bg-muted-foreground/40",
            )}
          />
          <span className="flex min-w-0 flex-1 flex-col gap-px">
            <span className="truncate text-[13px]">{task.name}</span>
            {/* The command, because a name you chose an hour ago is not always enough to say
                what is actually running. */}
            <code className="truncate font-mono text-[11px] text-muted-foreground/80">
              <span>{task.command}</span>
              {" · "}
              {task.alive ? (
                <>
                  up{" "}
                  {/* `startedAt` when the server sent one, and its own sentence otherwise — a
                      running process from a server that predates the field still reads right. */}
                  <span>{task.startedAt ? ago(task.startedAt, now) : task.for}</span>
                </>
              ) : (
                <span>done</span>
              )}
            </code>
          </span>
        </div>
      ))}
    </section>
  );
}
