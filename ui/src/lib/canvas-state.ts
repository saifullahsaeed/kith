/**
 * What the person did to a canvas, held until the next thing they say.
 *
 * A canvas is the first thing in a reply that is not finished when he stops writing. A diagram is
 * over the moment it is drawn; an instrument sits there and gets used, and everything that
 * happens to it after the turn ends is invisible to him. You can step a nine-step click-through
 * to the one fork that needs you, and as far as he is concerned you never opened it.
 *
 * So the canvas reports, this holds what it said, and the next message carries it. Deliberately
 * *not* a message of its own: a turn nobody asked for is a turn nobody reads, and this app has
 * been burned before by machinery that invents user messages. Moving a slider is not a question.
 * It is context for the question you are about to ask, and it should cost nothing until you ask
 * one.
 *
 * Keyed by the served document's id, so two canvases in one reply stay separate and a canvas that
 * re-mounts under a new id starts clean rather than inheriting a stale reading. Never persisted:
 * the state of a control you can no longer see is not context, it is a ghost.
 */
import { create } from "zustand";

import type { CanvasValue } from "@/lib/canvas-bridge";

export interface CanvasReading {
  /** The page's own `<title>`, when it has one — what he called it, so what you would call it. */
  title: string;
  values: Record<string, CanvasValue>;
}

/** More than this on screen at once and the oldest is dropped rather than the newest refused;
 *  what you are looking at now is what matters. */
const MAX_CANVASES = 8;

/** A reading, and which chat it was taken in. */
type Held = CanvasReading & {
  /** The conversation whose reply drew this canvas. `""` for a chat not yet named, which can
   *  never be asked for — see `currentCanvasState`. */
  conversation: string;
};

export const useCanvasState = create<{
  readings: Record<string, Held>;
  report: (conversation: string, id: string, reading: CanvasReading) => void;
  forget: (id: string) => void;
  clear: () => void;
}>((set) => ({
  readings: {},
  report: (conversation, id, reading) =>
    set((state) => {
      const next = { ...state.readings, [id]: { ...reading, conversation } };
      const keys = Object.keys(next);
      // Insertion order is stable in an object with string keys, so the oldest is simply first.
      for (const stale of keys.slice(0, Math.max(0, keys.length - MAX_CANVASES))) delete next[stale];
      return { readings: next };
    }),
  forget: (id) =>
    set((state) => {
      if (!(id in state.readings)) return state;
      const next = { ...state.readings };
      delete next[id];
      return { readings: next };
    }),
  clear: () => set({ readings: {} }),
}));

/**
 * What was set **in this conversation**, in the shape the wire wants.
 *
 * The conversation is not a nicety. This store is global and the app opens several chats at once,
 * so "everything currently set" meant everything set anywhere: a slider moved in one chat rode
 * along with the next message sent in another, as context for a turn that had never drawn it.
 *
 * The leak widened the day tabs stopped unmounting. `html-canvas` forgets its reading on unmount,
 * so while switching chats tore the other one down this was masked by a lifetime accident — and
 * keeping every tab mounted, which is right for every other reason, made both chats' canvases live
 * at once. Lifetime was doing work that ownership should have been doing.
 *
 * An unnamed conversation matches nothing. A draft chat's first turn has no id, and nothing can
 * have been drawn in a conversation that does not exist yet.
 */
export function currentCanvasState(conversation: string): CanvasReading[] {
  if (!conversation) return [];
  return Object.values(useCanvasState.getState().readings)
    .filter((held) => held.conversation === conversation)
    .filter((held) => Object.keys(held.values).length > 0)
    .map(({ title, values }) => ({ title, values }));
}
