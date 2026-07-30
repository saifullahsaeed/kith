/** Client for the autonomy API — status, control, and the live activity feed. */

export interface AutonomyStatus {
  running: boolean;
  intervalSeconds: number;
  quietSeconds: number;
  ticking: boolean;
  lastTick: string | null;
  current: string | null;
}

export interface ActivityItem {
  kind:
    | "start"
    | "reflect"
    | "curious"
    | "consolidate"
    | "reply"
    | "breakout"
    | "tool"
    | "thought"
    | "reminder"
    | "done"
    | "error"
    | "status";
  text: string;
  at: string;
}

export type AutonomyAction = "start" | "stop" | "tick";

export async function fetchAutonomyStatus(): Promise<AutonomyStatus> {
  const response = await fetch("/api/autonomy");
  if (!response.ok) throw new Error(`/api/autonomy returned ${response.status}`);
  return (await response.json()) as AutonomyStatus;
}

export async function controlAutonomy(
  action: AutonomyAction,
  intervalSeconds?: number,
): Promise<AutonomyStatus> {
  const response = await fetch("/api/autonomy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...(intervalSeconds ? { intervalSeconds } : {}) }),
  });
  if (!response.ok) throw new Error(`/api/autonomy returned ${response.status}`);
  return (await response.json()) as AutonomyStatus;
}

/** Subscribe to the server-sent activity stream. Returns an unsubscribe fn. */
export function openActivityStream(onItem: (item: ActivityItem) => void): () => void {
  const source = new EventSource("/api/autonomy/stream");
  source.onmessage = (event) => {
    try {
      onItem(JSON.parse(event.data) as ActivityItem);
    } catch {
      /* ignore malformed frames */
    }
  };
  return () => source.close();
}
