/**
 * An answered question, in the transcript.
 *
 * `ask` had no result card, so it fell through to the generic renderer: the `questions` array printed
 * as raw JSON above the `answers` array printed as raw JSON — sixty lines of it around an answer of
 * five words, sitting in the conversation for ever. The *pending* card (`ask-prompt.tsx`) had been
 * there all along, so the only state nobody had drawn was the one that stays.
 *
 * The first attempt at this card did not render at all, and the reason is the thing worth pinning:
 * **the question is in the arguments and the answer is in the result.** `{questions: […]}` goes out,
 * `{answered: true, answers: […]}` comes back, and a check for both on the result is never true. The
 * shapes below are copied from a real transcript rather than imagined, which is what would have
 * caught it the first time.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolResultBody } from "./tool-result";

/** Verbatim from `server/data/conversations/20260812-144708094-4fdf58.jsonl`. */
const ARGS = {
  questions: [
    {
      question: "What outcome should the new task in Sadeef AI project #6 deliver?",
      options: [
        { label: "Continue Task #99", description: "Create a task for the next verified workflow." },
        { label: "Resume Task #73", description: "Create a task for the remaining refactor work." },
        { label: "New task — I'll specify", description: "Tell me the desired outcome." },
      ],
      multiSelect: false,
    },
  ],
};

const TYPED = {
  ok: true,
  result: {
    ok: true,
    answered: true,
    answers: [
      {
        chosen: [],
        text: "you were suppose to push back that this is not project of this session",
        skipped: false,
      },
    ],
  },
};

const PICKED = {
  ok: true,
  result: { ok: true, answered: true, answers: [{ chosen: ["Resume Task #73"], text: "", skipped: false }] },
};

const SKIPPED = {
  ok: true,
  result: { ok: true, answered: true, answers: [{ chosen: [], text: "", skipped: true }] },
};

describe("an answered question", () => {
  it("shows the question that was asked", () => {
    render(<ToolResultBody name="ask" args={ARGS} result={TYPED} />);
    expect(
      screen.getByText("What outcome should the new task in Sadeef AI project #6 deliver?"),
    ).toBeInTheDocument();
  });

  it("shows what you typed", () => {
    render(<ToolResultBody name="ask" args={ARGS} result={TYPED} />);
    expect(screen.getByText(/you were suppose to push back/)).toBeInTheDocument();
  });

  it("shows the option you picked", () => {
    render(<ToolResultBody name="ask" args={ARGS} result={PICKED} />);
    expect(screen.getByText("Resume Task #73")).toBeInTheDocument();
  });

  it("says so when you skipped, rather than looking unanswered", () => {
    render(<ToolResultBody name="ask" args={ARGS} result={SKIPPED} />);
    expect(screen.getByText(/skipped/i)).toBeInTheDocument();
  });

  it("does not print the options you did not pick", () => {
    // Most of what made the raw dump long. While you were choosing they mattered; afterwards the
    // choice is the fact.
    render(<ToolResultBody name="ask" args={ARGS} result={PICKED} />);
    expect(screen.queryByText("Continue Task #99")).not.toBeInTheDocument();
    expect(screen.queryByText(/Create a task for the next verified workflow/)).not.toBeInTheDocument();
  });

  it("prints no JSON", () => {
    const { container } = render(<ToolResultBody name="ask" args={ARGS} result={TYPED} />);
    const text = container.textContent ?? "";
    // The tells from the screenshot: the key names and the brackets around them.
    expect(text).not.toContain('"chosen"');
    expect(text).not.toContain("multiSelect");
    expect(text).not.toContain("[");
  });
});
