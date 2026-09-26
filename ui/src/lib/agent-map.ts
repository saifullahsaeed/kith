import { create } from "zustand";

/**
 * Which conversation's agent map is open, and on which run.
 *
 * A store rather than local state because three places open it — the title bar's workflow pill,
 * the Workflows section of the Work panel, and the card a `run_workflow` call leaves in the
 * thread — and the window is drawn once, by `AgentMapHost`, for whichever conversation asked.
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
