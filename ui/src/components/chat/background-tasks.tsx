import { useEffect, useState } from "react";
import { Loader2, Terminal } from "lucide-react";

import { useChanges } from "@/hooks/use-changes";

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
  for: string;
}

export function BackgroundTasks({ conversationId }: { conversationId?: string }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [changed, setChanged] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch(`/api/processes?conversation=${encodeURIComponent(conversationId ?? "")}`)
        .then((response) => (response.ok ? response.json() : null))
        .then((body: { running?: Task[] } | null) => {
          if (alive) setTasks(body?.running ?? []);
        })
        .catch(() => {});
    load();
    // The elapsed time needs a clock of its own — "12m" goes stale on its own without anything
    // changing — so this stays, slowly. Starting and finishing arrive as events below.
    const timer = window.setInterval(load, 30_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [conversationId, changed]);

  // Starting and finishing both publish, so the list appears and empties as it happens.
  useChanges("process", () => setChanged((n) => n + 1), conversationId);

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
              {task.alive ? task.for : "done"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
