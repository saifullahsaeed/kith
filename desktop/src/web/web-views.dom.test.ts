import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import ts from "typescript";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

/**
 * The browser's tabs, run for real: `web-views.ts` itself, in Electron, against pages served on
 * loopback.
 *
 * Everything else in `web-views.test.ts` is arithmetic, because this file is Electron end to end.
 * Tabs are not arithmetic — they are which view is in front, what a `target=_blank` link does,
 * what closing the front tab brings forward — and the only honest test of that is to open real
 * views and click real links. One Electron launch through `scripts/views-harness.js`; skipped
 * where there is no Electron binary to launch.
 */

const root = join(__dirname, "../..");
const electron = (() => {
  try {
    return join(root, "node_modules/electron/dist", readFileSync(join(root, "node_modules/electron/path.txt"), "utf8"));
  } catch {
    return "";
  }
})();

const CASES: Record<string, string> = {
  opensOneTab: `
    const first = await views.navigate({ owner: "c" }, page("/"));
    return { tab: first.tab, tabs: first.tabs.map((t) => t.title) };`,
  aNewTabLinkOpensATab: `
    await views.navigate({ owner: "c" }, page("/"));
    const clicked = await views.click({ owner: "c" }, { text: "Two" });
    const read = await views.read({ owner: "c" });
    return { clicked: clicked.clicked, tab: clicked.tab, titles: clicked.tabs.map((t) => t.title), text: read.text };`,
  anOrdinaryLinkStaysInItsTab: `
    await views.navigate({ owner: "c" }, page("/"));
    const clicked = await views.click({ owner: "c" }, { text: "Three" });
    return { tabs: clicked.tabs.length, title: clicked.title };`,
  switchingAndClosing: `
    await views.navigate({ owner: "c" }, page("/"));
    await views.navigate({ owner: "c" }, page("/two"), { newTab: true });
    await views.navigate({ owner: "c" }, page("/three"), { newTab: true });
    const back = views.switchTab({ owner: "c" }, "t1");
    const read = await views.read({ owner: "c" });
    const closed = views.closeTab({ owner: "c" }, "t1");
    const last = views.closeTab({ owner: "c" });
    return {
      back: back.tab,
      read: read.title,
      afterClosingT1: closed.tab,
      order: closed.tabs.map((t) => t.id),
      afterClosingTheFront: last.tab,
      left: last.tabs.map((t) => t.id),
    };`,
  closingTheLastTabLeavesItEmpty: `
    await views.navigate({ owner: "c" }, page("/"));
    const empty = views.closeTab({ owner: "c" });
    let refused = "";
    try { await views.read({ owner: "c" }); } catch (e) { refused = e.message; }
    return { tabs: empty.tabs.length, tab: empty.tab, refused };`,
  anUnknownTabIsRefusedWithTheList: `
    await views.navigate({ owner: "c" }, page("/"));
    try { views.switchTab({ owner: "c" }, "t9"); return "switched"; } catch (e) { return e.message; }`,
  conversationsKeepTheirOwnTabs: `
    await views.navigate({ owner: "a" }, page("/"));
    await views.navigate({ owner: "b" }, page("/two"));
    await views.navigate({ owner: "b" }, page("/three"), { newTab: true });
    return { a: views.status({ owner: "a" }).tabs.length, b: views.status({ owner: "b" }).tabs.length };`,
  aBlankTabFromThePlus: `
    await views.navigate({ owner: "c" }, page("/"));
    const plus = await views.newTab({ owner: "c" });
    return { tab: plus.tab, tabs: plus.tabs.length, url: plus.url };`,
};

let results: Record<string, unknown> = {};
let scratch = "";

