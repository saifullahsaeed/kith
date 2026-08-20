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

export const useCanvasState = create<{
  readings: Record<string, CanvasReading>;
  report: (id: string, reading: CanvasReading) => void;
  forget: (id: string) => void;
  clear: () => void;
}>((set) => ({
  readings: {},
  report: (id, reading) =>
    set((state) => {
      const next = { ...state.readings, [id]: reading };
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

/** Everything currently set, in the shape the wire wants. Empty when nothing has been touched,
 *  which is the common case and costs the turn nothing. */
export function currentCanvasState(): CanvasReading[] {
  return Object.values(useCanvasState.getState().readings).filter(
    (reading) => Object.keys(reading.values).length > 0,
  );
}
