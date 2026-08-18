/** What a project's folder is holding that the board has not taken in.
 *
 * `.kith/` travels with the repository, so a second person's tasks arrive as files — and the
 * board is a per-machine database that has never read them. Until this existed the whole
 * mechanism was reachable only by the model, which meant a person working through the panel
 * had no way to see that somebody else's work was sitting there, let alone take it.
 */

export interface SharedBoard {
  /** The project's directory, or "" when it has none — then there is nothing to read. */
  folder: string;
  /** Tasks somebody else filed that are not on this board. */
  added: string[];
  /** Tasks where their file is newer than this board. */
  updated: string[];
  /** Tasks where this board is newer, so nothing is taken. Reported, never applied. */
  kept: string[];
  /** Why the folder could not be read at all — a merge in progress, or conflict markers in a
   *  brief. Non-empty means nothing was read and nothing was changed. */
  blocked: string;
  /** Seconds since anything in the folder was written. Nothing arrives until somebody
   *  fetches, so a long quiet usually means nobody has pulled rather than nobody has worked. */
  changed_ago: number;
}

export async function fetchSharedBoard(projectId: number): Promise<SharedBoard> {
  const res = await fetch(`/api/projects/${projectId}/shared`);
  if (!res.ok) throw new Error(`couldn't read the folder (${res.status})`);
  return (await res.json()) as SharedBoard;
}

export async function takeSharedBoardIn(projectId: number): Promise<SharedBoard> {
  const res = await fetch(`/api/projects/${projectId}/shared`, { method: "POST" });
  const body = (await res.json().catch(() => ({}))) as SharedBoard & { error?: string };
  if (!res.ok) throw new Error(body.error ?? `couldn't take it in (${res.status})`);
  return body;
}

/** "3 hours", "2 days" — rounded, because the difference that matters is minutes against days. */
export function howLong(seconds: number): string {
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.floor(seconds / 60)} minutes ago`;
  if (seconds < 172800) return `${Math.floor(seconds / 3600)} hours ago`;
  return `${Math.floor(seconds / 86400)} days ago`;
}
