import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, GitBranch, Square, X } from "lucide-react";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { agentCount } from "@/components/shell/chat-states";
import { useNow } from "@/hooks/use-now";
import { fetchAgentTree, stopAgents, type AgentNode, type AgentTreeSelf } from "@/lib/backend";
import {
  AGENT_DOT,
  AGENT_WORD,
  agentLook,
  agentTook,
  countAgents,
  findAgent,
  prunedAgents,
  tokensWord,
  useAgentTree,
  type AgentLook,
} from "@/lib/agents";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

/**
 * The agent map: a chat's agents drawn as the tree they are — the title bar's answer to "what are
 * they doing?".
 *
 * Drawn the way Claude Code draws its own (he asked for exactly that view, 2026-10-20): the chat on
 * the left, each level of agents in a column to its right, joined by brackets, every node saying
 * how long it has been at it and how many tokens it has spent. Nested flex rows do the layout — a
 * node beside the column of its agents, centred on them — so a tree of any depth lays itself out
 * without measuring anything. Clicking a node opens its details beside the map; the conversation
 * itself is one more click.
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
  return (
    <DialogContent className="flex max-h-[86vh] w-[min(1240px,calc(100%-2rem))] flex-col gap-4 sm:max-w-none">
      <AgentMap root={root} onOpen={onOpen} heading={(text) => (
        <div className="flex flex-col gap-1 pr-8">
          <DialogTitle className="text-lg">Agent map</DialogTitle>
          <DialogDescription className="text-[13px]">{text}</DialogDescription>
        </div>
      )} />
    </DialogContent>
  );
}

/**
 * The map itself — in the title bar's dialog, and as a card in the Activity tab.
 *
 * `compact` is the column's version: narrower nodes, and the details under the map instead of
 * beside it, because the column is a third of the window and the map scrolls sideways inside it.
 * `current` marks the conversation on screen, so an agent's own page shows where it sits.
 */
export function AgentMap({
  root,
  onOpen,
  heading,
  compact = false,
  current = "",
}: {
  root: string;
  onOpen: (id: string) => void;
  heading: (summary: string) => React.ReactNode;
  compact?: boolean;
  current?: string;
}) {
  const cache = useQueryClient();
  const { data, isPending, isError } = useQuery({
    queryKey: keys.agentTree(root),
    queryFn: () => fetchAgentTree(root),
    enabled: Boolean(root),
  });
  const [closed, setClosed] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [selected, setSelected] = useState("");
  const nodes = data?.agents ?? [];
  const counts = countAgents(nodes);
  const shown = prunedAgents(nodes, closed);
  const chosen = selected ? findAgent(nodes, selected) : null;
  // Ticks while anything runs, so durations count up; a slow tick otherwise.
  const now = useNow(counts.working || data?.self?.working ? 1000 : 60_000);

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
    counts.open ? agentCount(counts.open) : "",
    counts.working ? `${counts.working} working` : "",
    counts.closed ? `${counts.closed} closed` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      {heading(
        isPending
          ? "Reading the tree…"
          : isError
            ? "Could not read the agents under this chat."
            : summary
              ? `${summary} · click an agent for details`
              : "No agents under this chat yet. He starts them when the work splits.",
      )}

      <div className={cn("flex min-h-0 flex-1 gap-4", compact && "flex-col gap-3")}>
        <div className="min-h-0 min-w-0 flex-1 overflow-auto pb-1">
          {data ? (
            // Left to right in the dialog, top to bottom in the column: a third of the window
            // cannot hold three columns of nodes side by side, but it can hold a tree that steps in.
            <div className={cn("flex py-1", compact ? "flex-col" : "w-max items-center")}>
              {/* A server older than the map sends no `self`; the tree is still worth drawing. */}
              <SelfNode self={data.self ?? { ...NO_SELF, id: root }} compact={compact} current={current === root} />
              {shown.length ? (
                <Children
                  nodes={shown}
                  now={now}
                  selected={selected}
                  onSelect={(id) => setSelected((was) => (was === id ? "" : id))}
                  compact={compact}
                  current={current}
                />
              ) : null}
            </div>
          ) : null}
        </div>
        {chosen ? (
          <Details
            node={chosen}
            now={now}
            onOpen={onOpen}
            onClose={() => setSelected("")}
            compact={compact}
          />
        ) : null}
      </div>

      <div className={cn("border-border flex items-center gap-3 border-t pt-3", compact && "pt-2.5")}>
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
        {/* Only while something is running: stopping a tree with nothing running would only turn
            finished agents' dots into stopped ones. */}
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
    </>
  );
}

const NO_SELF: AgentTreeSelf = {
  id: "",
  title: "",
  name: "conductor",
  working: false,
  tokens: 0,
  costUsd: 0,
  seconds: 0,
  contextTokens: 0,
  modelUsed: "",
};

