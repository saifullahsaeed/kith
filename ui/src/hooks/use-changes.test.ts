/**
 * One connection for `/api/changes`, however many widgets are watching.
 *
 * This is a connection-count test rather than a behaviour test, because the bug it pins down was
 * invisible from the behaviour: every widget updated correctly, and the app froze anyway.
 *
 * `useChanges` opened one `EventSource` per subscriber, on the reasoning — written into the hook —
 * that "SSE over HTTP/2 multiplexes on a single connection". Kith is served by Werkzeug, which
 * speaks HTTP/1.1 only and has no TLS for Chromium to negotiate h2 over, so each subscriber took a
 * whole TCP connection out of a per-origin pool that Chromium caps at six. Seven call sites, plus
 * the activity feed, plus the turn's own `POST /api/chat`, against six slots: measured on
 * 2026-08-14, the renderer held exactly six and had issued no request of any kind for four minutes
 * while a turn sat parked on an `ask`. The question card cannot be fetched, health cannot be
 * polled, and Cmd-R cannot even fetch the document — the freeze is the pool, not the renderer.
 *
 * `ask` is where it shows because an ordinary turn's stream closes in seconds, so the shortage is
 * transient; a parked turn holds its socket for the full fifteen-minute deadline. Stop appears to
 * fix it because aborting the fetch is client-side and frees a slot, which lets the queued poll
 * finally go out.
 *
 * So the count is the contract, and it is asserted here rather than left to a comment.
 */
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useChanges } from "./use-changes";

interface FakeSource {
  url: string;
  closed: boolean;
  onmessage: ((event: { data: string }) => void) | null;
}

/** The stub from `test/setup.ts`, which records every source built. */
function sources(): FakeSource[] {
  return (globalThis as unknown as { EventSource: { open: FakeSource[] } }).EventSource.open;
}

function live(): FakeSource[] {
  return sources().filter((source) => !source.closed);
}

function emit(kind: string, conversation = "") {
  const data = JSON.stringify({ kind, conversation, at: new Date(0).toISOString() });
  for (const source of live()) source.onmessage?.({ data });
}

describe("useChanges", () => {
  it("opens one connection for many subscribers", () => {
    renderHook(() => {
      useChanges("task", () => {});
      useChanges("turn", () => {});
      useChanges(["project", "message"], () => {});
      useChanges("process", () => {}, "c-1");
    });

    // Four subscribers, one socket. The pool has six; the app has seven call sites.
    expect(live()).toHaveLength(1);
    expect(live()[0].url).toBe("/api/changes");
  });

  it("delivers to every subscriber over the shared connection", () => {
    const onTask = vi.fn();
    const onTurn = vi.fn();

    renderHook(() => {
      useChanges("task", onTask);
      useChanges("turn", onTurn);
    });

    emit("task");

    // Sharing a socket must not mean sharing a filter: each subscriber still hears only its kinds.
    expect(onTask).toHaveBeenCalledTimes(1);
    expect(onTurn).not.toHaveBeenCalled();
  });

  it("keeps the connection while any subscriber remains, and closes it with the last", () => {
    const staying = vi.fn();
    const leaving = renderHook(() => useChanges("task", () => {}));
    const remaining = renderHook(() => useChanges("task", staying));

    leaving.unmount();

    // One widget unmounting must not take the channel down for the others.
    expect(live()).toHaveLength(1);
    emit("task");
    expect(staying).toHaveBeenCalledTimes(1);

    remaining.unmount();
    expect(live()).toHaveLength(0);
  });
});
