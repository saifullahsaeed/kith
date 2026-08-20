/**
 * What the canvas does with a fence that is still arriving.
 *
 * `canvas.test.ts` covers the decisions; this covers the timing, which is where a streaming
 * surface actually goes wrong. Every case here is a thing that would look like a rendering bug
 * and be a scheduling one: a frame mounted around half a document, a frame rebuilt on every
 * token so the animation never gets past its first frame, a placeholder that never resolves
 * because he stopped writing mid-tag.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HtmlCanvas } from "@/components/assistant-ui/html-canvas";
import { currentCanvasState, useCanvasState } from "@/lib/canvas-state";

const CANVAS = '<style>b{color:red}</style><div id="scene"></div><script>run()</script>';

const fallback = <pre data-testid="source">source</pre>;

function frame() {
  return document.querySelector("iframe");
}

/** What the server hands back for a document it is now serving. The component frames a URL
 *  rather than a `srcdoc` because a local-scheme frame inherits the app's CSP — see
 *  `routes/canvas.py`. So the test has to have a server. */
const HOSTED = "/api/canvas/test-id";
let posted: string[] = [];

/** Past the settle window and the give-up window both, and past the POST that now sits between
 *  "it parsed" and "it is on screen" — advancing timers alone leaves that promise unresolved. */
async function settle(ms = 300) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  posted = [];
  useCanvasState.getState().clear();
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
    posted.push(String(init?.body ?? ""));
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ id: "test-id", url: HOSTED }),
    } as Response);
  });
});

