/**
 * Which board is open over the conversation, if any.
 *
 * The board existed and was three clicks away — control panel, tasks, the task, open board — which
 * is the same as not existing. He finishes four designs *in the chat*, and what the chat showed
 * was a paragraph about four files. The work and the surface for looking at the work were in
 * different rooms.
 *
 * A store rather than props because the two ends are far apart and neither owns the other: the
 * thing that opens a board is a tool result rendered deep inside a message, and the thing that
 * draws it has to sit above the whole pane. Threading a callback between them would mean every
 * component in between knowing about designs.
 */
import { create } from "zustand";

export interface OpenBoard {
  taskId: number;
  projectId: number | null;
  /** What to call it while the task detail is still being fetched. */
  title?: string;
}

export const useBoardOverlay = create<{
  open: OpenBoard | null;
  show: (board: OpenBoard) => void;
  hide: () => void;
}>((set) => ({
  open: null,
  show: (board) => set({ open: board }),
  hide: () => set({ open: null }),
}));
