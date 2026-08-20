/**
 * A flow script that would not compile, held until the next thing they say.
 *
 * He writes an animated diagram, the choreography names a node that is not in the graph or a hop
 * with no arrow under it, and the animation is refused. The person sees why — one line under the
 * still diagram, naming the node and listing the ones that exist. He sees nothing at all. So the
 * same mistake comes back in the next reply, and the one after that, and the only way it ever
 * gets fixed is somebody typing out the error by hand.
 *
 * This is the wire that closes that. Deliberately the *same* shape as `canvas-state`, and for the
 * same reasons: held rather than sent, carried by their next message rather than by a turn of its
 * own, never persisted. A diagram that did not animate is not a question and must not start a
 * turn — this app has been burned by machinery that invents user messages — but it is very much
 * context for the next thing that gets said, and it costs nothing until something is.
 *
 * The error text is the whole payload because it is already the fix: "unknown node
 * \"UserTypesEmail\" in a route — nodes in this diagram: Core, Gateway, IdP, User" is the mistake,
 * the vocabulary, and the correction in one line. Nothing about the diagram's source is sent; he
 * wrote it and it is above him in the transcript.
 */
import { create } from "zustand";

/** How many can be waiting at once. A reply with more broken diagrams than this has one problem,
 *  not five, and the first few say what it is. */
const MAX_FAILURES = 4;

export const useFlowFailures = create<{
  failures: Record<string, string>;
  report: (id: string, error: string) => void;
  forget: (id: string) => void;
  clear: () => void;
}>((set) => ({
  failures: {},
  report: (id, error) =>
    set((state) => {
      if (state.failures[id] === error) return state;
      const next = { ...state.failures, [id]: error };
      // Insertion order is stable for string keys, so the oldest is simply first.
      for (const stale of Object.keys(next).slice(0, -MAX_FAILURES)) delete next[stale];
      return { failures: next };
    }),
  forget: (id) =>
    set((state) => {
      if (!(id in state.failures)) return state;
      const next = { ...state.failures };
      delete next[id];
      return { failures: next };
    }),
  clear: () => set({ failures: {} }),
}));

/** Everything currently broken, in the shape the wire wants. Empty in the ordinary case, where it
 *  costs the turn nothing. */
export function currentFlowFailures(): string[] {
  return Object.values(useFlowFailures.getState().failures).filter(Boolean);
}