describe("an html fence, mid-stream", () => {

  it("shows the source for markup he is only showing you", async () => {
    render(<HtmlCanvas code='<div class="card"><span>hi</span></div>' fallback={fallback} />);
    await settle();
    expect(screen.getByTestId("source")).toBeInTheDocument();
    expect(frame()).toBeNull();
  });

  it("never flashes the source before drawing", async () => {
    // The reason `looksRenderable` runs synchronously rather than on the settle timer.
    render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    expect(screen.queryByTestId("source")).not.toBeInTheDocument();
    expect(screen.getByText(/building/)).toBeInTheDocument();
  });

  it("waits for the blocks to close before mounting anything", async () => {
    render(<HtmlCanvas code="<style>b{colo" fallback={fallback} />);
    await settle(250);
    expect(frame()).toBeNull();
    expect(screen.getByText(/building/)).toBeInTheDocument();
  });

  it("mounts the frame once he has stopped writing", async () => {
    render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    const box = frame();
    expect(box).not.toBeNull();
    expect(box?.getAttribute("src")).toBe(HOSTED);
    expect(posted.join("")).toContain("run()");
  });

  it("seals the frame it mounts", async () => {
    // The boundary, asserted where it is actually applied rather than only where it is defined.
    render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    expect(frame()?.getAttribute("sandbox")).toBe("allow-scripts");
    // The seal reaches the frame through the served document's own headers now, but the copy
    // baked into the markup goes with it — so what is POSTed still carries the policy.
    expect(posted.join("")).toContain("default-src ");
  });

  it("does not rebuild the frame while the tail of the message arrives", async () => {
    // A remount restarts the animation. The fence is finished here; the paragraph after it is
    // not, and re-rendering for that must not touch the canvas.
    const { rerender } = render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    const sent = posted.length;
    rerender(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    expect(frame()?.getAttribute("src")).toBe(HOSTED);
    expect(posted.length).toBe(sent);
  });

  it("gives the source back for a document he never finished", async () => {
    // A truncated reply, or a `<script` he opened and abandoned. Without the give-up timer this
    // is a placeholder that stays on screen forever.
    render(<HtmlCanvas code="<html><body><script>const x =" fallback={fallback} />);
    await settle(3000);
    expect(screen.getByTestId("source")).toBeInTheDocument();
    expect(frame()).toBeNull();
  });

  it("does not build while the reply is still being written", async () => {
    // The blinking bug: a long page is momentarily valid many times on its way in — the instant
    // `</style>` closes, every block `isComplete` counts is balanced — so a pause between tokens
    // used to look like a finished document, mount a frame, and have it torn down by the next
    // token. Nothing is built until the thread says the message is done.
    const { rerender } = render(<HtmlCanvas code={CANVAS} fallback={fallback} streaming />);
    await settle(3000);
    expect(frame()).toBeNull();
    expect(posted).toHaveLength(0);
    expect(screen.getByText(/writing a page/)).toBeInTheDocument();

    rerender(<HtmlCanvas code={CANVAS} fallback={fallback} streaming={false} />);
    await settle();
    expect(frame()).not.toBeNull();
    expect(posted).toHaveLength(1);
  });

  it("keeps the drawing once it has one, even if the tail never closes", async () => {
    const { rerender } = render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    rerender(<HtmlCanvas code={`${CANVAS}<script>more(`} fallback={fallback} />);
    await settle(3000);
    expect(frame()).not.toBeNull();
  });
});

/**
 * The wire back, at the component boundary.
 *
 * `canvas-bridge.test.ts` covers what a message is allowed to say. This covers what happens to
 * it once it is believed — which is where all three of these went wrong, each of them invisible:
 * a reading that arrived and was applied to nothing, a frame that was never spoken to at all,
 * a height accepted from the wire that the box it lives in could not hold.
 */
describe("what a canvas says back", () => {
  /** A message from a specific frame. `source` is the whole of the identity check, so a test
   *  that skips it is testing nothing — the listener drops it before reading the payload. */
  const say = (from: HTMLIFrameElement, data: unknown) =>
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data, source: from.contentWindow }));
    });

  const frames = () => Array.from(document.querySelectorAll("iframe"));

  it("holds a page that measures itself taller than the box will go", async () => {
    render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    const box = frame()!;
    say(box, { kith: 1, type: "height", px: 2000 });
    // The wire allows the claim — the full-screen view honours it — but this rectangle does not.
    // Applied unclamped, the first pixel of drag snapped it back into range and the canvas lost
    // eight hundred pixels in one frame.
    expect(box.style.height).toBe("1200px");
  });

  it("hears the full-screen canvas, not only the small one", async () => {
    render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Open the canvas full screen"));
    });
    const [, big] = frames();
    expect(big).toBeDefined();

    say(big, { kith: 1, type: "state", title: "tuner", values: { step: 4 } });
    // The lightbox used to mount a bare frame with no listener, so the bigger and more usable
    // surface was the one whose readings were thrown away.
    expect(currentCanvasState()).toEqual([{ title: "tuner", values: { step: 4 } }]);
  });

  it("does not resize the small canvas to fit the big one's window", async () => {
    render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    const box = frame()!;
    const was = box.style.height;
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Open the canvas full screen"));
    });
    say(frames()[1], { kith: 1, type: "height", px: 900 });
    expect(box.style.height).toBe(was);
  });

  it("pushes the theme into a replayed frame", async () => {
    render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    const first = frame()!;
    const toFirst = vi.spyOn(first.contentWindow!, "postMessage");
    await act(async () => {
      fireEvent.load(first);
    });
    expect(toFirst).toHaveBeenCalledTimes(1);

    await act(async () => {
      fireEvent.click(screen.getByLabelText("Play it again from the start"));
    });
    const second = frame()!;
    expect(second).not.toBe(first);
    const toSecond = vi.spyOn(second.contentWindow!, "postMessage");
    // Nothing is said to a frame that has not loaded yet, and — the actual bug — the frame is
    // spoken to once it has. The flag saying "this frame is up" used to survive the remount, so
    // a replayed canvas was pushed a palette while it was still blank and never pushed again.
    expect(toSecond).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.load(second);
    });
    expect(toSecond).toHaveBeenCalledTimes(1);
  });
});
