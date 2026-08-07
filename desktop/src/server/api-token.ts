/**
 * The shell's copy of the API token.
 *
 * The Kith server requires a shared secret on every `/api` call (see
 * `server/kith/api/auth.py`). The shell is one of those callers — it registers itself as a
 * renderer at startup and unregisters on quit — so it needs the value too.
 *
 * It reads the file the server wrote rather than being handed the token: the shell is a
 * process running as the same user, which is exactly who the 0600 file is readable by, and
 * that means no extra channel has to exist for handing secrets around.
 *
 * The page inside the window is a different matter and needs nothing from here — when the
 * server serves the app it injects the token into the document itself.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

/** Where the server keeps it. `KITH_DATA_DIR` wins, matching the server's own resolution. */
function candidates(): string[] {
  const found: string[] = [];
  if (process.env.KITH_DATA_DIR) found.push(path.join(process.env.KITH_DATA_DIR, "api.token"));
  // Running from a checkout: server/data beside the desktop folder.
  found.push(path.resolve(__dirname, "../../server/data/api.token"));
  // A packaged install, where the server keeps its data under the user's home.
  found.push(path.join(os.homedir(), ".kith", "api.token"));
  return found;
}

let cached = "";

/**
 * The token, or "" if the server has not written one yet.
 *
 * Not cached until non-empty, on purpose: the shell frequently starts before the server has
 * finished booting, and remembering "" would leave every later call unauthenticated for the
 * lifetime of the app.
 */
function apiToken(): string {
  if (cached) return cached;
  for (const candidate of candidates()) {
    try {
      const value = fs.readFileSync(candidate, "utf8").trim();
      if (value) {
        cached = value;
        return value;
      }
    } catch {
      // Next candidate.
    }
  }
  return "";
}

/** Headers for a call to the Kith API, with the token when there is one. */
export function apiHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = apiToken();
  return token ? { ...extra, "X-Kith-Token": token } : extra;
}
