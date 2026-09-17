/**
 * A latch, and the bug it exists for.
 *
 * `ActivityRun` in the thread drew a chain of thought as a disclosure while it had tool calls *or*
 * was running, and as bare children otherwise. Both looks are wanted. The problem is the moment
 * between them: React reconciles by element type, so the condition flipping at the end of a turn
 * unmounted everything below it and built it again.
 *
 * What that costs is not cosmetic. `ReasoningRoot` promises in its own docstring that "the first
 * manual toggle takes over the open/close state permanently" — it keeps your choice in a `useState`
 * and falls back to `streaming` only while you have not made one. Remount it and that choice is
 * gone, so a reasoning block you opened mid-answer snapped shut the instant the turn finished, and
 * the guarantee held right up until the one moment anybody would notice it.
 */
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useLatched } from "./latch";

describe("a latch", () => {
  it("is false until the thing is true", () => {
    const { result } = renderHook(({ now }) => useLatched(now), {
      initialProps: { now: false },
    });
    expect(result.current).toBe(false);
  });

  it("is true from the first time the thing is true", () => {
    const { result, rerender } = renderHook(({ now }) => useLatched(now), {
      initialProps: { now: false },
    });
    rerender({ now: true });
    expect(result.current).toBe(true);
  });

  it("stays true after the thing stops being true", () => {
    /* The whole point. A chain that was running is a disclosure, and it does not stop being one
     * when the turn ends — because ceasing to be one means unmounting what is inside it. */
    const { result, rerender } = renderHook(({ now }) => useLatched(now), {
      initialProps: { now: true },
    });
    rerender({ now: false });
    expect(result.current).toBe(true);
  });

  it("starts false for something that was never true, so a reload renders the resting shape", () => {
    /* A chain read back from the transcript is not running and has no calls: it was never a
     * disclosure and must not become one. The latch is per-mount, which is what makes that work. */
    const { result } = renderHook(({ now }) => useLatched(now), { initialProps: { now: false } });
    expect(result.current).toBe(false);
  });
});
