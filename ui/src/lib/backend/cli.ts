/**
 * The `kith` command: is it on PATH, and put it there.
 *
 * A thin pair of calls, but the states they return are worth naming because three of them
 * look like failure and only one is:
 *
 * * `available: false` — no desktop shell, or a build that does not ship the CLI. There is
 *   nothing to link *from*, so the page should explain rather than offer a dead button.
 * * `stale: true` — a link exists and points at a different Kith. Needs replacing, not
 *   creating, and calling it "not installed" sends someone hunting for a command that is
 *   right there and broken.
 * * `cancelled: true` — the macOS password dialog was dismissed. That is an answer. Showing
 *   it back as an error is how an app argues with somebody who already said no.
 */

export type CliStatus = {
  available: boolean;
  installed: boolean;
  stale: boolean;
  link: string;
  target: string;
};

export type CliInstallResult = {
  ok: boolean;
  error: string;
  cancelled: boolean;
  status: CliStatus;
};

const ABSENT: CliStatus = {
  available: false,
  installed: false,
  stale: false,
  link: "",
  target: "",
};

export async function cliStatus(): Promise<CliStatus> {
  const response = await fetch("/api/system/cli");
  if (!response.ok) return ABSENT;
  return { ...ABSENT, ...((await response.json()) as Partial<CliStatus>) };
}

export async function installCli(remove = false): Promise<CliInstallResult> {
  const response = await fetch("/api/system/cli", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ remove }),
  });
  if (!response.ok) {
    return { ok: false, error: await response.text(), cancelled: false, status: ABSENT };
  }
  return (await response.json()) as CliInstallResult;
}
