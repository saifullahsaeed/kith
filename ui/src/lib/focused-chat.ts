import { create } from "zustand";

import type { TurnUsage } from "@/components/assistant-ui/turn-usage";

/**
 * What the chat in front of you is doing, for the surfaces that are *about* a conversation
 * without being one.
 *
 * There is one consumer and it is the reason this exists: the context meter lives in the Work
 * panel, which is now its own pane, and it used to read `thread.messages` and `thread.isRunning`
 * straight off the runtime. That worked while a single runtime sat above the whole app. With one
 * per chat pane, the Work panel is outside every provider — `useAuiState` throws there, and
 * because it throws during render it took the whole panel down with it.
 *
 * So the focused chat publishes the two facts, and anything outside a pane reads them here. A
 * store rather than the module-level slot `active-composer` uses, because this one is *rendered
 * against*: the meter has to move when the turn does.
 *
 * Deliberately two facts and not a handle on the runtime. Handing out the runtime would let
 * anything outside a pane reach into a conversation it is not in, which is exactly the coupling
 * the split was for — and it would go stale the moment focus changed.
 */
export type FocusedChat = {
  /** The most recent reading of how full the window is, or undefined before the first turn.
   *  The same shape `latestUsage` returns, not a copy of it — a second definition here would
   *  drift from the one the thread actually produces. */
  usage?: TurnUsage;
  /** Whether a turn is running in the focused chat. */
  running: boolean;
  publish: (state: { usage?: FocusedChat["usage"]; running: boolean }) => void;
  /** Nothing is focused — no chat tab, or the focused pane is the board. */
  clear: () => void;
};

export const useFocusedChat = create<FocusedChat>((set) => ({
  usage: undefined,
  running: false,
  publish: ({ usage, running }) => set({ usage, running }),
  clear: () => set({ usage: undefined, running: false }),
}));
