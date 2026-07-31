/** Client for the autonomy API — status, control, and the live activity feed. */

import type { Usage } from "@/lib/tokens";

export interface AutonomyStatus {
  running: boolean;
  intervalSeconds: number;
  quietSeconds: number;
  ticking: boolean;
  /** A stop was asked for and the step is winding up. */
  stopping?: boolean;
  lastTick: string | null;
  current: string | null;
  ticks?: number;
  /** Everything he was shown, which counts a cached prefix again on every round. */
  tokensIn?: number;
  tokensOut?: number;
  /** The prompt side with cache hits removed — what a provider actually had to read. */
  tokensUncached?: number;
  lastTickTokens?: number;
  lastTickUncached?: number;
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
    | "status"
    | "tokens";
  text: string;
  at: string;
  /** Present on "tokens" items only: what one model request within the tick cost. */
  tokens?: Usage & { round: number };
  /** Present on "tool" items: the tool's name, as a field rather than embedded in `text`.
   *  The name used to be recovered by splitting `text` on "(" and discarding the rest, which
   *  threw away the only interesting part — which file, which command. */
  tool?: string;
  /** Present on "tool" items: its arguments, trimmed for display. Which one is the subject of
   *  the sentence is decided in the interface, since that changes with the wording. */
  args?: Record<string, string>;
}

export type AutonomyAction = "start" | "stop" | "tick" | "cancel";

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
