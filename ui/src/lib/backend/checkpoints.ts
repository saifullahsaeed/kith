/** Client for the checkpoint chain — files Kith has changed, before each change. */

export interface Checkpoint {
  id: number;
  sha: string;
  parentSha: string | null;
  repoRoot: string;
  trigger: string;
  createdAt: string;
  /** Same index the UI already uses for messages — an integer comparison, not timestamp math. */
  turnIndex: number;
}

export interface RestoreResult {
  ok: boolean;
  dirtyBefore: string;
  safetyCheckpointId: number | null;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(detail.error ?? `that didn't work (${response.status})`);
  }
  return (await response.json()) as T;
}

export const fetchCheckpoints = (conversationId: string) =>
  json<{ checkpoints: Checkpoint[] }>(`/api/conversations/${conversationId}/checkpoints`).then(
    (r) => r.checkpoints,
  );

export const restoreCheckpoint = (checkpointId: number) =>
  json<RestoreResult>(`/api/checkpoints/${checkpointId}/restore`, { method: "POST" });
