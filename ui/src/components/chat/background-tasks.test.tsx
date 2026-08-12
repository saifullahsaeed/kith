/**
 * Background tasks in the panel.
 *
 * A half-hour test suite used to be invisible from here: he started it, the turn ended, and the only
 * way to learn anything was for him to check — 371 of those calls over two days, each a full round.
 * Finishing wakes the conversation now; this is so you can see it running without asking either.
 *
 * The payload shape is copied from `processes.check()` with no name, which is what `/api/processes`
 * returns.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BackgroundTasks } from "./background-tasks";

function answering(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response)),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("background tasks", () => {
  it("shows a running task, its command and how long it has been going", async () => {
    answering({
      running: [{ name: "run-tests", command: "pytest tests/", alive: true, for: "12m" }],
    });

    render(<BackgroundTasks conversationId="c-1" />);

    expect(await screen.findByText("run-tests")).toBeInTheDocument();
    // The command, because a name chosen an hour ago does not always say what is running.
    expect(screen.getByText("pytest tests/")).toBeInTheDocument();
    expect(screen.getByText("12m")).toBeInTheDocument();
  });

  it("says done rather than an elapsed time once it has exited", async () => {
    answering({
      running: [{ name: "run-tests", command: "pytest tests/", alive: false, for: "31m" }],
    });

    render(<BackgroundTasks conversationId="c-1" />);

    expect(await screen.findByText("done")).toBeInTheDocument();
    expect(screen.queryByText("31m")).not.toBeInTheDocument();
  });

  it("renders nothing at all when nothing is running", async () => {
    // A header over an empty list is furniture, and this panel had a column of it before.
    answering({ running: [] });

    const { container } = render(<BackgroundTasks conversationId="c-1" />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("renders nothing when the endpoint is unavailable", async () => {
    // An older server, or one restarting. The panel must not blank out or throw over it.
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("nope"))));

    const { container } = render(<BackgroundTasks conversationId="c-1" />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
