import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Network } from "lucide-react";

import { AgentDot } from "@/components/chat/agent-tree";
import { useNow } from "@/hooks/use-now";
import { fetchAgentTree } from "@/lib/backend";
import { summarizeAgents, tokensWord, treeTook, useAgentTree, type AgentLook } from "@/lib/agents";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

/**
 * The agents in this chat's tree, as one card in the Activity tab — counted, not drawn.
 *
 * This was the whole agent map, compact. In a column a third of the window wide the map was a tall
 * scroll of nodes that repeated the sidebar's list beside it, and pushed every card under it out of
 * view on a tree of any size. The questions a glance at this column asks are smaller: is anything
 * still going, is anyone stuck on a question, what has it cost and for how long. Those fit in four
 * lines, and the map is one click away — the card is that click.
 *
 * The whole tree rather than the part under this conversation, because "where is everything" is
 * the question, and an agent's own page already says where it sits in it.
 *
 * Renders nothing for a chat with no agents anywhere in its tree: a card about agents that do not
 * exist is furniture.
 */
export function AgentsCard({ conversationId }: { conversationId: string }) {
  // Which tree this conversation is in. The same request the map makes when this is the root.
  const { data: here } = useQuery({
    queryKey: keys.agentTree(conversationId),
    queryFn: () => fetchAgentTree(conversationId),
    enabled: Boolean(conversationId),
  });
  const root = here?.root ?? "";
  const { data: tree } = useQuery({
    queryKey: keys.agentTree(root),
    queryFn: () => fetchAgentTree(root),
    enabled: Boolean(root),
  });
  const nodes = tree?.agents ?? [];
  const sum = summarizeAgents(nodes);
  // A second hand only while the duration is still counting up; once nothing is busy it is fixed.
  const now = useNow(sum.working + sum.queued + sum.asking + sum.waiting ? 1000 : 60_000);
  if (!root || !nodes.length) return null;

  const figures: { look: AgentLook; count: number; word: string; title?: string; always?: boolean }[] = [
    // Working is always said — "0 working" is the answer to the question the card is for. The rest
    // only when there is one: "0 asking" beside every quiet tree is a line of noise.
    { look: "live", count: sum.working, word: "working", always: true },
    { look: "queued", count: sum.queued, word: "queued", title: "Waiting for a free turn slot" },
    { look: "waiting", count: sum.waiting, word: "waiting on agents", title: "Waiting on agents of their own" },
    { look: "asking", count: sum.asking, word: "asking", title: "Parked on a question to their handler" },
    { look: "done", count: sum.done, word: "done" },
    { look: "stopped", count: sum.stopped, word: "stopped" },
    { look: "closed", count: sum.closed, word: "closed" },
  ];
  const took = treeTook(nodes, now);
  const totals = [`${tokensWord(sum.tokens)} tokens`, took ? `running ${took}` : ""].filter(Boolean).join(" · ");

  return (
    <button
      type="button"
      onClick={() => useAgentTree.getState().show(root)}
      className={cn(
        "group border-border bg-card flex w-full shrink-0 cursor-pointer flex-col gap-1.5 rounded-[14px] border px-3.5 py-3 text-left transition-colors",
        "hover:border-muted-foreground/40 focus-visible:ring-kith/60 focus-visible:ring-2 focus-visible:outline-none",
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <Network className="size-3.5 shrink-0 text-teal-600 dark:text-teal-400" aria-hidden />
        <span className="text-[13px] font-semibold">Agents</span>
        <span className="flex-1" />
        {/* Said, not only implied by the hover: a card that is secretly a button is a card nobody
            clicks. */}
        <span className="text-muted-foreground group-hover:text-foreground flex shrink-0 items-center gap-0.5 text-[11.5px] transition-colors">
          Open agent map
          <ArrowUpRight className="size-3" aria-hidden />
        </span>
      </span>

      <span className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[12px] tabular-nums">
        {figures
          .filter((one) => one.always || one.count)
          .map((one) => (
            <span
              key={one.look}
              title={one.title}
              className={cn("flex items-center gap-1.5", one.count ? "text-foreground/85" : "text-muted-foreground/60")}
            >
              <AgentDot look={one.look} />
              {one.count} {one.word}
            </span>
          ))}
      </span>

      <code className="text-muted-foreground/80 truncate font-mono text-[11px]" title="Tokens every agent in the tree has processed, and how long the tree has been at it">
        {totals}
      </code>

      {sum.askingNames.length ? (
        /* On its own line because it is the one thing here that is waiting on somebody — its
           handler, not you, which is why it is teal and not amber. */
        <span className="flex min-w-0 items-center gap-1.5 text-[12px] text-teal-700 dark:text-teal-300">
          <AgentDot look="asking" />
          <span className="truncate">{askingLine(sum.askingNames)}</span>
        </span>
      ) : null}
    </button>
  );
}

/** "schema is asking its handler", "api and schema are asking their handlers". */
function askingLine(names: string[]): string {
  if (names.length === 1) return `${names[0]} is asking its handler`;
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1)} are asking their handlers`;
}
