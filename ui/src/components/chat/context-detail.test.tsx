/**
 * The Context panel's chrome — specifically, that it has none of its own.
 *
 * It opened with a header: the Layers mark, "Context", a line of explanation, and a close button.
 * All of it sat directly beneath the bar its own pane draws, which already carries the surface's
 * name and its close — so the panel began with a second title and a third way to shut itself, in
 * the one column on screen whose job is to fit a great many numbers.
 *
 * `Fold now` was the only thing in that header that lived nowhere else, so it moved rather than
 * going: into the legend row, beside the sentence that says when the window will fold on its own.
 * That sentence is the one this button answers, and the row was already being drawn — so the whole
 * header came off at no cost to what you can do from here.
 */
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { render, screen } from "@/test/render";
import { ConfirmProvider } from "@/components/ui/confirm";
import { ContextDetailScreen } from "./context-detail";

/** Mounted the way the shell mounts it. Nothing stubs the fetch: `fetchContextDetail` answers with
 *  an empty reading rather than throwing, so the panel renders its own resting state either way —
 *  which is all these assertions are about. */
const panel = () =>
  render(
    <MemoryRouter>
      <ConfirmProvider>
        <ContextDetailScreen conversationId="c-1" onClose={vi.fn()} />
      </ConfirmProvider>
    </MemoryRouter>,
  );

describe("the context panel", () => {
  it("draws no title of its own — the pane it sits in already has one", () => {
    panel();
    expect(screen.queryByText("every message his next turn sends, and what each one costs")).toBeNull();
  });

  it("offers no close button of its own — the pane it sits in already has one", () => {
    panel();
    expect(screen.queryByLabelText("Close")).toBeNull();
  });
});
