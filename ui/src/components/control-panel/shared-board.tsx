import { useCallback, useEffect, useState } from "react";
import { GitPullRequestArrow, TriangleAlert } from "lucide-react";

import {
  fetchSharedBoard,
  howLong,
  takeSharedBoardIn,
  type SharedBoard,
} from "@/lib/backend";

/**
 * What this project's folder is holding that the board has not taken in.
 *
 * `.kith/` commits with the repository, so a second person's tasks reach this machine as files
 * — and the board is a per-machine database that has never read them. Every part of that was
 * reachable only by the model until now, which meant a person working through the panel could
 * not see that somebody else's work was sitting there, let alone take it.
 *
 * **Renders nothing when there is nothing**, which is the common case and the reason this sits
 * inline above the lanes rather than in a section of its own. A permanent panel saying "nothing
 * has come in" is a panel people stop reading, and this has to be noticed on the day it matters.
 */
export function SharedBoardBanner({
  projectId,
  onTaken,
}: {
  projectId: number;
  onTaken: () => void;
}) {
  const [state, setState] = useState<SharedBoard | null>(null);
  const [taking, setTaking] = useState(false);
  const [failed, setFailed] = useState("");

  const look = useCallback(() => {
    fetchSharedBoard(projectId)
      .then(setState)
      .catch(() => setState(null));
  }, [projectId]);

  useEffect(look, [look]);

  const take = async () => {
    setTaking(true);
    setFailed("");
    try {
      await takeSharedBoardIn(projectId);
      // The board it just changed is upstream of this component, so the refresh is theirs to
      // do; looking again afterwards is what turns the banner off.
      onTaken();
      look();
    } catch (err) {
      setFailed(err instanceof Error ? err.message : String(err));
    } finally {
      setTaking(false);
    }
  };

  if (!state || !state.folder) return null;

  // Nothing was read, and saying which file is the whole value — the person has to go and look
  // at it, and "sync failed" would send them hunting.
  if (state.blocked) {
    return (
      <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3.5 py-3 text-sm">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-500" />
        <div className="min-w-0">
          <p className="font-medium">This project's shared folder can't be read.</p>
          <p className="text-muted-foreground mt-0.5">
            {state.blocked}. Nothing has been taken from it — sort that out first, and until you
            do this board may be behind without saying so.
          </p>
        </div>
      </div>
    );
  }

  const waiting = state.added.length + state.updated.length;
  if (!waiting && !state.kept.length) return null;

  return (
    <div className="mb-4 rounded-xl border border-border/60 bg-muted/20 px-3.5 py-3 text-sm">
      <div className="flex items-start gap-2.5">
        <GitPullRequestArrow className="text-kith mt-0.5 size-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {waiting > 0
              ? `${waiting} task${waiting === 1 ? "" : "s"} in the shared folder aren't on this board`
              : "The shared folder differs from this board"}
          </p>
          <ul className="text-muted-foreground mt-1 space-y-0.5">
            {state.added.length > 0 && <li>New from someone else: {state.added.join(", ")}</li>}
            {state.updated.length > 0 && <li>Newer in the folder: {state.updated.join(", ")}</li>}
            {/* Named even though nothing will happen to them: a difference the person cannot
                see is a difference they will rediscover as a surprise. */}
            {state.kept.length > 0 && (
              <li>Newer here, so they stay as they are: {state.kept.join(", ")}</li>
            )}
          </ul>
          <p className="text-muted-foreground/80 mt-1.5 text-xs">
            The folder last changed {howLong(state.changed_ago)}. Nothing arrives on its own —
            pull the repository first if nobody has today.
          </p>
          {failed && <p className="text-destructive mt-1.5 text-xs">{failed}</p>}
        </div>
        {waiting > 0 && (
          <button
            onClick={take}
            disabled={taking}
            className="bg-kith/15 text-kith hover:bg-kith/25 shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50"
          >
            {taking ? "Taking…" : "Take them in"}
          </button>
        )}
      </div>
    </div>
  );
}
