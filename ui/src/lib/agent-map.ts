import { create } from "zustand";

/**
 * Which conversation's agent map is open, and on which run.
 *
 * A store rather than local state because three places open it — the chip on the composer, the
 * Workflows section of the Work panel, and the card a `run_workflow` call leaves in the thread —
 * and only one of them (the chip) is where the window is drawn. Keyed by conversation so two
 * chat panes side by side do not both open a map when one is asked for.
 */
export const useAgentMap = create<{
  conversation: string;
  run: string;
  show: (conversation: string, run?: string) => void;
  choose: (run: string) => void;
  close: () => void;
}>((set) => ({
  conversation: "",
  run: "",
  show: (conversation, run = "") => set({ conversation, run }),
  choose: (run) => set({ run }),
  close: () => set({ conversation: "", run: "" }),
}));
