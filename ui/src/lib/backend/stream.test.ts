/**
 * The hop between the two halves of a feedback channel.
 *
 * What a surface in a reply knows and he does not — an animated diagram whose choreography was
 * refused — reaches him by riding along with the next message. Both ends of that are tested: the
 * store holds it, the prompt renders it. This is the middle, which is where a note (the retired
 * canvas readings) was silently lost once already, and the shape of that
 * failure is worth remembering: every unit test passed with the feature entirely disconnected.
 */
import type { ThreadMessage } from "@assistant-ui/react";
import { beforeEach, describe, expect, it } from "vitest";

import { toWireMessages } from "@/lib/backend/stream";
import { useFlowFailures } from "@/lib/flow-failures";

/** Only the parts `toWireMessages` reads. */
const said = (role: "user" | "assistant", text: string) =>
  ({ role, content: [{ type: "text", text }] }) as unknown as ThreadMessage;

const REFUSED = 'flow: no edge between "CompA" and "Modules"';

beforeEach(() => {
  useFlowFailures.getState().clear();
});

describe("what rides along with the next message", () => {
  it("is nothing at all in the ordinary case", () => {
    const [message] = toWireMessages([said("user", "morning")], "c-1");
    expect(message).toEqual({ role: "user", content: "morning" });
  });

  it("carries a diagram that would not animate", () => {
    useFlowFailures.getState().report("c-1", "one", REFUSED);
    const wire = toWireMessages([said("assistant", "here you go"), said("user", "and the second?")], "c-1");
    expect(wire.at(-1)).toMatchObject({ role: "user", diagrams: [REFUSED] });
  });

  it("attaches to the newest message, and only if they were the one who spoke", () => {
    // Held, not sent: it goes with something they say. Hung off the tail of a reply it would be
    // a note appended to his own words.
    useFlowFailures.getState().report("c-1", "one", REFUSED);
    const wire = toWireMessages([said("user", "hi"), said("assistant", "hello")], "c-1");
    expect(wire.some((message) => "diagrams" in message)).toBe(false);
  });

  /* **Whose diagram.**
   *
   * The store is global and the app opens several chats at once, so "everything currently set"
   * was everything set *anywhere*: a refusal in one conversation rode along with the next message
   * sent in another, as context for a turn that had never seen it. */
  it("carries only what was set in the conversation being written to", () => {
    useFlowFailures.getState().report("c-2", "one", REFUSED);
    const wire = toWireMessages([said("user", "so?")], "c-1");
    expect(wire[0].diagrams).toBeUndefined();
  });

  it("carries nothing at all when the conversation is not yet named", () => {
    /* The first turn of a draft chat has no id. Nothing can have been set in a conversation that
     * does not exist, so the honest answer is empty rather than everyone else's. */
    useFlowFailures.getState().report("c-1", "one", REFUSED);
    expect(toWireMessages([said("user", "hi")], "")[0].diagrams).toBeUndefined();
  });

  it("says it once, however many times the diagram reported it", () => {
    useFlowFailures.getState().report("c-1", "one", REFUSED);
    useFlowFailures.getState().report("c-1", "one", REFUSED);
    expect(toWireMessages([said("user", "so?")], "c-1")[0].diagrams).toEqual([REFUSED]);
  });
});
