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
import { fireEvent, render, screen } from "@testing-library/react";
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

describe("a long objective does not take the column", () => {
  it("folds a running errand's objective instead of rendering all of it", () => {
    // The bug a builder found. `send_builder`'s description tells the worker to say exactly
    // what to change and in which files, so a *good* builder objective is hundreds of words —
    // and the panel had no clamp on a running errand at all, only on a finished one.
    const long = "TASK: ".concat("add a docstring saying why this exists. ".repeat(40));
    render(
      <Errands
        lines={[
          {
            kind: "errand",
            text: long,
            at: "2026-09-15T20:18:00Z",
            conversation: "c1",
            errand: { id: "w1", state: "running", objective: long, role: "builder" },
          },
        ]}
        conversationId="c1"
      />,
    );
    const head = screen.getByRole("button", { expanded: false });
    expect(head.className).toContain("line-clamp-2");
  });

  it("opens the whole objective on click, and folds it again", async () => {
    const long = "change every call site. ".repeat(40);
    render(
      <Errands
        lines={[
          {
            kind: "errand",
            text: long,
            at: "2026-09-15T20:18:00Z",
            conversation: "c1",
            errand: { id: "w1", state: "running", objective: long, role: "builder" },
          },
        ]}
        conversationId="c1"
      />,
    );
    const head = screen.getByRole("button", { expanded: false });
    fireEvent.click(head);
    expect(screen.getByRole("button", { expanded: true }).className).not.toContain("line-clamp-2");
    fireEvent.click(screen.getByRole("button", { expanded: true }));
    expect(screen.getByRole("button", { expanded: false })).toBeTruthy();
  });

  it("says which errands are builders, in words and not only in colour", () => {
    render(
      <Errands
        lines={[
          {
            kind: "errand",
            text: "x",
            at: "2026-09-15T20:18:00Z",
            conversation: "c1",
            errand: { id: "w1", state: "running", objective: "rewrite store.py", role: "builder" },
          },
          {
            kind: "errand",
            text: "y",
            at: "2026-09-15T20:18:00Z",
            conversation: "c1",
            errand: { id: "w2", state: "running", objective: "where does it live", role: "scout" },
          },
        ]}
        conversationId="c1"
      />,
    );
    // One label, not two: a scout is the unmarked case, so only the one editing your files
    // is called out.
    expect(screen.getAllByText("builder")).toHaveLength(1);
  });

  it("treats a line with no role as a scout", () => {
    // Lines published before roles existed. Wrong in the safe direction: nothing gets labelled
    // as editing your repository unless it said so.
    const folded = foldErrands(
      [
        {
          kind: "errand",
          text: "x",
          at: "2026-09-15T20:18:00Z",
          conversation: "c1",
          errand: { id: "w1", state: "running", objective: "look" },
        },
      ],
      "c1",
    );
    expect(folded[0].role).toBe("scout");
  });
});
