/**
 * Putting `kith` on PATH, from inside the app.
 *
 * The CLI is shipped in Resources and is useless there — a binary nobody can type is not a
 * command line. Something has to link it somewhere a shell will find it, and on macOS that is
 * a narrower question than it looks.
 *
 * **Why /usr/local/bin and not ~/.local/bin.** The system PATH comes from `/etc/paths`:
 * /usr/local/bin, /usr/bin, /bin, /usr/sbin, /sbin. Nothing in the default zsh profile adds a
 * home directory to it. So `~/.local/bin/kith` is a file that exists and a command that is not
 * found, and finishing that install means editing somebody's `.zshrc` — a second consent, for
 * a file people feel ownership of, that only takes effect in a new shell. /usr/local/bin is on
 * PATH on every Mac by construction. It costs one admin password, which is a recognisable
 * system dialog rather than a thing we invented, and it is what `code`, `gh` and every other
 * tool in this position does.
 *
 * **Why a symlink and not a copy.** An app update replaces the binary inside the bundle. A
 * symlink follows it; a copy goes on running the old CLI silently, against a newer server,
 * until somebody notices the version. The same property is what makes an update need no
 * action at all — which is the whole reason this is worth doing once rather than on every
 * launch.
 *
 * **Why status is checked by reading the link rather than by running `which`.** `which kith`
 * answers about the PATH of whatever shell Electron inherited, which in a GUI app launched
 * from Finder is not the PATH of the user's terminal. Reading the symlink asks the only
 * question that has a stable answer: is there a link at the well-known place, and does it
 * point at us.
 */

import { exec } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { promisify } from "node:util";

const run = promisify(exec);

/** Where a command has to be for a terminal to find it without anyone editing a profile. */
const LINK = "/usr/local/bin/kith";

/** Where the frozen CLI lives, packaged and from a checkout. Mirrors `server-process.ts`. */
function binaryCandidates(): string[] {
  const name = process.platform === "win32" ? "kith.exe" : "kith";
  return [
    path.join(process.resourcesPath ?? "", "kith-cli", name),
    path.resolve(__dirname, "../../../server/dist/kith", name),
  ];
}

function findBinary(): string | null {
  for (const candidate of binaryCandidates()) {
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // Next.
    }
  }
  return null;
}

export type CliStatus = {
  /** Is the CLI shipped with this build at all? False in a checkout that has not frozen it. */
  available: boolean;
  /** Is there a link at the well-known place pointing at *this* app's binary? */
  installed: boolean;
  /**
   * There is a link, and it points somewhere else — an older Kith, or a copy of the app that
   * has since been moved or deleted. Distinct from `installed: false` because the fix is
   * different: this one needs replacing, not creating, and saying "not installed" about a
   * broken link sends someone looking for a command that is right there and does not work.
   */
  stale: boolean;
  /** Where it would go, so the interface can show it without knowing the convention. */
  link: string;
  /** Where it would point. Empty when nothing is shipped. */
  target: string;
};

export function cliStatus(): CliStatus {
  const target = findBinary();
  const status: CliStatus = {
    available: target !== null,
    installed: false,
    stale: false,
    link: LINK,
    target: target ?? "",
  };
  let current: string;
  try {
    current = fs.readlinkSync(LINK);
  } catch {
    // No link, or a real file sitting there. Either way nothing of ours is installed, and a
    // real file is somebody else's `kith` that we must not quietly replace.
    return status;
  }
  if (target !== null && path.resolve(current) === path.resolve(target)) {
    status.installed = true;
  } else {
    status.stale = true;
  }
  return status;
}

export type InstallResult = {
  ok: boolean;
  /** Empty on success; on failure, what to tell the person. */
  error: string;
  /** True when the person dismissed the password dialog — not a failure to report as one. */
  cancelled: boolean;
  status: CliStatus;
};

/**
 * Link the shipped CLI into /usr/local/bin, asking for an administrator password.
 *
 * `osascript ... with administrator privileges` rather than bundling a privileged helper: one
 * prompt, no installed daemon, and the dialog is macOS's own — which matters, because a custom
 * window asking for an admin password is indistinguishable from the thing people are told
 * never to trust.
 *
 * `mkdir -p` is in the same elevated command deliberately. /usr/local/bin exists on most Macs
 * and is absent on a clean one that has never had Homebrew or an installer touch it, and
 * discovering that *after* spending the password is a second prompt for one job.
 */
export async function installCli(): Promise<InstallResult> {
  const status = cliStatus();
  if (!status.available) {
    return {
      ok: false,
      cancelled: false,
      error: "this build does not ship the command line",
      status,
    };
  }
  if (status.installed) {
    return { ok: true, cancelled: false, error: "", status };
  }

  // Quoted for the shell *inside* the AppleScript string, then escaped for AppleScript. An
  // app installed at a path with a space in it — "/Applications/Kith 2.app" — is ordinary, and
  // an unquoted path there produces a link to a directory that does not exist.
  const shell = `mkdir -p /usr/local/bin && ln -sf '${status.target}' '${LINK}'`;
  const script = `do shell script "${shell.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}" with administrator privileges`;

  try {
    await run(`osascript -e ${JSON.stringify(script)}`);
  } catch (error) {
    const message = String((error as Error).message ?? error);
    // -128 is AppleScript for "the user cancelled". Reported separately because dismissing a
    // password dialog is a decision, and showing it back as an error is how an app argues with
    // somebody who already answered.
    if (message.includes("-128") || message.includes("User canceled")) {
      return { ok: false, cancelled: true, error: "", status: cliStatus() };
    }
    return { ok: false, cancelled: false, error: message, status: cliStatus() };
  }

  const after = cliStatus();
  return {
    ok: after.installed,
    cancelled: false,
    error: after.installed ? "" : "the link was not created",
    status: after,
  };
}

/** Remove the link, if it is ours. Same prompt, same reasoning. */
export async function removeCli(): Promise<InstallResult> {
  const status = cliStatus();
  if (!status.installed && !status.stale) {
    return { ok: true, cancelled: false, error: "", status };
  }
  const script = `do shell script "rm -f '${LINK}'" with administrator privileges`;
  try {
    await run(`osascript -e ${JSON.stringify(script)}`);
  } catch (error) {
    const message = String((error as Error).message ?? error);
    if (message.includes("-128") || message.includes("User canceled")) {
      return { ok: false, cancelled: true, error: "", status: cliStatus() };
    }
    return { ok: false, cancelled: false, error: message, status: cliStatus() };
  }
  return { ok: true, cancelled: false, error: "", status: cliStatus() };
}
