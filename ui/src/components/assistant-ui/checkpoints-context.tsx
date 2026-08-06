import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { fetchCheckpoints, type Checkpoint } from "@/lib/backend/checkpoints";

type CheckpointsValue = {
  checkpoints: Checkpoint[];
  /** Call after a restore so the just-taken safety checkpoint shows up too. */
  reload: () => void;
  /** Turns the render window dropped off the front, so a rendered message's index can be read
   *  back as the absolute turn index checkpoints are tagged with. Zero when nothing is windowed. */
  turnOffset: number;
};

const CheckpointsContext = createContext<CheckpointsValue>({
  checkpoints: [],
  reload: () => {},
  turnOffset: 0,
});

/** A conversation's checkpoints, fetched once per conversation and made available to
 * anything under it — the "Restore files to here" action needs to know, per message,
 * whether there's anything to restore to at all. */
export function CheckpointsProvider({
  conversationId,
  turnOffset = 0,
  children,
}: {
  conversationId: string;
  turnOffset?: number;
  children: ReactNode;
}) {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);

  const reload = useCallback(() => {
    if (!conversationId) {
      setCheckpoints([]);
      return;
    }
    void fetchCheckpoints(conversationId)
      .then(setCheckpoints)
      .catch(() => setCheckpoints([]));
  }, [conversationId]);

  useEffect(reload, [reload]);

  // Memoised: a fresh object here re-renders every consumer on any parent render, and the
  // consumer is the action bar on every message in the thread.
  const value = useMemo(() => ({ checkpoints, reload, turnOffset }), [checkpoints, reload, turnOffset]);

  return <CheckpointsContext.Provider value={value}>{children}</CheckpointsContext.Provider>;
}

export const useCheckpoints = () => useContext(CheckpointsContext);
