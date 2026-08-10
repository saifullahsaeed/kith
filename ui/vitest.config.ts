/**
 * The harness that should have existed before any of this interface was written.
 *
 * Five UI changes shipped broken in one day — a crashing composer, a slash menu whose Root did
 * not contain its input, multi-select that was silently always single, an empty div earning a
 * gap, a list that would not scroll to its own selection. Every one of them passed `tsc`, and
 * `tsc` was the only gate. Types agreeing is not the same as a component working, and the
 * difference had been landing on whoever was looking at the screen.
 *
 * Separate from `vite.config.ts` on purpose: that file's `server.proxy` block reads the API
 * token off disk at request time, which is machinery a test run has no use for.
 */
import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // A component test that takes five seconds is a component test with a real timer in it.
    testTimeout: 5000,
  },
});
