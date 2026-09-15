/**
 * A ⌘⏎ message survives the pane it was typed in going away.
 *
 * It was the one piece of unsent text nothing could see. ⌘⏎ empties the composer *before* handing
 * the words to `holdUntilIdle`, so while they wait they are not in the box, not in the drafts
 * store, and not in the transcript — they exist only in this module's `held`, and the thing they
 * are waiting for is a `deliver` closure over that pane's own `send`. Click another tab and the
 * pane is unmounted: the poll ran its twenty minutes and then delivered into a runtime nobody was
 * reading. Not sent, not shown, not held.
 *
 * `rescueHeld` is the way back out, and it is keyed on the conversation for the obvious reason —
 * with two chat panes open, the one being unmounted must not take the other's queued message with
 * it.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { dropHeld, heldMessage, isHolding, rescueHeld } from "./queued-send";

beforeEach(() => {
  dropHeld();
});

/** Put something in the hold without running the twenty-minute poll behind it.
 *
 *  `holdUntilIdle` starts by writing `held` and only then awaits, so calling it and never
 *  resolving the poll leaves exactly the state a queued message sits in. The fetch it polls with
 *  is stubbed to hang, which is what "a turn is still running" looks like from here. */
function queue(conversationId: string, text: string, deliver = vi.fn()) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {})),
  );
  // Deliberately not awaited: the point is the state *while* it waits.
  void import("./queued-send").then((module) =>
    module.holdUntilIdle(conversationId, text, deliver),
  );
  return deliver;
}

describe("a message waiting on the turn to finish", () => {
  it("comes back to the conversation it was typed in", async () => {
    queue("c-1", "when you're done, do the UI side");
    await vi.waitFor(() => expect(isHolding()).toBe(true));
    expect(rescueHeld("c-1")).toBe("when you're done, do the UI side");
  });

  it("is not handed to a different conversation", async () => {
    queue("c-1", "meant for the first chat");
    await vi.waitFor(() => expect(isHolding()).toBe(true));
    // The case two panes make reachable: unmounting one must not empty the other's queue.
    expect(rescueHeld("c-2")).toBe("");
    expect(heldMessage()).toBe("meant for the first chat");
  });

  it("stops waiting once it has been taken back", async () => {
    queue("c-1", "taken back");
    await vi.waitFor(() => expect(isHolding()).toBe(true));
    rescueHeld("c-1");
    // Or the composer goes on showing "Queued" for a message that is now an ordinary draft.
    expect(isHolding()).toBe(false);
    expect(heldMessage()).toBe("");
  });

  it("is nothing to rescue when nothing is waiting", () => {
    expect(rescueHeld("c-1")).toBe("");
  });

  it("cannot be taken twice", async () => {
    queue("c-1", "only once");
    await vi.waitFor(() => expect(isHolding()).toBe(true));
    expect(rescueHeld("c-1")).toBe("only once");
    expect(rescueHeld("c-1")).toBe("");
  });
});
