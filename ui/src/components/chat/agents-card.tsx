import { useQuery } from "@tanstack/react-query";

import { AgentMap } from "@/components/chat/agent-tree";
import { fetchAgentTree } from "@/lib/backend";
import { useOpenConversation } from "@/lib/open-conversation";
import { keys } from "@/lib/query-keys";

/**
 * The agent map, as a card in the Activity tab.
 *
 * The sidebar keeps the list — who exists, under which chat, one click from each conversation —
 * and this is the picture beside the chat you are in: the whole tree it belongs to, how long each
 * agent has been at it, what each has spent, and its details on a click. The whole tree rather than
 * the part under this conversation, because the question it answers is "where is everything", and
 * an agent's own page marks where it sits in it.
 *
 * Renders nothing for a chat with no agents anywhere in its tree: a card about agents that do not
 * exist is furniture.
 */
export function AgentsCard({ conversationId }: { conversationId: string }) {
  const open = useOpenConversation();
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
  if (!root || !tree?.agents.length) return null;

  return (
    <section aria-label="Agent map" className="border-border bg-card flex shrink-0 flex-col gap-3 rounded-[14px] border p-3.5">
      <AgentMap
        root={root}
        onOpen={open}
        compact
        current={conversationId}
        heading={(summary) => (
          <div className="flex flex-col gap-0.5">
            <h3 className="text-[13px] font-semibold">Agent map</h3>
            <p className="text-muted-foreground text-[11.5px]">{summary}</p>
          </div>
        )}
      />
    </section>
  );
}
