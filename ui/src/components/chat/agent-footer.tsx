import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuiState } from "@assistant-ui/react";
import { ChevronRight, Network, SquareIcon } from "lucide-react";

import { AgentDot } from "@/components/chat/agent-tree";
import { fetchAgentTree, fetchLiveTurns } from "@/lib/backend";
import { agentLook } from "@/lib/agents";
import { stopTurn } from "@/lib/commands";
import { useOpenConversation } from "@/lib/open-conversation";
import { keys } from "@/lib/query-keys";

/**
 * Where an agent's composer would be: who it works for, the way back up, and Stop.
 *
 * An agent answers to the conversation that started it and to nothing else — the server refuses a
 * message or a steer from anyone else with a 409. A composer that every send bounced off would be
 * a box that looks like a way in and is not one, so it is not drawn; this says why in its place,
 * and offers the two things that *are* yours to do here: go up the chain to the conversation you
 * do talk to, and stop the turn.
 *
 * The chain is the server's (`GET …/agents` hands it back with the tree), from the conductor down
 * to this agent, each a link into that conversation. Stop is only offered while a turn is running,
 * and posts the same `/stop` the composer's button does — it ends this agent's turn, and its
 * handler hears about it the way it hears about anything an agent does.
 */
export function AgentFooter({ conversationId, name }: { conversationId: string; name: string }) {
  const open = useOpenConversation();
  const { data } = useQuery({
    queryKey: keys.agentTree(conversationId),
    queryFn: () => fetchAgentTree(conversationId),
    enabled: Boolean(conversationId),
  });
  const { data: live = [] } = useQuery({ queryKey: keys.liveTurns(), queryFn: fetchLiveTurns });
  const streaming = useAuiState((s) => s.thread.isRunning);
  const running = streaming || live.includes(conversationId);

  const chain = data?.chain ?? [];
  const handler = chain.length > 1 ? chain[chain.length - 2]?.name : "";
  const me = chain.at(-1);

  return (
    <div
      role="note"
      aria-label="This conversation is an agent's"
      className="bg-card border-border flex min-h-[3.25rem] w-full items-center gap-3 rounded-2xl border px-3.5 py-2.5"
    >
      <Network className="size-4 shrink-0 text-teal-600 dark:text-teal-400" aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-foreground/85 text-[13px] leading-snug">
          <span className="font-semibold">{name}</span> is an agent of{" "}
          <span className="font-semibold">{handler || "the chat that started it"}</span> — you talk
          to the conductor.
        </p>
        {chain.length ? (
          <nav aria-label="Who it works for" className="flex min-w-0 flex-wrap items-center gap-1 text-[11.5px]">
            {chain.map((link, at) => {
              const last = at === chain.length - 1;
              return (
                <Fragment key={link.id}>
                  {at ? <ChevronRight className="text-muted-foreground/60 size-3 shrink-0" aria-hidden /> : null}
                  {last ? (
                    <span aria-current="page" className="text-foreground flex items-center gap-1.5 font-medium">
                      {me ? <AgentDot look={agentLook({ state: me.state, working: running, asking: false })} /> : null}
                      {link.name}
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => open(link.id)}
                      title={`Open ${link.name}`}
                      className="text-muted-foreground hover:text-foreground cursor-pointer underline-offset-2 hover:underline"
                    >
                      {link.name}
                    </button>
                  )}
                </Fragment>
              );
            })}
          </nav>
        ) : null}
      </div>
      {running ? (
        <button
          type="button"
          onClick={() => void stopTurn(conversationId)}
          title={`Stop ${name}'s turn. Its handler is told, and can message it to carry on.`}
          aria-label={`Stop ${name}`}
          className="border-foreground/15 bg-accent text-foreground hover:bg-accent/70 flex h-8 shrink-0 items-center gap-1.5 rounded-[10px] border px-3 text-xs font-semibold transition-colors"
        >
          <SquareIcon className="size-2.5 fill-current" aria-hidden />
          Stop
        </button>
      ) : null}
    </div>
  );
}
