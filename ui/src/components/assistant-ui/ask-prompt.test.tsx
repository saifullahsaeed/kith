/**
 * The question card, tested on the two things that reached you broken.
 *
 * Both were reported from the screen rather than caught here, because there was no here:
 * "multiple choice do not work" (the flag was read under one name and was silently always
 * false) and "writing my answer take same answe in next question" (an uncontrolled input that
 * React reused across a change of question). Neither is a type error. Both are two lines of
 * test.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AskPrompt } from "./ask-prompt";

/** The submit button, by exact name — `/Next|Done/` also matches the pager's "Next question"
 *  chevron, which is a collision worth knowing about in the markup as well as here. */
function submit() {
  return screen.getByRole("button", { name: "Next" });
}

const TWO = {
  id: "q1",
  conversationId: "c1",
  questions: [
    {
      question: "Which of these?",
      options: [
        { label: "Alpha", description: "" },
        { label: "Beta", description: "" },
      ],
      multiple: true,
    },
    { question: "And then?", options: [{ label: "Ship", description: "" }], multiple: false },
  ],
};

/** The server, as far as this component knows: one open question and a place to answer it. */
function serve(question: unknown) {
  const answered: { id: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
      if (init?.method === "POST") {
        answered.push({ id: String(url), body: JSON.parse(init.body ?? "{}") });
        return { ok: true, json: async () => ({ answered: true }) };
      }
      return { ok: true, json: async () => question };
    }),
  );
  return answered;
}

describe("a question with several answers", () => {
  beforeEach(() => serve(TWO));

  it("lets more than one option be picked", async () => {
    render(<AskPrompt conversationId="c1" />);
    const alpha = await screen.findByRole("button", { name: /Alpha/ });
    await userEvent.click(alpha);
    // The bug: the first click was the answer, so Beta was never reachable.
    await userEvent.click(screen.getByRole("button", { name: /Beta/ }));
    expect(submit()).toBeInTheDocument();
  });

  it("says that several are allowed before anything is clicked", async () => {
    render(<AskPrompt conversationId="c1" />);
    // A card that merely permits several looked identical to one that takes the first click.
    expect(await screen.findByText(/pick any that apply/i)).toBeInTheDocument();
  });
});

describe("answering in your own words", () => {
  beforeEach(() => serve(TWO));

  it("does not carry the text into the next question", async () => {
    render(<AskPrompt conversationId="c1" />);
    const box = await screen.findByLabelText(/own words/i);
    await userEvent.type(box, "only for the first");
    await userEvent.click(submit());

    await waitFor(() => expect(screen.getByText("And then?")).toBeInTheDocument());
    // The bug: React reused the same input element, so this still held the first answer.
    expect(screen.getByLabelText(/own words/i)).toHaveValue("");
  });

  it("keeps it when you page back to that question", async () => {
    render(<AskPrompt conversationId="c1" />);
    const box = await screen.findByLabelText(/own words/i);
    await userEvent.type(box, "remember this");
    await userEvent.click(submit());
    await waitFor(() => expect(screen.getByText("And then?")).toBeInTheDocument());

    await userEvent.click(screen.getByLabelText(/previous question/i));
    expect(screen.getByLabelText(/own words/i)).toHaveValue("remember this");
  });
});
