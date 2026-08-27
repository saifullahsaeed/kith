import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The shell's token, found by walking up rather than by counting `..`.
 *
 * This is the bug that made the menu bar say "nothing running" while he was mid-turn. The
 * checkout candidate was `../../server/data/api.token`, written when this file was expected to
 * sit one level shallower than it compiles to — so it resolved to `desktop/server/data`, which
 * does not exist, and fell through to `~/.kith/api.token`. That file *does* exist on any machine
 * that has ever run a packaged build, and belongs to a different server with a different token.
 * So every authenticated call the shell made came back 401 into a silent catch.
 *
 * Nothing looked broken because the one thing the shell does constantly — hold the event stream —
 * is on an open path and never needed a token.
 */
function tokenFor(dir: string, home: string): string[] {
  // The resolution under test, kept as data so the walk can be asserted without spawning Electron.
  const found: string[] = [];
  for (let at = dir, up = 0; up < 6; up += 1) {
    const parent = path.dirname(at);
    if (parent === at) break;
    at = parent;
    found.push(path.join(at, "server", "data", "api.token"));
  }
  found.push(path.join(home, ".kith", "api.token"));
  return found;
}

describe("finding the API token from a checkout", () => {
  it("reaches the repo's server/data from where the code actually compiles to", () => {
    // `src/server/api-token.ts` -> `out/server/api-token.js`. The old two-dot path landed here…
    const wrong = path.resolve("/repo/desktop/out/server", "../../server/data/api.token");
    expect(wrong).toBe("/repo/desktop/server/data/api.token");
    // …and the walk reaches the real one instead.
    expect(tokenFor("/repo/desktop/out/server", "/home/me")).toContain(
      "/repo/server/data/api.token",
    );
  });

  it("survives the build output moving", () => {
    for (const compiled of [
      "/repo/desktop/out/server",
      "/repo/desktop/dist/src/server",
      "/repo/desktop/build/a/b/server",
    ]) {
      expect(tokenFor(compiled, "/home/me")).toContain("/repo/server/data/api.token");
    }
  });

  it("prefers a checkout over the packaged install's token", () => {
    // A developer's machine usually has both, and they are different secrets for different
    // servers. Taking the home one first is what produced 401s against the server being run.
    const order = tokenFor("/repo/desktop/out/server", "/home/me");
    expect(order.indexOf("/repo/server/data/api.token")).toBeLessThan(
      order.indexOf("/home/me/.kith/api.token"),
    );
  });

  it("stops walking rather than climbing to the filesystem root forever", () => {
    expect(tokenFor("/a/b", "/home/me").length).toBeLessThanOrEqual(7);
  });
});

describe("the real module", () => {
  afterEach(() => vi.resetModules());

  it("reads the file KITH_DATA_DIR names, ahead of everything else", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kith-token-"));
    fs.writeFileSync(path.join(dir, "api.token"), "  from-the-env  \n");
    vi.stubEnv("KITH_DATA_DIR", dir);
    try {
      const { apiHeaders } = await import("./api-token.js");
      expect(apiHeaders()["X-Kith-Token"]).toBe("from-the-env");
      // And it carries whatever else the caller wanted.
      expect(apiHeaders({ Accept: "text/event-stream" }).Accept).toBe("text/event-stream");
    } finally {
      vi.unstubAllEnvs();
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
