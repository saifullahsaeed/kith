/**
 * Starting the Kith server, and stopping it again.
 *
 * The shell never did this. Its own comment said the backend "runs as its own process
 * (Docker today)" — and Docker is gone, so what that actually meant was: the user starts a
 * Python server by hand, or the app opens and sits there. For something meant to be
 * installed by a friend and double-clicked, that is not a gap in the packaging, it is the
 * whole thing being missing.
 *
 * So the server is frozen into a binary (see `server/kith-server.spec`), shipped in
 * Resources, and spawned from here.
 *
 * Four decisions worth knowing:
 *
 * **It does not start one if one is already answering.** A developer running
 * `python app.py` in a terminal must not get a second server fighting over the port and the
 * databases — SQLite would let them both open the file and the loser's writes would vanish.
 *
 * **stdout goes to a log file, not nowhere.** A packaged app has no terminal, and the first
 * question when it does not work is always "what did the server say". Nowhere is the answer
 * that costs an evening.
 *
 * **It is killed on quit, and killed properly.** An orphaned server keeps the agent ticking,
 * spending money, with no window to see it in — and holds the port so the next launch finds
 * "one already answering" and attaches to a copy the user cannot control.
 *
 * **A crash is reported, not retried forever.** One restart covers a transient fault; a loop
 * would hide a server that cannot start at all behind an app that merely seems slow.
 */

import { type ChildProcess, spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { app } from "electron";

import { isBackendUp } from "./backend";

/** Where the frozen server lives, in a packaged app and in a checkout. */
function binaryCandidates(): string[] {
  const name = process.platform === "win32" ? "kith-server.exe" : "kith-server";
  return [
    // Packaged: electron-builder copies the bundle into Resources.
    path.join(process.resourcesPath ?? "", "kith-server", name),
    // A checkout that has run the freeze, so `npm start` behaves like the real thing.
    path.resolve(__dirname, "../../server/dist/kith-server", name),
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

/** Where his databases go. Matches the frozen server's own default, so the two agree. */
function dataDir(): string {
  return process.env.KITH_DATA_DIR ?? path.join(os.homedir(), ".kith");
}

let child: ChildProcess | null = null;
let logStream: fs.WriteStream | null = null;
let restarts = 0;

/**
 * Start the server if nothing is answering yet.
 *
 * Returns what happened, so startup can say something true rather than a generic "could not
 * reach the server": "already running" and "there is no server binary in this build" are
 * completely different problems with completely different fixes.
 */
export async function ensureServer(): Promise<"already-running" | "started" | "no-binary"> {
  if (await isBackendUp()) return "already-running";

  const binary = findBinary();
  if (!binary) return "no-binary";

  const directory = dataDir();
  fs.mkdirSync(directory, { recursive: true });
  const logPath = path.join(directory, "server.log");
  // Appended, not truncated: the log of the run that failed is the one you want, and
  // truncating on launch destroys it at exactly the moment the user reopens the app to
  // find out what went wrong.
  logStream = fs.createWriteStream(logPath, { flags: "a" });
  logStream.write(`\n=== ${new Date().toISOString()} launching ${binary} ===\n`);

  child = spawn(binary, [], {
    cwd: path.dirname(binary),
    env: { ...process.env, KITH_DATA_DIR: directory },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout?.pipe(logStream);
  child.stderr?.pipe(logStream);

  child.on("exit", (code, signal) => {
    logStream?.write(`=== exited code=${code} signal=${signal} ===\n`);
    child = null;
    if (app.isReady() && !quitting && restarts < 1) {
      // Once. A loop here would turn "the server cannot start" into "the app is slow",
      // which is the same bug wearing a disguise that takes much longer to see through.
      restarts += 1;
      console.warn(`[kith] server exited (${code ?? signal}); restarting once`);
      void ensureServer();
    }
  });

  console.log(`[kith] started server from ${binary}, logging to ${logPath}`);
  return "started";
}

let quitting = false;

/**
 * Stop the server we started.
 *
 * SIGTERM first so it can close its databases, then SIGKILL if it is still there. Killing a
 * SQLite writer outright can leave a hot journal, and while SQLite recovers from that on the
 * next open, an unclean shutdown every single time is not something to design in.
 */
export function stopServer(): void {
  quitting = true;
  const running = child;
  child = null;
  if (!running || running.exitCode !== null) {
    logStream?.end();
    return;
  }
  running.kill("SIGTERM");
  const deadline = setTimeout(() => {
    if (running.exitCode === null) running.kill("SIGKILL");
  }, 3_000);
  running.once("exit", () => {
    clearTimeout(deadline);
    logStream?.end();
  });
}

