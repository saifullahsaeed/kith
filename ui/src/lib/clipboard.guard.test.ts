import { describe, expect, it } from "vitest";

/**
 * `navigator.clipboard.writeText` may only be called in one place.
 *
 * This has now been the same bug three times. `writeText` rejects with `NotAllowedError` in an
 * Electron webview — the document is not focused, or the permission is simply not granted — and
 * every caller that reached for it directly got one of two failures: a button that did nothing at
 * all and logged nothing (the code-block header), or a tick shown over an empty clipboard (the
 * reply button, then the context message row). Each was found separately, by a person noticing
 * that what they pasted was wrong.
 *
 * `copyText` in `lib/files.ts` tries the async clipboard, falls back to `execCommand` on a
 * different permission route, and returns whether either actually worked. Everything that copies
 * text goes through it, and the tick is shown on a `true` and not before.
 *
 * `clipboard.write` (images, from the mermaid toolbar) and `clipboard.readText` (paste, from the
 * context menu) are deliberately not covered: neither has an `execCommand` fallback that Chromium
 * still permits, and both already handle their own failure.
 */
const SOURCES = import.meta.glob("/src/**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const ALLOWED = "/src/lib/files.ts";

/** Comments discuss `navigator.clipboard.writeText` at length — deliberately, since explaining
 *  why it is not used is the point. Only actual calls count. */
function code(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("copying text", () => {
  it("goes through copyText everywhere, so a denied clipboard cannot pass for a copy", () => {
    const offenders = Object.entries(SOURCES)
      .filter(([path]) => !path.endsWith(".test.ts") && !path.endsWith(".test.tsx"))
      .filter(([, src]) => code(src).includes("navigator.clipboard.writeText"))
      .map(([path]) => path)
      .filter((path) => path !== ALLOWED);
    expect(offenders).toEqual([]);
  });

  it("still has the one allowed implementation, so this cannot pass by deleting it", () => {
    // Otherwise the guard above goes green the day someone removes copying altogether.
    expect(Object.keys(SOURCES).length).toBeGreaterThan(50);
    const src = code(SOURCES[ALLOWED] ?? "");
    expect(src).toContain("navigator.clipboard.writeText");
    expect(src).toContain('execCommand("copy")');
  });
});
