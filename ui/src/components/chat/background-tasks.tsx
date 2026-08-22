import { useQuery } from "@tanstack/react-query";
import { Loader2, Terminal } from "lucide-react";

import { ago, useNow } from "@/hooks/use-now";
import { keys } from "@/lib/query-keys";

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
 * is a turn carrying on against a world that changed under it.
 */

interface Task {
  name: string;
  command: string;
  alive: boolean;
  /** The server's own sentence. Kept as the fallback for `startedAt`. */
  for: string;
  /** Unix seconds. What lets the elapsed time be counted here instead of re-fetched. */
  startedAt?: number;
}

export function BackgroundTasks({ conversationId }: { conversationId?: string }) {
  // Starting and finishing both publish a `process` change, so the list appears and empties as it
  // happens rather than on a timer.
  const { data: tasks = [] } = useQuery({
    queryKey: keys.processes(conversationId ?? ""),
    queryFn: async (): Promise<Task[]> => {
      const response = await fetch(
        `/api/processes?conversation=${encodeURIComponent(conversationId ?? "")}`,
      );
      if (!response.ok) return [];
      const body = (await response.json()) as { running?: Task[] } | null;
      return body?.running ?? [];
    },
  });

  /* The elapsed time is the one thing here that goes stale with nothing having changed, and it
   * used to be why this refetched every thirty seconds — a poll for a *duration*. The server sends
   * `startedAt` now, so the data is pushed and the counting is local. A clock, not a poll: see
   * hooks/use-now.ts. */
  const now = useNow(30_000);

  if (!tasks.length) return null;

  return (
    <div className="border-border/60 flex flex-col gap-2 border-b px-4 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium">Background</span>
        <span className="text-muted-foreground/60 font-mono text-[10px] tabular-nums">
          {tasks.length}
        </span>
      </div>
      <ul className="flex flex-col gap-1.5">
        {tasks.map((task) => (
          <li key={task.name} className="flex items-start gap-2 text-[11px]">
            {task.alive ? (
              <Loader2 className="text-kith mt-0.5 size-3 shrink-0 animate-spin" aria-hidden />
            ) : (
              <Terminal className="text-muted-foreground/50 mt-0.5 size-3 shrink-0" aria-hidden />
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{task.name}</span>
              {/* The command, because a name you chose an hour ago is not always enough to say
                  what is actually running. */}
              <span className="text-muted-foreground/60 block truncate font-mono text-[10px]">
                {task.command}
              </span>
            </span>
            <span className="text-muted-foreground/50 shrink-0 font-mono text-[10px] tabular-nums">
              {/* `startedAt` when the server sent one, and its own sentence otherwise — a running
                  process from a server that predates the field still reads correctly. */}
              {task.alive ? (task.startedAt ? ago(task.startedAt, now) : task.for) : "done"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