/** The chat the tree grows from: its title, and the model and context its last round carried. */
function SelfNode({ self, compact, current }: { self: AgentTreeSelf; compact: boolean; current: boolean }) {
  const look: AgentLook = self.working ? "live" : "idle";
  const detail = [self.modelUsed ? shortModel(self.modelUsed) : "", self.contextTokens ? `${tokensWord(self.contextTokens)} in context` : ""]
    .filter(Boolean)
    .join(" · ");
  return (
    <div
      className={cn(
        "bg-card flex shrink-0 flex-col gap-0.5 rounded-xl border px-4 py-3",
        compact ? "w-full px-3 py-2.5" : "w-80",
        current ? "border-kith/60" : "border-border",
      )}
    >
      <span className="flex min-w-0 items-center gap-2.5">
        <AgentDot look={look} className="size-2" />
        <span className="min-w-0 truncate text-[14px] font-semibold" title={self.title}>
          {self.title || "This chat"}
        </span>
      </span>
      {detail ? <span className="text-muted-foreground pl-[18px] text-[12px] tabular-nums">{detail}</span> : null}
    </div>
  );
}

/**
 * A column of agents beside the node they belong to, each with its own agents beside it.
 *
 * The bracket is three pieces of one line per child, drawn by pseudo-elements: a tick out to the
 * child, and the vertical rail above and below its centre — left off above the first child and
 * below the last, which is what makes the rail end at the outermost ticks instead of running past.
 */
