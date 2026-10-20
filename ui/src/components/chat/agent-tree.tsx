import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, GitBranch, Network, Square } from "lucide-react";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { agentCount } from "@/components/shell/chat-states";
import { fetchAgentTree, stopAgents } from "@/lib/backend";
import {
  AGENT_DOT,
  AGENT_INDENT,
  AGENT_WORD,
  agentLook,
  countAgents,
  flattenAgents,
  useAgentTree,
  type AgentLook,
  type AgentRow,
} from "@/lib/agents";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

/**
 * A chat's agents, drawn as the tree they are — the title bar's answer to "what are they doing?".
 *
 * This took the agent map's place, and is smaller on purpose. The map drew a workflow: a script,
 * its phases as columns, a prompt and a patch per agent, because a workflow's agents were nowhere
 * else — their work reached the thread only as one note at the end. An agent now is a whole
 * conversation: what it was told, everything it did and what it said back are in its own thread,
 * one click away. So this is the index to those threads — who exists, under whom, what each is
 * doing, the branch it works on and the last thing it said — plus the one action that is about the
 * tree rather than any conversation in it: stopping all of it.
 */

/** An agent's state as a dot — here, in the sidebar and on its own page. See `AGENT_DOT`. */
export function AgentDot({ look, className }: { look: AgentLook; className?: string }) {
  return (
    <span
      title={AGENT_WORD[look]}
      aria-hidden
      className={cn("size-1.5 shrink-0 rounded-full", AGENT_DOT[look], className)}
    />
  );
}

/**
 * The dialog, mounted once for the window and opened through `useAgentTree`.
 *
 * `onOpen` is the workspace's `openConversation`: opening an agent from here puts its conversation
 * in front of you and closes the dialog, because the conversation is the thing you went to see.
 */
export function AgentTreeHost({ onOpen }: { onOpen: (id: string) => void }) {
  const root = useAgentTree((state) => state.root);
  const close = useAgentTree((state) => state.close);
  return (
    <Dialog open={Boolean(root)} onOpenChange={(next) => (next ? null : close())}>
      {root ? (
        <AgentTreeDialog
          root={root}
          onOpen={(id) => {
            close();
            onOpen(id);
          }}
        />
      ) : null}
    </Dialog>
  );
}

export function AgentTreeDialog({ root, onOpen }: { root: string; onOpen: (id: string) => void }) {
  const cache = useQueryClient();
  const { data, isPending, isError } = useQuery({
    queryKey: keys.agentTree(root),
    queryFn: () => fetchAgentTree(root),
  });
  const [closed, setClosed] = useState(false);
  const [stopping, setStopping] = useState(false);
  const nodes = data?.agents ?? [];
  const counts = countAgents(nodes);
  const rows = flattenAgents(nodes, closed);

  const stopAll = async () => {
    setStopping(true);
    try {
      await stopAgents(root);
    } catch {
      /* The tree is refetched either way, and it is what says whether they stopped. */
    } finally {
      setStopping(false);
      void cache.invalidateQueries({ queryKey: ["agentTree"] });
      void cache.invalidateQueries({ queryKey: ["conversations"] });
    }
  };

  const summary = [
    counts.open ? `${agentCount(counts.open)} open` : "",
    counts.working ? `${counts.working} working` : "",
    counts.closed ? `${counts.closed} closed` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <DialogContent className="flex max-h-[80vh] w-[min(720px,calc(100%-2rem))] flex-col gap-3 sm:max-w-none">
      <div className="flex flex-col gap-0.5 pr-8">
        <DialogTitle className="flex items-center gap-2 text-base">
          <Network className="size-4 text-teal-600 dark:text-teal-400" aria-hidden />
          Agents
        </DialogTitle>
        <DialogDescription className="text-[12px]">
          {isPending
            ? "Reading the tree…"
            : isError
              ? "Could not read the agents under this chat."
              : summary
                ? `${summary} · each one is a conversation of its own`
                : "No agents under this chat yet. He starts them when the work splits."}
        </DialogDescription>
      </div>

      {rows.length ? (
        <ul aria-label="Agents" className="-mx-2 flex min-h-0 flex-col gap-0.5 overflow-y-auto">
          {rows.map((row) => (
            <TreeRow key={row.node.id} row={row} onOpen={onOpen} />
          ))}
        </ul>
      ) : null}

      <div className="border-border flex items-center gap-3 border-t pt-3">
        {counts.closed ? (
          <button
            type="button"
            onClick={() => setClosed((was) => !was)}
            aria-pressed={closed}
            className="text-muted-foreground hover:text-foreground cursor-pointer text-[11.5px]"
          >
            {closed ? "Hide closed" : `Show ${counts.closed} closed`}
          </button>
        ) : null}
        <span className="flex-1" />
        {/* Only while something is running. Stopping marks every open agent stopped, a "done" one
            included, and turning a finished agent into a stopped one would lose the one fact its
            dot had to say. */}
        <button
          type="button"
          disabled={!counts.working || stopping}
          onClick={() => void stopAll()}
          title="Stop every agent under this chat. Their conversations and branches stay, and the chat can message them to start again."
          className="text-muted-foreground hover:text-foreground ring-border flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] ring-1 disabled:cursor-default disabled:opacity-50"
        >
          <Square className="size-3" aria-hidden />
          {stopping ? "Stopping…" : "Stop all agents"}
        </button>
      </div>
    </DialogContent>
  );
}

/** One agent: its dot, name, state and branch, the last thing it said, and the way into it. */
function TreeRow({ row, onOpen }: { row: AgentRow; onOpen: (id: string) => void }) {
  const { node, depth } = row;
  const look = agentLook(node);
  return (
    <li
      className={cn(
        "relative",
        // The rail each level hangs from, drawn at its parent's dot so the nesting reads as a tree.
        depth > 0 && "before:bg-border before:absolute before:inset-y-0 before:left-(--rail) before:w-px",
      )}
      style={{
        paddingInlineStart: depth * AGENT_INDENT,
        ["--rail" as string]: `${(depth - 1) * AGENT_INDENT + 11}px`,
      }}
    >
      <div
        className={cn(
          "hover:bg-accent/40 group/agent flex items-start gap-2.5 rounded-lg px-2 py-1.5",
          look === "closed" && "opacity-60",
        )}
      >
        <AgentDot look={look} className="mt-[6px]" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex min-w-0 items-baseline gap-2">
            <span className="truncate text-[13px] font-medium">{node.name}</span>
            <span className="text-muted-foreground shrink-0 text-[11px]">{AGENT_WORD[look]}</span>
            {node.branch ? (
              <span className="text-muted-foreground flex min-w-0 items-center gap-1 font-mono text-[11px]">
                <GitBranch className="size-3 shrink-0 self-center" aria-hidden />
                <span className="truncate">{node.branch}</span>
              </span>
            ) : null}
          </div>
          {node.lastSaid ? (
            <p className="text-muted-foreground line-clamp-2 text-[11.5px] leading-snug">{node.lastSaid}</p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => onOpen(node.id)}
          aria-label={`Open ${node.name}`}
          className="text-muted-foreground hover:text-foreground ring-border hover:bg-accent/60 flex shrink-0 cursor-pointer items-center gap-1 rounded-md px-2 py-0.5 text-[11px] ring-1"
        >
          Open
          <ArrowUpRight className="size-3" aria-hidden />
        </button>
      </div>
    </li>
  );
}
