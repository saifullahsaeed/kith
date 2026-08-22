import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CornerDownRight, X } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
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
 * So it stays visible from the moment it is said, in the person's own alignment but unmistakably
 * not yet delivered: dashed, dimmed, and labelled with where it is going. When a round takes it,
 * the server records it and this disappears — replaced by the real message, in the turn that acted
 * on it.
 *
 * And it can be taken back. A queue you can watch but not change is a queue that makes you sit and
 * wait for your own mistake to be read out. Withdraw reaches only what is still queued; once a
 * round has it, it is in the prompt and the way to change your mind is to say so.
 */
export function PendingSteer({ conversationId }: { conversationId: string }) {
  const cache = useQueryClient();
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

  return (
    <div className="mx-auto mb-2 flex w-full max-w-(--thread-max-width) flex-col items-end gap-1.5 px-4">
      {pending.map((said, index) => (
        <div
          key={`${index}-${said.slice(0, 24)}`}
          className="border-border/60 bg-muted/20 text-muted-foreground/80 flex max-w-[80%] items-start gap-2 rounded-2xl border border-dashed px-3.5 py-2 text-sm"
        >
          <span className="min-w-0 flex-1 break-words whitespace-pre-wrap">{said}</span>
          {/* Only on the last one: withdraw takes the whole queue, and a cross on every row
              would promise a precision the server does not offer. */}
          {index === pending.length - 1 ? (
            <TooltipIconButton
              tooltip={pending.length > 1 ? "Take all of these back" : "Take this back"}
              side="left"
              variant="ghost"
              size="icon"
              className="hover:text-foreground -me-1 size-5 shrink-0"
              onClick={() => void withdraw()}
            >
              <X className="size-3.5" />
            </TooltipIconButton>
          ) : null}
        </div>
      ))}
      <span className="text-muted-foreground/50 flex items-center gap-1 pe-1 text-[11px]">
        <CornerDownRight className="size-3" aria-hidden />
        {pending.length > 1 ? `${pending.length} waiting` : "waiting"} for the next round
      </span>
    </div>
  );
}
