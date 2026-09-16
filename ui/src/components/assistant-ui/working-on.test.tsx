/**
 * What he is working on, in a panel that has to hold four other things under it.
 *
 * This rendered every checklist item, always, at generous spacing — and the column it sits in is
 * `h-full` with no scroll anywhere, so whatever did not fit was not merely below the fold, it was
 * unreachable. One nine-step task was enough to hide the errands, the context and the background
 * work completely.
 *
 * Nine steps used to be unusual. `plan_work` writes a milestone's tasks with their whole
 * checklists in one call, so it is now ordinary — the feature landing is what turned a latent
 * layout bug into the thing you look at every day.
 *
 * What survives the fold is the part worth glancing at: how far through, and what is next.
 */
import { render, screen, waitFor } from "@/test/render";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent } from "@testing-library/react";

import { WorkingOn } from "./working-on";

function working(steps: { text: string; done: boolean }[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            id: 155,
            goal: "The 152 public definitions in connections are documented",
            checklist: steps.map((one, i) => ({ id: i + 1, text: one.text, done: one.done })),
          }),
      } as Response),
    ),
  );
}

const nine = [
  { text: "audit the package", done: true },
  { text: "read the style reference", done: true },
  { text: "write the convention down", done: true },
  { text: "send a builder at exporter", done: true },
  { text: "apply the patch", done: false },
  { text: "run the tests", done: false },
  { text: "send a builder at importer", done: false },
  { text: "apply that patch", done: false },
  { text: "re-audit and record what is left", done: false },
];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a long checklist folds", () => {
  it("does not render every step by default", async () => {
    working(nine);
    render(<WorkingOn conversationId="c-1" />);

    await screen.findByText(/152 public definitions/);
    expect(screen.queryByText("re-audit and record what is left")).not.toBeInTheDocument();
    expect(screen.queryByText("audit the package")).not.toBeInTheDocument();
  });

  it("still shows how far through it is", async () => {
    // The count and the bar are the part you actually glance at, so folding must not cost them.
    working(nine);
    render(<WorkingOn conversationId="c-1" />);

    expect(await screen.findByText("4/9")).toBeInTheDocument();
  });

  it("shows what is next, which is the only other line worth a glance", async () => {
    working(nine);
    render(<WorkingOn conversationId="c-1" />);

    expect(await screen.findByText(/next · apply the patch/)).toBeInTheDocument();
  });

  it("opens on click and closes again", async () => {
    working(nine);
    render(<WorkingOn conversationId="c-1" />);

    const toggle = await screen.findByRole("button", { name: "Show the checklist" });
    fireEvent.click(toggle);
    expect(await screen.findByText("re-audit and record what is left")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Hide the checklist" }));
    await waitFor(() =>
      expect(screen.queryByText("re-audit and record what is left")).not.toBeInTheDocument(),
    );
  });
});

describe("a short checklist does not", () => {
  it("shows all of it, because folding three costs more than it saves", async () => {
    working([
      { text: "read the file", done: true },
      { text: "make the edit", done: false },
      { text: "run the tests", done: false },
    ]);
    render(<WorkingOn conversationId="c-1" />);

    expect(await screen.findByText("make the edit")).toBeInTheDocument();
    expect(screen.getByText("read the file")).toBeInTheDocument();
  });

  it("offers no toggle to press", async () => {
    working([{ text: "the only step", done: false }]);
    render(<WorkingOn conversationId="c-1" />);

    await screen.findByText("the only step");
    expect(screen.getByRole("button")).toBeDisabled();
  });
});

describe("a task with no checklist", () => {
  it("shows the task and nothing else", async () => {
    working([]);
    render(<WorkingOn conversationId="c-1" />);

    await screen.findByText(/152 public definitions/);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText(/next ·/)).not.toBeInTheDocument();
  });
});

describe("the way in is obvious", () => {
  it("opens from the next line as well as the count", async () => {
    // The next line is what you are reading when you decide you want the rest, so it is the
    // more natural target of the two — and it was not a target at all.
    working(nine);
    render(<WorkingOn conversationId="c-1" />);

    fireEvent.click(await screen.findByText(/next · apply the patch/));
    expect(await screen.findByText("re-audit and record what is left")).toBeInTheDocument();
  });

  it("offers two ways in when folded and none when there is nothing to open", async () => {
    working([{ text: "the only step", done: false }]);
    render(<WorkingOn conversationId="c-1" />);

    await screen.findByText("the only step");
    // The count is still there, still disabled, and the next line is not a control because
    // there is nothing folded behind it.
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
