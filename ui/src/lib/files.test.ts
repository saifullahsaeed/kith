/**
 * Where a link he wrote actually goes.
 *
 * Two failures, one cause. Clicking `[report](/Users/you/Kith/report.html)` opened a second
 * copy of Kith in the browser, and clicking `Staff-SAIF.xlsx` said the file did not exist
 * about a file that did. Both came from this file deciding, by regular expression, which
 * strings are his files — and answering no to the shapes he actually writes.
 *
 * The browser one is worth spelling out, because every layer behaved correctly:
 *
 *   1. `<a href="/Users/you/Kith/report.html">` is a *relative* URL. The browser resolves it
 *      against this page's origin: `http://127.0.0.1:8756/Users/you/Kith/report.html`.
 *   2. The SPA serves `index.html` for every unknown route, because BrowserRouter needs a
 *      reload on `/tasks` to work (`api/spa.py`).
 *   3. `target="_blank"` makes Electron's window-open handler hand the URL to the OS, and
 *      the scheme is http, so it is allowed (`desktop/src/window/window.ts`).
 *
 * A link to a file opens a browser showing the app. Nothing logs anything.
 *
 * So the default is inverted here: the browser gets a link only when it is genuinely a web
 * address, and everything else is treated as one of his files. A viewer that says it cannot
 * find something is a true answer; the old one was not.
 */

import { describe, expect, it } from "vitest";

import { linkTarget, looksLikeHisFile } from "./files";

describe("what a markdown link points at", () => {
  it("sends a real address to the browser", () => {
    expect(linkTarget("https://anthropic.com")).toEqual({
      kind: "url",
      href: "https://anthropic.com",
    });
    expect(linkTarget("mailto:someone@example.com").kind).toBe("url");
  });

  it("keeps a same-page jump in the page", () => {
    // `target="_blank"` on one of these opens a second copy of the app, scrolled to a heading.
    expect(linkTarget("#the-bit-about-cost")).toEqual({
      kind: "anchor",
      href: "#the-bit-about-cost",
    });
  });

  it("treats an absolute path as a file, not as a route on this origin", () => {
    // The failure in the screenshot.
    expect(linkTarget("/Users/you/Kith/inbox/Staff-SAIF.xlsx")).toEqual({
      kind: "file",
      path: "/Users/you/Kith/inbox/Staff-SAIF.xlsx",
    });
  });

  it("treats a relative path as a file", () => {
    expect(linkTarget("work/plan.md")).toEqual({ kind: "file", path: "work/plan.md" });
    expect(linkTarget("./diagram-test.mmd").kind).toBe("file");
  });

  it("opens a file: url rather than handing over one nothing will accept", () => {
    // Chromium refuses a file: navigation from an http: page, and the desktop shell's scheme
    // allowlist refuses to hand one to the OS. The click did nothing at all, twice over.
    expect(linkTarget("file:///Users/you/Kith/notes.md")).toEqual({
      kind: "file",
      path: "/Users/you/Kith/notes.md",
    });
  });

  it("undoes the escaping a link needs and a path does not", () => {
    expect(linkTarget("inbox/Q3%20report.md")).toEqual({
      kind: "file",
      path: "inbox/Q3 report.md",
    });
  });

  it("drops the fragment, because only the file can be opened", () => {
    expect(linkTarget("notes.md#costs")).toEqual({ kind: "file", path: "notes.md" });
  });

  it("leaves a protocol-relative address alone", () => {
    expect(linkTarget("//example.com/x").kind).toBe("url");
  });

  it("sends an unrecognised shape to the viewer rather than to the browser", () => {
    // Not a path anyone can open, and that is fine: the viewer says so. The alternative was
    // navigating to it and being served the app, which said nothing at all.
    expect(linkTarget("somewhere").kind).toBe("file");
  });
});

describe("a path he wrote in backticks", () => {
  it("recognises the absolute paths he actually writes now", () => {
    // This is the half that made the other half worse: the full path stayed inert while the
    // bare filename beside it became clickable, so the only thing you could click was the
    // only one that could not be found.
    expect(looksLikeHisFile("/Users/you/Kith/inbox/Staff-SAIF.xlsx")).toBe(true);
  });

  it("still recognises the sandbox paths in his older messages", () => {
    expect(looksLikeHisFile("/home/kith/work/task-76.md")).toBe(true);
    expect(looksLikeHisFile("~/Kith/notes.md")).toBe(true);
    expect(looksLikeHisFile("work/plan.md")).toBe(true);
  });

  it("still leaves ordinary writing alone", () => {
    // The reason this is a pattern and not "anything with a dot".
    expect(looksLikeHisFile("object.method")).toBe(false);
    expect(looksLikeHisFile("TypeScript 5.4")).toBe(false);
    expect(looksLikeHisFile("and/or")).toBe(false);
    expect(looksLikeHisFile("https://example.com/a.md")).toBe(false);
  });
});
