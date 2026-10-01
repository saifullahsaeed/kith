/**
 * A message posted for a tab is handed over once, and only once.
 *
 * The outbox is what turns "Request changes" and "Ask about this" into a sent message, so a
 * second delivery is a second turn — he answers the same question twice and it costs twice. The
 * ways a second delivery could happen are all ordinary: the pane remounting, an HMR update
 * re-running its effects, React's development double-mount, the subscription and the mount both
 * seeing the same entry. Each is a case here, counted rather than checked for presence.
 */
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";

import { post, STALE_MS, take, usePosted, waitingFor, type Posted } from "./outbox";

function Receiver({ uid, got }: { uid: string; got: (message: Posted) => void }) {
  usePosted(uid, got);
  return null;
}

/** Let the hook's deferred take run. */
async function settle() {
  await act(async () => {
    await new Promise((done) => setTimeout(done, 5));
  });
}

afterEach(() => {
  vi.useRealTimers();
});

describe("take", () => {
  it("returns a posted message once and empties the slot", () => {
    post("t-take", { text: "hello", projectId: 3 });
    expect(take("t-take")).toMatchObject({ text: "hello", projectId: 3 });
    expect(take("t-take")).toBeUndefined();
    expect(waitingFor("t-take")).toBe(false);
  });

  it("drops a message nobody took in time instead of sending it late", () => {
    post("t-stale", { text: "hello", projectId: null }, 1_000);
    expect(take("t-stale", 1_000 + STALE_MS + 1)).toBeUndefined();
    expect(waitingFor("t-stale")).toBe(false);
  });

  it("posts nothing for an empty message or no tab", () => {
    post("t-empty", { text: "   ", projectId: null });
    post("", { text: "hello", projectId: null });
    expect(waitingFor("t-empty")).toBe(false);
    expect(waitingFor("")).toBe(false);
  });
});

describe("usePosted", () => {
  it("delivers what was posted before the pane mounted, once", async () => {
    const got = vi.fn();
    post("t-mount", { text: "before", projectId: 7 });
    render(<Receiver uid="t-mount" got={got} />);
    await settle();
    expect(got).toHaveBeenCalledTimes(1);
    expect(got.mock.calls[0][0]).toMatchObject({ text: "before", projectId: 7 });
  });

  it("delivers what is posted while the pane is mounted, once", async () => {
    const got = vi.fn();
    render(<Receiver uid="t-live" got={got} />);
    await settle();
    expect(got).not.toHaveBeenCalled();
    act(() => post("t-live", { text: "while", projectId: null }));
    await settle();
    expect(got).toHaveBeenCalledTimes(1);
  });

  it("does not deliver again when the pane remounts", async () => {
    const got = vi.fn();
    post("t-remount", { text: "once", projectId: null });
    const first = render(<Receiver uid="t-remount" got={got} />);
    await settle();
    first.unmount();
    render(<Receiver uid="t-remount" got={got} />);
    await settle();
    expect(got).toHaveBeenCalledTimes(1);
  });

  it("does not deliver again when its effects re-run, as an HMR update does", async () => {
    const got = vi.fn();
    post("t-hmr", { text: "once", projectId: null });
    const view = render(<Receiver uid="t-hmr" got={got} />);
    await settle();
    // A fresh callback identity on every render is what a hot-replaced module hands the hook.
    view.rerender(<Receiver uid="t-hmr" got={(message) => got(message)} />);
    await settle();
    expect(got).toHaveBeenCalledTimes(1);
  });

  it("delivers once under React's development double-mount", async () => {
    const got = vi.fn();
    post("t-strict", { text: "once", projectId: null });
    render(
      <StrictMode>
        <Receiver uid="t-strict" got={got} />
      </StrictMode>,
    );
    await settle();
    expect(got).toHaveBeenCalledTimes(1);
  });

  it("is delivered to one pane when two are listening on the same tab", async () => {
    const got = vi.fn();
    render(
      <>
        <Receiver uid="t-two" got={got} />
        <Receiver uid="t-two" got={got} />
      </>,
    );
    await settle();
    act(() => post("t-two", { text: "once", projectId: null }));
    await settle();
    expect(got).toHaveBeenCalledTimes(1);
  });

  it("leaves another tab's message alone, and keeps it if the pane unmounts first", async () => {
    const got = vi.fn();
    const view = render(<Receiver uid="t-mine" got={got} />);
    act(() => post("t-other", { text: "not yours", projectId: null }));
    await settle();
    expect(got).not.toHaveBeenCalled();
    expect(waitingFor("t-other")).toBe(true);
    // Unmounted inside the tick between seeing an entry and taking it: nothing is taken.
    act(() => post("t-mine", { text: "late", projectId: null }));
    view.unmount();
    await settle();
    expect(got).not.toHaveBeenCalled();
    expect(waitingFor("t-mine")).toBe(true);
    take("t-other");
    take("t-mine");
  });
});
