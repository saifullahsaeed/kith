import { useCallback, useEffect, useState } from "react";
import { GitPullRequestArrow, TriangleAlert } from "lucide-react";

import {
  fetchSharedBoard,
  howLong,
  takeSharedBoardIn,
  type SharedBoard,
} from "@/lib/backend";
import { cn } from "@/lib/utils";
import { BUTTON } from "./chrome";
import { sharedSummary } from "./project-page-model";

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
 *
 * **One line until you ask.** The banner used to print every task's name, three of them
 * truncated mid-word, above the board it was describing — five lines of names pushing the lanes
 * down before you had decided whether you cared. The count and what kind of difference it is
 * are what decide that; Review opens the names.
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
  const [open, setOpen] = useState(false);

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

  const summary = sharedSummary(state);
  if (!state || !summary) return null;

  return (
    <div
      role="status"
      className={cn(
        "bg-card mt-4 shrink-0 rounded-[10px] border",
        // Nothing was read, and saying which file is the whole value — the person has to go and
        // look at it, and "sync failed" would send them hunting.
        summary.blocked ? "border-kith/25" : "border-border",
      )}
    >
      <div className="flex h-[42px] items-center gap-2.5 ps-3.5 pe-2">
        {summary.blocked ? (
          <TriangleAlert className="text-kith size-[15px] shrink-0" />
        ) : (
          <GitPullRequestArrow className="text-muted-foreground size-[15px] shrink-0" />
        )}
        <span className="min-w-0 flex-1 truncate text-[13px]">
          <span className="text-foreground font-semibold">{summary.headline}</span>
          {summary.detail ? <span className="text-muted-foreground"> — {summary.detail}</span> : null}
        </span>
        {failed ? (
          <span className="text-destructive max-w-[40ch] shrink-0 truncate text-xs" title={failed}>
            {failed}
          </span>
        ) : null}
        {!summary.blocked ? (
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((was) => !was)}
            className={cn(BUTTON.secondary, "h-7 rounded-[7px] px-[11px] text-[12.5px]")}
          >
            {open ? "Hide" : "Review"}
          </button>
        ) : null}
        {summary.waiting > 0 ? (
          <button type="button" onClick={take} disabled={taking} className={cn(BUTTON.primary, BUTTON.small)}>
            {taking ? "Taking…" : "Take them in"}
          </button>
        ) : null}
      </div>

      {open && !summary.blocked ? (
        <div className="border-border/80 text-muted-foreground border-t px-3.5 pt-2.5 pb-3 ps-[39px] text-[12.5px] leading-relaxed">
          <ul className="space-y-0.5">
            {state.added.length > 0 && (
              <li>
                <span className="text-foreground/80">New from someone else:</span> {state.added.join(", ")}
              </li>
            )}
            {state.updated.length > 0 && (
              <li>
                <span className="text-foreground/80">Newer in the folder:</span> {state.updated.join(", ")}
              </li>
            )}
            {/* Named even though nothing will happen to them: a difference the person cannot
                see is a difference they will rediscover as a surprise. */}
            {state.kept.length > 0 && (
              <li>
                <span className="text-foreground/80">Newer here, so they stay as they are:</span>{" "}
                {state.kept.join(", ")}
              </li>
            )}
          </ul>
          <p className="text-muted-foreground/80 mt-1.5 text-xs">
            The folder last changed {howLong(state.changed_ago)}. Nothing arrives on its own — pull the
            repository first if nobody has today.
          </p>
        </div>
      ) : null}

      {summary.blocked ? (
        <p className="border-border/80 text-muted-foreground border-t px-3.5 py-2.5 ps-[39px] text-[12.5px]">
          Nothing has been taken from it — sort that out first, and until you do this board may be
          behind without saying so.
        </p>
      ) : null}
    </div>
  );
}
