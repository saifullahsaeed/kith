import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

/**
 * jsdom has no `EventSource`, and every component that subscribes to `/api/changes` builds one on
 * mount — so without this a test of the *rendering* fails on the live-update plumbing, which is not
 * what it is testing. A stub rather than a mock library: the components only ever set `onmessage` and
 * call `close()`, and anything more elaborate here would be a second implementation to keep in step.
 *
 * `emit` lets a test push a change through if it wants to, without knowing how the hook is wired.
 */
class FakeEventSource {
  static open: FakeEventSource[] = [];
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.open.push(this);
  }
  close() {
    this.closed = true;
  }
  static emit(kind: string, conversation = "") {
    const data = JSON.stringify({ kind, conversation, at: new Date(0).toISOString() });
    for (const source of FakeEventSource.open) {
      if (!source.closed) source.onmessage?.({ data });
    }
  }
}
// Assigned rather than `vi.stubGlobal`: the `afterEach` below calls `restoreAllMocks`, which undoes a
// stubbed global — so the first test in a file had an EventSource and every one after it did not.
(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource;

// Unmount between tests. Without it a polling component from one test keeps its interval
// running through the next, which is the kind of failure that looks like flakiness.
afterEach(() => {
  cleanup();
  FakeEventSource.open = [];
  vi.restoreAllMocks();
  vi.useRealTimers();
});
