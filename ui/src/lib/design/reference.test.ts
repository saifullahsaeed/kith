/**
 * Pointing at a design from the chat.
 *
 * The behaviour worth pinning is what happens to text you had already typed: the usual move is
 * to write the question and then point at the screen, so an assignment rather than an append
 * would eat the question.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearActiveComposer, setActiveComposer } from "@/lib/active-composer";
import { referenceFor, referenceInChat } from "@/lib/design/reference";

function fakeComposer(text = "") {
  const state = { text };
  return {
    getState: () => state,
    setText: vi.fn((next: string) => {
      state.text = next;
    }),
    focus: vi.fn(),
    _state: state,
  };
}

describe("how a design reads in a message", () => {
  it("is a path he can open, not the markup", () => {
    // Pasting the HTML would put a stale copy in the transcript and be wrong the moment the
    // design is touched again.
    expect(referenceFor(".kith/screens/login.html", "Sign in")).toBe("Sign in — `.kith/screens/login.html`");
  });

  it("is just the path when there is no title", () => {
    expect(referenceFor(".kith/screens/login.html")).toBe("`.kith/screens/login.html`");
  });
});

describe("putting it in the composer", () => {
  let composer: ReturnType<typeof fakeComposer>;

  beforeEach(() => {
    composer = fakeComposer();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    setActiveComposer(composer as any);
  });

  it("says so when there is no chat to put it in", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    clearActiveComposer(composer as any);
    expect(referenceInChat(".kith/screens/a.html")).toBe(false);
  });

  it("keeps what you had already typed", () => {
    composer._state.text = "make the rows tighter";
    referenceInChat(".kith/screens/a.html", "Settings");
    expect(composer.setText).toHaveBeenCalledWith("make the rows tighter\n\nSettings — `.kith/screens/a.html`");
  });

  it("does not add the same design twice", () => {
    composer._state.text = "Settings — `.kith/screens/a.html`";
    referenceInChat(".kith/screens/a.html", "Settings");
    expect(composer.setText).not.toHaveBeenCalled();
  });
});