describe.skipIf(!electron || !existsSync(electron))("the browser's tabs, in real Electron", () => {
  beforeAll(() => {
    scratch = mkdtempSync(join(tmpdir(), "kith-views-"));
    const built = join(scratch, "src");
    // The three modules `web-views` needs, as CommonJS, and a placeholder where the app's window
    // module sits — the harness answers for it, but the path has to resolve.
    for (const [from, to] of [
      ["web/web-views.ts", "web/web-views.js"],
      ["web/page-scripts.ts", "web/page-scripts.js"],
      ["config.ts", "config.js"],
    ] as const) {
      const source = readFileSync(join(root, "src", from), "utf8");
      const out = ts.transpileModule(source, {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
      });
      mkdirSync(join(built, to, ".."), { recursive: true });
      writeFileSync(join(built, to), out.outputText);
    }
    mkdirSync(join(built, "window"), { recursive: true });
    writeFileSync(join(built, "window", "window.js"), "exports.getMainWindow = () => null;");
    writeFileSync(join(scratch, "job.json"), JSON.stringify({ dir: built, cases: CASES }));
    const env = { ...process.env };
    delete env.ELECTRON_RUN_AS_NODE;
    execFileSync(
      electron,
      [join(root, "scripts/views-harness.js"), join(scratch, "job.json"), join(scratch, "results.json")],
      { env, timeout: 120_000, stdio: "ignore" },
    );
    results = JSON.parse(readFileSync(join(scratch, "results.json"), "utf8")) as Record<string, unknown>;
  }, 150_000);

  afterAll(() => {
    if (scratch) rmSync(scratch, { recursive: true, force: true });
  });

  const result = <T,>(name: string): T => {
    const got = results[name] as { harnessError?: string } | undefined;
    expect(got?.harnessError, `${name} did not run`).toBeUndefined();
    return got as T;
  };

  it("opens a page in one tab", () => {
    expect(result("opensOneTab")).toEqual({ tab: "t1", tabs: ["One"] });
  });

  it("opens a target=_blank link in a new tab in front, and says so", () => {
    /* It used to replace the page the link was on — the page anybody opening a link in a new tab
       meant to keep — and a click that opened a tab read as a click that did nothing. */
    const got = result<{ clicked: string; tab: string; titles: string[]; text: string }>("aNewTabLinkOpensATab");
    expect(got.titles).toEqual(["One", "Two"]);
    expect(got.tab).toBe("t2");
    expect(got.clicked).toContain("opened in a new tab, t2");
    expect(got.text).toContain("the second page");
  });

  it("keeps an ordinary link in its own tab", () => {
    expect(result("anOrdinaryLinkStaysInItsTab")).toEqual({ tabs: 1, title: "Three" });
  });

  it("switches to a tab by id, and brings the neighbour forward when the front one closes", () => {
    expect(result("switchingAndClosing")).toEqual({
      back: "t1",
      read: "One",
      // t1 was in front and sat first, so the tab to its right comes forward.
      afterClosingT1: "t2",
      order: ["t2", "t3"],
      // Then t2 is in front, and closing it brings t3 — to its right — forward.
      afterClosingTheFront: "t3",
      left: ["t3"],
    });
  });

  it("leaves the browser empty when the last tab closes, and says a page has to be opened", () => {
    const got = result<{ tabs: number; tab: string; refused: string }>("closingTheLastTabLeavesItEmpty");
    expect(got.tabs).toBe(0);
    expect(got.tab).toBe("");
    expect(got.refused).toContain("`open` a page first");
  });

  it("refuses a tab that does not exist, naming the ones that do", () => {
    const said = result<string>("anUnknownTabIsRefusedWithTheList");
    expect(said).toContain("There is no tab t9");
    expect(said).toContain("t1 (One)");
  });

  it("gives each conversation its own tabs", () => {
    expect(result("conversationsKeepTheirOwnTabs")).toEqual({ a: 1, b: 2 });
  });

  it("opens a blank tab in front from the +", () => {
    const got = result<{ tab: string; tabs: number; url: string }>("aBlankTabFromThePlus");
    expect(got).toMatchObject({ tab: "t2", tabs: 2 });
  });
});
