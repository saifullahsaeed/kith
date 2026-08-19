/**
 * What the canvas does with a fence that is still arriving.
 *
 * `canvas.test.ts` covers the decisions; this covers the timing, which is where a streaming
 * surface actually goes wrong. Every case here is a thing that would look like a rendering bug
 * and be a scheduling one: a frame mounted around half a document, a frame rebuilt on every
 * token so the animation never gets past its first frame, a placeholder that never resolves
 * because he stopped writing mid-tag.
 */
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HtmlCanvas } from "@/components/assistant-ui/html-canvas";

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

describe("an html fence, mid-stream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    posted = [];
    vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
      posted.push(String(init?.body ?? ""));
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ id: "test-id", url: HOSTED }),
      } as Response);
    });
  });

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

  it("keeps the drawing once it has one, even if the tail never closes", async () => {
    const { rerender } = render(<HtmlCanvas code={CANVAS} fallback={fallback} />);
    await settle();
    rerender(<HtmlCanvas code={`${CANVAS}<script>more(`} fallback={fallback} />);
    await settle(3000);
    expect(frame()).not.toBeNull();
  });
});
