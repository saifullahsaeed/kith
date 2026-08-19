/**
 * An errand's steps arrive as a flat event log, and the panel shows them as a list per errand.
 *
 * The fold between those two shapes is the whole of this component's logic, and it is the kind
 * of thing that looks obviously right while silently dropping the last step or merging two
 * workers into one. It matters more here than it would elsewhere: this is the *only* place a
 * sub-agent's work is ever shown. Its greps and reads are discarded by design and reach the
 * chat thread nowhere, so a fold that loses them loses them for good.
 *
 * Three scouts sharing a round is the case worth pinning down, because it is the case the
 * feature exists for and the one a naive fold gets wrong: the lines interleave.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Errands, foldErrands, isErrandLine } from "./errands";
import type { ActivityItem } from "@/lib/backend/activity";

const at = "2026-08-19T10:00:00Z";

function line(
  id: string,
  state: "running" | "step" | "done",
  text: string,
  extra: Partial<ActivityItem> & { objective?: string } = {},
): ActivityItem {
  const { objective, ...rest } = extra;
  return {
    kind: "errand",
    text,
    at,
    conversation: "conv-1",
    // Carried on every line, not only the opening one, because the panel folds a live feed
    // and may well see a step before it sees the errand that owns it.
    errand: { id, state, objective: objective ?? `find out about ${id}` },
    ...rest,
  };
}

describe("folding the feed into errands", () => {
  it("keeps three interleaved errands apart", () => {
    const folded = foldErrands(
      [
        line("a", "running", "find out about a"),
        line("b", "running", "find out about b"),
        line("a", "step", "grep", { tool: "grep", args: { pattern: "fold" } }),
        line("b", "step", "read_file", { tool: "read_file", args: { path: "b.py" } }),
        line("a", "step", "read_file", { tool: "read_file", args: { path: "a.py" } }),
        line("b", "done", "read_file"),
      ],
      "conv-1",
    );

    expect(folded.map((one) => one.id)).toEqual(["a", "b"]);
    expect(folded[0].steps.map((s) => s.tool)).toEqual(["grep", "read_file"]);
    expect(folded[0].running).toBe(true);
    expect(folded[1].steps.map((s) => s.tool)).toEqual(["read_file"]);
    expect(folded[1].running).toBe(false);
  });

  it("closes an errand and keeps what it cost", () => {
    const folded = foldErrands(
      [line("a", "running", "x"), line("a", "step", "grep", { tool: "grep" }), line("a", "done", "grep x3, read_file x8")],
      "conv-1",
    );

    expect(folded[0].running).toBe(false);
    expect(folded[0].tally).toBe("grep x3, read_file x8");
  });

  it("ignores another conversation's errands", () => {
    const folded = foldErrands(
      [line("a", "running", "x"), { ...line("b", "running", "y"), conversation: "conv-2" }],
      "conv-1",
    );

    expect(folded.map((one) => one.id)).toEqual(["a"]);
  });

  it("does not count as one of the turn's rounds", () => {
    // A worker's nine tool calls are not nine rounds of the conversation, and counting them
    // made a single delegation read as nine steps in the panel header.
    expect(isErrandLine(line("a", "step", "grep"))).toBe(true);
    expect(isErrandLine({ kind: "tool", text: "grep", at })).toBe(false);
  });
});

describe("the section itself", () => {
  it("renders nothing when nobody has been sent anywhere", () => {
    const { container } = render(<Errands lines={[]} conversationId="conv-1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a running errand's steps in English, not as function calls", () => {
    render(
      <Errands
        lines={[
          line("a", "running", "x", { objective: "where the fold is triggered from" }),
          line("a", "step", "grep", {
            objective: "where the fold is triggered from",
            tool: "grep",
            args: { pattern: "_FOLD_ABOVE_SHARE" },
          }),
        ]}
        conversationId="conv-1"
      />,
    );

    expect(screen.getByText("where the fold is triggered from")).toBeTruthy();
    expect(screen.getByText(/searched files for/)).toBeTruthy();
    expect(screen.getByText(/_FOLD_ABOVE_SHARE/)).toBeTruthy();
  });

  it("drops the step list once the report is in the thread, and keeps the tally", () => {
    render(
      <Errands
        lines={[
          line("a", "running", "where the fold is"),
          line("a", "step", "grep", { tool: "grep", args: { pattern: "fold" } }),
          line("a", "done", "grep x3, read_file x8"),
        ]}
        conversationId="conv-1"
      />,
    );

    expect(screen.queryByText(/searched files for/)).toBeNull();
    expect(screen.getByText("grep x3, read_file x8")).toBeTruthy();
  });
});