function Children({
  nodes,
  now,
  selected,
  onSelect,
  compact,
  current,
}: {
  nodes: AgentNode[];
  now: number;
  selected: string;
  onSelect: (id: string) => void;
  compact: boolean;
  current: string;
}) {
  if (compact) {
    // Top to bottom: a rail down the left from the node above, and a tick in to each node — the
    // rail stopping at the last tick rather than running on past it.
    return (
      <ul className="mt-2 flex flex-col gap-2 pl-5">
        {nodes.map((node, at) => (
          <li
            key={node.id}
            className={cn(
              "relative flex flex-col",
              "before:bg-border before:absolute before:-top-2 before:-left-2.5 before:w-px",
              at === nodes.length - 1 ? "before:h-[30px]" : "before:-bottom-2",
              "after:bg-border after:absolute after:top-[22px] after:-left-2.5 after:h-px after:w-2.5",
            )}
          >
            <MapNode
              node={node}
              now={now}
              selected={selected === node.id}
              onSelect={() => onSelect(node.id)}
              compact
              current={current === node.id}
            />
            {node.agents?.length ? (
              <Children nodes={node.agents} now={now} selected={selected} onSelect={onSelect} compact current={current} />
            ) : null}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <>
      <span aria-hidden className="bg-border h-px w-6 shrink-0" />
      <ul className="flex flex-col gap-3">
        {nodes.map((node, at) => (
          <li
            key={node.id}
            className={cn(
              "relative flex items-center pl-6",
              // The tick out to this node.
              "before:bg-border before:absolute before:top-1/2 before:left-0 before:h-px before:w-6",
              // The rail: from this node's centre up (unless first) and down (unless last).
              "after:bg-border after:absolute after:left-0 after:w-px",
              nodes.length === 1
                ? "after:hidden"
                : at === 0
                  ? "after:top-1/2 after:-bottom-3"
                  : at === nodes.length - 1
                    ? "after:-top-0 after:bottom-1/2"
                    : "after:top-0 after:-bottom-3",
            )}
          >
            <MapNode
              node={node}
              now={now}
              selected={selected === node.id}
              onSelect={() => onSelect(node.id)}
              compact={false}
              current={current === node.id}
            />
            {node.agents?.length ? (
              <Children
                nodes={node.agents}
                now={now}
                selected={selected}
                onSelect={onSelect}
                compact={false}
                current={current}
              />
            ) : null}
          </li>
        ))}
      </ul>
    </>
  );
}

/** One agent: its dot and name, then how long it has been at it and what it has spent. */
function MapNode({
  node,
  now,
  selected,
  onSelect,
  compact,
  current,
}: {
  node: AgentNode;
  now: number;
  selected: boolean;
  onSelect: () => void;
  compact: boolean;
  current: boolean;
}) {
  const look = agentLook(node);
  const figures = [agentTook(node, now), node.tokens ? `${tokensWord(node.tokens)} tokens` : ""]
    .filter(Boolean)
    .join(" · ");
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      title={AGENT_WORD[look]}
      className={cn(
        "bg-card flex shrink-0 cursor-pointer flex-col gap-0.5 rounded-xl border text-left transition-colors",
        compact ? "h-11 w-full justify-center px-3" : "w-72 px-4 py-2.5",
        selected
          ? "border-teal-500/70 ring-2 ring-teal-500/25"
          : current
            ? "border-kith/60"
            : "border-border hover:border-muted-foreground/40",
        look === "closed" && "opacity-60",
      )}
    >
      <span className="flex min-w-0 items-center gap-2.5">
        <AgentDot look={look} className="size-2" />
        <span className="min-w-0 truncate text-[14px] font-medium">{node.name}</span>
      </span>
      <span className="text-muted-foreground truncate pl-[18px] text-[12px] tabular-nums">
        {figures || AGENT_WORD[look]}
      </span>
    </button>
  );
}

/** What one agent is, beside the map: state, figures, branch, its task, what it last said. */
function Details({
  node,
  now,
  onOpen,
  onClose,
  compact,
}: {
  node: AgentNode;
  now: number;
  onOpen: (id: string) => void;
  onClose: () => void;
  compact: boolean;
}) {
  const [taskOpen, setTaskOpen] = useState(false);
  const look = agentLook(node);
  const figures = [
    AGENT_WORD[look],
    agentTook(node, now),
    node.tokens ? `${tokensWord(node.tokens)} tokens` : "",
    node.costUsd ? `$${node.costUsd.toFixed(node.costUsd < 1 ? 3 : 2)}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <aside
      aria-label={`${node.name}, in detail`}
      className={cn(
        "bg-card border-border flex shrink-0 flex-col gap-3 overflow-y-auto rounded-xl border p-4",
        compact ? "w-full" : "w-80",
      )}
    >
      <div className="flex items-start gap-2">
        <AgentDot look={look} className="mt-[7px] size-2" />
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold break-words">{node.name}</div>
          <div className="text-muted-foreground text-[11.5px] tabular-nums">{figures}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the details"
          className="text-muted-foreground hover:text-foreground -mt-1 -mr-1 cursor-pointer rounded p-1"
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>

      <dl className="text-muted-foreground grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11.5px]">
        {node.modelUsed || node.model ? (
          <>
            <dt>Model</dt>
            <dd className="text-foreground truncate font-mono">{shortModel(node.modelUsed || node.model)}</dd>
          </>
        ) : null}
        {node.branch ? (
          <>
            <dt>Branch</dt>
            <dd className="text-foreground flex min-w-0 items-center gap-1 font-mono">
              <GitBranch className="size-3 shrink-0" aria-hidden />
              <span className="truncate" title={node.branch}>
                {node.branch}
              </span>
            </dd>
          </>
        ) : (
          <>
            <dt>Folder</dt>
            <dd className="text-foreground truncate font-mono" title={node.folder}>
              shared — {node.folder || "its handler's"}
            </dd>
          </>
        )}
        {node.contextTokens ? (
          <>
            <dt>Context</dt>
            <dd className="text-foreground tabular-nums">{tokensWord(node.contextTokens)} tokens</dd>
          </>
        ) : null}
        {node.agents?.length ? (
          <>
            <dt>Agents</dt>
            <dd className="text-foreground">{agentCount(node.agents.length)} of its own</dd>
          </>
        ) : null}
      </dl>

      {node.lastSaid ? (
        <section className="flex flex-col gap-1">
          <h4 className="text-muted-foreground text-[10px] font-medium tracking-wide uppercase">Last said</h4>
          <p className="text-[12px] leading-relaxed">{node.lastSaid}</p>
        </section>
      ) : null}

      {node.task ? (
        <section className="flex flex-col gap-1">
          <button
            type="button"
            onClick={() => setTaskOpen((was) => !was)}
            aria-expanded={taskOpen}
            className="text-muted-foreground hover:text-foreground w-fit cursor-pointer text-[10px] font-medium tracking-wide uppercase"
          >
            {taskOpen ? "Hide its task" : "The task it was given"}
          </button>
          {taskOpen ? (
            <pre className="bg-muted/30 max-h-72 overflow-auto rounded-md p-2 font-sans text-[11.5px] leading-relaxed whitespace-pre-wrap">
              {node.task}
            </pre>
          ) : null}
        </section>
      ) : null}

      <button
        type="button"
        onClick={() => onOpen(node.id)}
        className="text-foreground ring-border hover:bg-accent/60 mt-auto flex cursor-pointer items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-medium ring-1"
      >
        Open its conversation
        <ArrowUpRight className="size-3.5" aria-hidden />
      </button>
    </aside>
  );
}

/** `anthropic/claude-opus-5.5` → `claude-opus-5.5`: the provider is noise beside a node. */
function shortModel(model: string): string {
  return model.split("/").pop() || model;
}
