import { useCallback, useEffect, useState } from "react";

import { fetchMood, type Mood } from "@/lib/backend/mood";

/** Polls Kith's felt state so the room can reflect how he's doing. */
export function useMood() {
  const [mood, setMood] = useState<Mood | null>(null);

  const refresh = useCallback(() => {
    fetchMood()
      .then(setMood)
      .catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 10000);
    return () => window.clearInterval(id);
  }, [refresh]);

  return { mood, refresh };
}
