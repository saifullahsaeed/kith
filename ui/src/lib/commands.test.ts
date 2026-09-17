/**
 * What `/design` actually asks for.
 *
 * A string constant is not usually worth a test. This one is, because every clause in it is
 * load-bearing and each was learned from a turn that went wrong without it:
 *
 * * **the skill**, or he writes a screen in whatever markup occurs to him and it opens in the
 *   editor with no handles on it;
 * * **several files**, or he writes one screen — the reasonable reading of "design this", and
 *   the one that leaves a board with a single frame on it and nothing to compare;
 * * **attached to a task**, or the files exist on disk and reach no board at all, which is the
 *   shape of the failure that started this: eight finished screens and a paragraph about them.
 *
 * Deleting a clause here is easy, reads as tidying, and quietly turns the command back into the
 * generic `/skill` prefix it exists to be better than.
 */
import { describe, expect, it } from "vitest";

import { DESIGN_PROMPT } from "@/lib/commands";

describe("the /design prompt", () => {
  it("names the skill, so the dialect is loaded before anything is written", () => {
    expect(DESIGN_PROMPT).toContain("designing-a-screen");
  });

  it("asks for options rather than a screen", () => {
    expect(DESIGN_PROMPT).toMatch(/options rather than one/i);
  });

  it("asks for a file each, which is what puts them side by side", () => {
    expect(DESIGN_PROMPT).toMatch(/own HTML file/i);
  });

  it("asks for them to be attached, which is what makes a board exist", () => {
    expect(DESIGN_PROMPT).toMatch(/attached to a\s+task/i);
    expect(DESIGN_PROMPT).toMatch(/deliverable/i);
  });

  it("asks him to check them before attaching", () => {
    expect(DESIGN_PROMPT).toMatch(/check/i);
  });

  it("ends open, because the subject is still to be typed", () => {
    // Written into the composer, not sent. A prompt that ended in a full stop would read as the
    // whole request and get sent as one.
    expect(DESIGN_PROMPT.trimEnd().endsWith(":")).toBe(true);
    expect(DESIGN_PROMPT.endsWith(" ")).toBe(true);
  });
});
