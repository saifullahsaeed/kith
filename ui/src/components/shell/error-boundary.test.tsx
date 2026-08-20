/**
 * A fence that throws costs you the drawing, not the conversation.
 *
 * React's default for an uncaught render error is to unmount the nearest boundary, and until
 * every fence had one of its own the nearest was the thread — so one bad diagram took the
 * conversation you were reading with it. Below the fence line is someone else's parser and
 * layout engine, running on text a model wrote; it will fail in a way nobody predicted.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "@/components/shell/error-boundary";

function Throws(): never {
  throw new Error("mermaid ate itself");
}

describe("a boundary with a quiet answer", () => {
  it("shows the fallback instead of an alert", () => {
    // The right answer for a drawing that failed is the source — what the fence showed before
    // anything drew it — not a centred alert with a button in the middle of a paragraph.
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary where="A diagram" fallback={<pre data-testid="source">graph TD</pre>}>
        <Throws />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("source")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("leaves everything around it standing", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <div>
        <p>the paragraph above</p>
        <ErrorBoundary where="A diagram" fallback={<pre>source</pre>}>
          <Throws />
        </ErrorBoundary>
        <p>the paragraph below</p>
      </div>,
    );
    expect(screen.getByText("the paragraph above")).toBeInTheDocument();
    expect(screen.getByText("the paragraph below")).toBeInTheDocument();
  });

  it("still reports it to the console, so a quiet failure is not a hidden one", () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary where="A diagram" fallback={<pre>source</pre>}>
        <Throws />
      </ErrorBoundary>,
    );
    expect(logged.mock.calls.flat().join(" ")).toContain("A diagram crashed");
  });

  it("still says something out loud when there is no quieter answer", () => {
    // A panel has no source to fall back to, so it keeps the message and the way back.
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary where="Conversations" compact>
        <Throws />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/Conversations stopped working/)).toBeInTheDocument();
  });
});
