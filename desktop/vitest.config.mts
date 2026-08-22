import { defineConfig } from "vitest/config";

/**
 * Tests for the main process, which had none.
 *
 * The shell is deliberately thin and most of it is Electron API calls that only mean anything
 * inside Electron — which is why there was nothing here to test. `server/sse.ts` is the exception:
 * holding the event stream in the main process means parsing SSE by hand, since there is no
 * `EventSource` out here to do it, and incremental parsing is exactly the code that works on every
 * frame you try by hand and fails on the one split across two reads.
 *
 * Node environment, not jsdom: nothing under test touches a DOM, and pulling one in would be
 * pretending this code runs somewhere it does not.
 *
 * `.mts` rather than `.ts`: this package emits CommonJS (Electron's main process and sandboxed
 * preloads cannot load ESM — see tsconfig.json), so a `.ts` config written in ESM is loaded as
 * CommonJS and warns. The extension is the whole fix.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
