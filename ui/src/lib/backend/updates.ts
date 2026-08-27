/** Whether there is a newer Kith than the one running. */

export interface UpdateState {
  /** The running version. Empty when Kith is running from a checkout. */
  current: string;
  /** The newest release on GitHub, or "" if the look failed and none is remembered. */
  latest: string;
  newer: boolean;
  /**
   * False from a checkout, where there is no installed version to compare against.
   *
   * Kept apart from `newer` because "there is nothing to check" and "you are up to date" are
   * different answers and must not read the same. A developer running `./run` should see the
   * first and never be offered a dmg.
   */
  packaged: boolean;
  /** The release page — always set when there is a release, so a button is never empty. */
  page: string;
  /** The dmg, when the release has one. */
  download: string;
  notes: string;
  publishedAt: string;
  checkedAt: string;
  /** Why the last look failed. Shown, not swallowed — see `services/updates.py`. */
  error: string;
}

export async function fetchUpdate(): Promise<UpdateState> {
  const response = await fetch("/api/update");
  if (!response.ok) throw new Error(`/api/update returned ${response.status}`);
  return (await response.json()) as UpdateState;
}

/** Look now, ignoring the six-hour cache. What "Check now" calls. */
export async function checkForUpdate(): Promise<UpdateState> {
  const response = await fetch("/api/update/check", { method: "POST" });
  if (!response.ok) throw new Error(`/api/update/check returned ${response.status}`);
  return (await response.json()) as UpdateState;
}
