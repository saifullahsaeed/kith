import { useComposerRuntime } from "@assistant-ui/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CornerDownRight } from "lucide-react";

import { fetchPendingSteers, withdrawSteers } from "@/lib/commands";
import { keys } from "@/lib/query-keys";

/**
 * What you said to a running turn, while it is still waiting to be read.
 *
 * Pressing Enter during a turn steers: the text goes into a queue and reaches the model at the
 * next round boundary, which is seconds away and occasionally a minute. Until now it left the
 * composer and appeared nowhere — so for that gap there was nothing on screen, and no way to tell
 * a steer that had landed from one that was never sent. It came back into the thread only once the
 * model had picked it up, which is the one moment you no longer need reassurance.
 *
 * So it stays visible from the moment it is said, one row each, labelled with where it is going:
 * "Reaches him at his next step". When a round takes it, the server records it and the row goes —
 * replaced by the real thing, drawn in the turn at the step it landed.
 *
 * And it can be taken back, or taken back to edit. A queue you can watch but not change is a queue
 * that makes you sit and wait for your own mistake to be read out. Withdraw reaches only what is
 * still queued; once a round has it, it is in the prompt and the way to change your mind is to say
 * so. It takes the whole queue — the server offers nothing finer — so the controls sit on the last
 * row only, rather than promising a precision that is not there.
 */
export function PendingSteer({ conversationId }: { conversationId: string }) {
  const cache = useQueryClient();
  const composer = useComposerRuntime();
  const { data: pending = [] } = useQuery({
    queryKey: keys.steers(conversationId),
    queryFn: () => fetchPendingSteers(conversationId),
    enabled: Boolean(conversationId),
  });

  if (pending.length === 0) return null;

  const withdraw = async () => {
    // Gone from the screen before the round trip. Taking something back is a decision already
    // made, and watching your own retraction sit there is the thing being fixed.
    cache.setQueryData(keys.steers(conversationId), []);
    await withdrawSteers(conversationId).catch(() => {});
    void cache.invalidateQueries({ queryKey: keys.steers(conversationId) });
  };

  /** Take it back and put the words where you can change them. */
  const edit = async () => {
    const said = pending.join("\n\n");
    await withdraw();
    const now = composer.getState().text;
    composer.setText(now.trim() ? `${now}\n\n${said}` : said);
  };

  const several = pending.length > 1;

  return (
    <div className="flex flex-col gap-1.5">
      {pending.map((said, index) => {
        const last = index === pending.length - 1;
        return (
          <div
            key={`${index}-${said.slice(0, 24)}`}
            className="border-kith/25 bg-card flex min-w-0 items-center gap-2.5 rounded-xl border px-3 py-2 text-xs"
          >
            <CornerDownRight className="text-kith size-3.5 shrink-0" aria-hidden />
            {index === 0 ? (
              <span className="text-kith/85 shrink-0">
                Reaches him at his next step{several ? ` · ${pending.length} waiting` : ""}
              </span>
            ) : null}
            <span className="text-foreground min-w-0 flex-1 truncate" title={said}>
              “{said}”
            </span>
            {last ? (
              <>
                <button
                  type="button"
                  onClick={() => void edit()}
                  className="text-muted-foreground hover:text-foreground shrink-0 rounded-md px-2 py-0.5 transition-colors"
                >
                  Edit
                </button>
                <button
                  type="button"
                  onClick={() => void withdraw()}
                  className="text-muted-foreground hover:text-foreground shrink-0 rounded-md px-2 py-0.5 transition-colors"
                >
                  {several ? "Take these back" : "Take back"}
                </button>
              </>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
