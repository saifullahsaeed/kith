/**
 * The hop between the two halves of a feedback channel.
 *
 * Everything a surface in a reply knows and he does not — what a canvas control was set to, an
 * animated diagram whose choreography was refused — reaches him by riding along with the next
 * message. Both ends of that are tested: the store holds it, the prompt renders it. This is the
 * middle, which is where the canvas note was silently lost once already, and the shape of that
 * failure is worth remembering: every unit test passed with the feature entirely disconnected.
 */
import type { ThreadMessage } from "@assistant-ui/react";
import { beforeEach, describe, expect, it } from "vitest";

import { toWireMessages } from "@/lib/backend/stream";
import { useCanvasState } from "@/lib/canvas-state";
import { useFlowFailures } from "@/lib/flow-failures";

/** Only the parts `toWireMessages` reads. */
const said = (role: "user" | "assistant", text: string) =>
  ({ role, content: [{ type: "text", text }] }) as unknown as ThreadMessage;

const REFUSED = 'flow: no edge between "CompA" and "Modules"';

beforeEach(() => {
  useCanvasState.getState().clear();
  useFlowFailures.getState().clear();
});

describe("what rides along with the next message", () => {
  it("is nothing at all in the ordinary case", () => {
    const [message] = toWireMessages([said("user", "morning")]);
    expect(message).toEqual({ role: "user", content: "morning" });
  });

  it("carries a diagram that would not animate", () => {
    useFlowFailures.getState().report("one", REFUSED);
    const wire = toWireMessages([said("assistant", "here you go"), said("user", "and the second?")]);
    expect(wire.at(-1)).toMatchObject({ role: "user", diagrams: [REFUSED] });
  });

  it("carries what a canvas was set to, at the same time", () => {
    useFlowFailures.getState().report("one", REFUSED);
    useCanvasState.getState().report("doc", { title: "tuner", values: { foldAt: 5 } });
    const wire = toWireMessages([said("user", "so?")]);
    expect(wire[0].diagrams).toEqual([REFUSED]);
    expect(wire[0].canvas).toEqual([{ title: "tuner", values: { foldAt: 5 } }]);
  });

  it("attaches to the newest message, and only if they were the one who spoke", () => {
    // Held, not sent: it goes with something they say. Hung off the tail of a reply it would be
    // a note appended to his own words.
    useFlowFailures.getState().report("one", REFUSED);
    const wire = toWireMessages([said("user", "hi"), said("assistant", "hello")]);
    expect(wire.some((message) => "diagrams" in message)).toBe(false);
  });

  it("says it once, however many times the diagram reported it", () => {
    useFlowFailures.getState().report("one", REFUSED);
    useFlowFailures.getState().report("one", REFUSED);
    expect(toWireMessages([said("user", "so?")])[0].diagrams).toEqual([REFUSED]);
  });
});
