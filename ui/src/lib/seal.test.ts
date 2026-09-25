/**
 * The seal on a plugin surface, which is the one sealed frame left.
 *
 * Carried over from the retired `canvas.test.ts`: the sandbox must not be widened for
 * convenience. `server/tests/test_the_seal_is_stated_once.py` checks the server agrees.
 */
import { describe, expect, it } from "vitest";

import { FRAME_SANDBOX, frameTokens } from "@/lib/seal";
import { PALETTE } from "@/lib/kith-palette";

describe("the seal", () => {
  it("runs scripts and nothing else", () => {
    expect(FRAME_SANDBOX).toBe("allow-scripts");
  });

  it("never hands the frame the app's origin", () => {
    expect(FRAME_SANDBOX).not.toContain("allow-same-origin");
  });
});

describe("frameTokens", () => {
  it("carries all nine, each with a value", () => {
    const tokens = frameTokens(PALETTE.dark as unknown as Record<string, string>);
    expect(Object.keys(tokens)).toHaveLength(9);
    for (const value of Object.values(tokens)) expect(value).toBeTruthy();
  });
});
