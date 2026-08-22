import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

/**
 * jsdom has no `EventSource`, and the app opens one on mount whenever it is not running inside the
 * desktop shell — so without this a test of the *rendering* fails on the live-update plumbing,
 * which is not what it is testing. A stub rather than a mock library: `lib/backend/events.ts` only
 * ever adds listeners and closes, and anything more elaborate here would be a second
 * implementation to keep in step with.
 *
 * Named events, not `onmessage`. One connection carries both `changed` and `activity` now, told
 * apart by the SSE `event:` field — so the stub has to speak `addEventListener` or it would be
 * testing a wire format the server does not send. Every frame carries an `id`, because the ids are
 * how a gap is noticed; `emit` numbers them for you.
 */
class FakeEventSource {
  static open: FakeEventSource[] = [];
  static nextId = 1;
  /** Which run of the server the stub is pretending to be. Change it to simulate a restart. */
  static epoch = "run-1";
  private listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>();
  closed = false;
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.open.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  removeEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.get(type)?.delete(listener);
  }

  close() {
    this.closed = true;
  }

  /** Push one frame at every live source. `id` defaults to the next in sequence. */
  static send(type: string, data: unknown, id?: number) {
    const at = id ?? FakeEventSource.nextId++;
    if (id !== undefined) FakeEventSource.nextId = id + 1;
    // `<epoch>-<n>`, as the server writes it: the number says where in the sequence, the epoch
    // says which sequence. A test that wants to simulate a restart sets `epoch` and carries on.
    const event = {
      data: JSON.stringify(data),
      // An empty epoch writes a bare number, which is what a server too old to name its run sends.
      lastEventId: FakeEventSource.epoch ? `${FakeEventSource.epoch}-${at}` : String(at),
    } as MessageEvent<string>;
    for (const source of FakeEventSource.open) {
      if (source.closed) continue;
      for (const listener of source.listeners.get(type) ?? []) listener(event);
    }
  }

  /** The common case: something changed. */
  static emit(kind: string, conversation = "", id?: number) {
    FakeEventSource.send("changed", { kind, conversation }, id);
  }
}
// Assigned rather than `vi.stubGlobal`: the `afterEach` below calls `restoreAllMocks`, which undoes a
// stubbed global — so the first test in a file had an EventSource and every one after it did not.
(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource;

// Unmount between tests. Without it a component from one test keeps listening through the next,
// which is the kind of failure that looks like flakiness.
afterEach(() => {
  cleanup();
  FakeEventSource.open = [];
  FakeEventSource.nextId = 1;
  FakeEventSource.epoch = "run-1";
  // The shell's channel, if a test pretended to be inside the desktop app.
  delete (window as unknown as { kith?: unknown }).kith;
  vi.restoreAllMocks();
  vi.useRealTimers();
});
