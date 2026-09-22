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

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { forgetMissingPaths, linkTarget, looksLikeHisFile, useOpenable } from "./files";

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

/**
 * Whether a path he wrote is worth offering as a click.
 *
 * The question behind two screenshots. The interface used to offer every path-shaped thing he
 * wrote and find out on the click — a viewer saying *there's no …*, or a list of seven files
 * of the same name — and both of those were made nicer without being made fewer. A name that
 * leads nowhere should not have been a link.
 *
 * Only the server can answer it: the path is relative to a project, and it may resolve by a
 * search rather than by existing where it was written. So the shape of this is a question sent
 * and an answer that arrives later, which is what these are about.
 */
describe("offering a path as a link", () => {
  let asked: string[][];

  beforeEach(() => {
    // The answers are cached for the life of the module, deliberately — so each case below
    // uses a path of its own rather than a shared one it would find already answered.
    forgetMissingPaths();
    asked = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: { body?: string }) => {
        const paths = (JSON.parse(init?.body ?? "{}") as { paths: string[] }).paths;
        asked.push(paths);
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              resolved: Object.fromEntries(
                paths.map((path) => [path, { openable: !path.includes("home.html") }]),
              ),
            }),
        });
      }),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("offers it while nobody has asked yet", async () => {
    // Starting at "no" would make every file he names flicker into a link a moment later.
    const { result } = renderHook(() => useOpenable("inbox/before.xlsx"));
    expect(result.current).toBe("unknown");
    // Let the question it just queued settle, so its request does not land inside the next test.
    await waitFor(() => expect(result.current).not.toBe("unknown"));
  });

  it("keeps offering one that leads somewhere", async () => {
    const { result } = renderHook(() => useOpenable("inbox/present.xlsx"));
    await waitFor(() => expect(result.current).toBe("yes"));
  });

  it("stops offering one that leads nowhere", async () => {
    // The first screenshot, decided before the click instead of after it.
    const { result } = renderHook(() => useOpenable(".kith/screens/home.html"));
    await waitFor(() => expect(result.current).toBe("no"));
  });

  it("asks once for a paragraph's worth of mentions", async () => {
    const { result } = renderHook(() => {
      useOpenable("a.md");
      useOpenable("b.md");
      return useOpenable("c.md");
    });
    await waitFor(() => expect(result.current).toBe("yes"));
    // A miss costs a walk of his folders on the server, so three of them in series to draw
    // one message is its own bug.
    expect(asked).toHaveLength(1);
    expect(asked[0]).toEqual(["a.md", "b.md", "c.md"]);
  });

  it("does not ask twice about the same path", async () => {
    const first = renderHook(() => useOpenable("inbox/asked-once.xlsx"));
    await waitFor(() => expect(first.result.current).toBe("yes"));
    const second = renderHook(() => useOpenable("inbox/asked-once.xlsx"));
    expect(second.result.current).toBe("yes");
    expect(asked).toHaveLength(1);
  });

  it("offers it anyway when the question could not be asked", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    const { result } = renderHook(() => useOpenable("whatever.md"));
    // Not an answer, so not a refusal. Swallowing it would turn every file he names into
    // plain text for no reason a person could see.
    await waitFor(() => expect(result.current).toBe("yes"));
  });

  it("re-asks about a miss once a turn has done some work", async () => {
    // He writes the path in the sentence before he writes the file.
    const { result, rerender } = renderHook(() => useOpenable(".kith/screens/later-home.html"));
    await waitFor(() => expect(result.current).toBe("no"));
    act(() => forgetMissingPaths());
    rerender();
    await waitFor(() => expect(asked).toHaveLength(2));
  });

  it("keeps the hits when it forgets the misses", async () => {
    const { result } = renderHook(() => useOpenable("inbox/kept.xlsx"));
    await waitFor(() => expect(result.current).toBe("yes"));
    act(() => forgetMissingPaths());
    expect(result.current).toBe("yes");
    expect(asked).toHaveLength(1);
  });
});
