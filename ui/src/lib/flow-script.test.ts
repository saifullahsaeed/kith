/**
 * Which of the two mermaid renderers a fence goes to.
 *
 * One regex over the head of a string, and the reason it is worth a test file is that it runs on
 * every code block in every reply and decides something visible. A false positive sends a still
 * diagram down a path that will not animate it; a false negative silently ignores choreography
 * he wrote. Both look like the feature is broken rather than like a predicate is wrong.
 */
import { describe, expect, it } from "vitest";

import { hasFlowScript, loopsForever } from "@/lib/flow-script";

const diagram = "flowchart LR\n  Client --> LB\n  LB --> A";

describe("a mermaid fence asking to move", () => {
  it("is a flow: key in the frontmatter", () => {
    expect(hasFlowScript(`---\nflow:\n  loop:\n    - route: [Client, LB]\n---\n${diagram}`)).toBe(
      true,
    );
  });

  it("is not an ordinary diagram", () => {
    expect(hasFlowScript(diagram)).toBe(false);
  });

  it("is not frontmatter that says something else", () => {
    // The key mermaid frontmatter is normally used for. Sending these to the animator would mean
    // every titled diagram in the app waiting on the settle window for nothing.
    expect(hasFlowScript(`---\ntitle: How a request lands\n---\n${diagram}`)).toBe(false);
    expect(hasFlowScript(`---\nconfig:\n  theme: base\n---\n${diagram}`)).toBe(false);
  });

  it("survives the newline a fence arrives with", () => {
    expect(hasFlowScript(`\n---\nflow:\n  loop:\n    - route: [A, B]\n---\n${diagram}`)).toBe(true);
  });

  it("is already true before he has closed the block", () => {
    // The ordinary state of a fence mid-stream. Deciding late would mean the still renderer
    // drawing it and the animated one taking over a beat after the reply finished.
    expect(hasFlowScript("---\nflow:\n  loop:\n    - route: [Client, ")).toBe(true);
  });

  it("is not a node in the diagram that happens to be called flow", () => {
    expect(hasFlowScript(`---\ntitle: t\n---\nflowchart LR\n  flow: --> B`)).toBe(false);
    expect(hasFlowScript("flowchart LR\n  flow: --> B")).toBe(false);
  });

  it("is not a flow: nested under another key", () => {
    // Indented, so not the top-level key the animator reads — and a script it would not run.
    expect(hasFlowScript(`---\nconfig:\n  flow: yes\n---\n${diagram}`)).toBe(false);
  });

  it("needs the dashes on a line of their own, at the top", () => {
    expect(hasFlowScript(`--- flow:\n${diagram}`)).toBe(false);
    expect(hasFlowScript(`${diagram}\n---\nflow:\n  loop: []\n---`)).toBe(false);
  });
});

describe("and whether it repeats", () => {
  const script = (key: string) =>
    `---\nflow:\n  ${key}:\n    - route: [Client, LB]\n---\n${diagram}`;

  it("plays once when he wrote steps:", () => {
    // The default, and the one he is told to write. Both names mean the same list to the
    // library; here they are made to mean what they say.
    expect(loopsForever(script("steps"))).toBe(false);
  });

  it("repeats when he wrote loop:", () => {
    expect(loopsForever(script("loop"))).toBe(true);
  });

  it("is not a repeat just because the word appears in the diagram", () => {
    expect(loopsForever(`---\nflow:\n  steps:\n    - route: [A, B]\n---\nflowchart LR\n  loop: --> B`)).toBe(
      false,
    );
    expect(loopsForever(diagram)).toBe(false);
  });

  it("is nothing at all for a fence with no script", () => {
    expect(loopsForever(`---\ntitle: t\n---\n${diagram}`)).toBe(false);
  });
});
