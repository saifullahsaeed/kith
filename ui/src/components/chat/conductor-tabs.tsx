import { useRef, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare, X } from "lucide-react";

import { AgentDot } from "@/components/chat/agent-tree";
import { fetchAgentTree, type ConversationDetail } from "@/lib/backend";
import { AGENT_WORD, agentLook, findAgent, type AgentLook } from "@/lib/agents";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

/**
 * A chat tab, with the agents opened inside it.
 *
 * An agent opened from anywhere — the sidebar, the agent map, a report's name, a breadcrumb — opens
 * here, in its conductor's tab, rather than as a tab of its own (see `lib/agent-views`). Once one
 * has been, the tab grows a slim strip under its header: the conductor first, then each agent
 * opened in it, with its state and a ×. What is picked is what the tab shows — its thread, its
 * session bar and its companion column — and only that conversation is mounted.
 *
 * **The body's position never changes**, for the reason `withCompanions` gives at length: the strip
 * is `null` or an element in the first slot, and the body is always the second, so the strip
 * appearing or going cannot unmount the chat under it. Which conversation the body holds is the
 * caller's key on the pane inside it, not this component's business.
 */
export function ConductorTabs({
  root,
  open,
  shown,
  onShow,
  onClose,
  children,
}: {
  /** The conductor's conversation — the tab's own. */
  root: string;
  /** The agents opened in this tab, in strip order. */
  open: readonly string[];
  /** What the tab shows: `root`, or one of `open`. */
  shown: string;
  onShow: (id: string) => void;
  onClose: (id: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      {root && open.length ? (
        <Strip root={root} open={open} shown={shown} onShow={onShow} onClose={onClose} />
      ) : null}
      <div className="relative min-h-0 flex-1">{children}</div>
    </div>
  );
}

function Strip({
  root,
  open,
  shown,
  onShow,
  onClose,
}: {
  root: string;
  open: readonly string[];
  shown: string;
  onShow: (id: string) => void;
  onClose: (id: string) => void;
}) {
  const cache = useQueryClient();
  const strip = useRef<HTMLDivElement | null>(null);
  /* Names and states from the root's tree — the request the agent map and the Activity card make
   * for this same tree, so a subscriber and not a fetch. Kept current by the `agents` change. */
  const { data: tree } = useQuery({
    queryKey: keys.agentTree(root),
    queryFn: () => fetchAgentTree(root),
  });
  const title = tree?.self?.title || "";
  const order = [root, ...open];

  /* Arrow keys along the strip, the tablist pattern the pane's own strip uses: one Tab stop, the
   * arrows to move, focus following the selection so the next arrow lands on the tab it is about. */
  const onKeys = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const at = Math.max(0, order.indexOf(shown));
    const to =
      event.key === "ArrowRight"
        ? Math.min(at + 1, order.length - 1)
        : event.key === "ArrowLeft"
          ? Math.max(at - 1, 0)
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? order.length - 1
              : null;
    if (to === null || to === at) return;
    event.preventDefault();
    onShow(order[to]);
    requestAnimationFrame(() =>
      strip.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus(),
    );
  };

  return (
    <div
      ref={strip}
      role="tablist"
      aria-label="The conductor and the agents opened here"
      onKeyDown={onKeys}
      className={cn(
        "border-border/60 bg-muted/25 flex h-8 shrink-0 items-center gap-0.5 overflow-x-auto border-b px-1.5",
        "scrollbar-none [&::-webkit-scrollbar]:hidden",
      )}
    >
      <SubTab
        selected={shown === root}
        onSelect={() => onShow(root)}
        tip={`${title ? `${title} — ` : ""}the conductor, the chat you talk to`}
      >
        <MessageSquare className="size-3 shrink-0 opacity-70" aria-hidden />
        <span className="truncate">{title || "Conductor"}</span>
      </SubTab>
      <span aria-hidden className="bg-border mx-1 h-3.5 w-px shrink-0" />
      {open.map((id) => {
        const node = tree ? findAgent(tree.agents, id) : null;
        // Before the tree arrives, or for an agent it no longer lists, the agent's own record —
        // which its pane fetched to show it — is the next best name.
        const name =
          node?.name ||
          cache.getQueryData<ConversationDetail>(keys.conversation(id))?.agentName ||
          "agent";
        const look: AgentLook | null = node ? agentLook(node) : null;
        return (
          <SubTab
            key={id}
            selected={shown === id}
            onSelect={() => onShow(id)}
            onClose={() => onClose(id)}
            name={name}
            tip={look ? `${name} — ${AGENT_WORD[look]}` : name}
          >
            {look ? <AgentDot look={look} /> : <span aria-hidden className="size-1.5 shrink-0" />}
            <span className="truncate">{name}</span>
          </SubTab>
        );
      })}
    </div>
  );
}

/** One item on the strip. A `div` with the tab role rather than a button, so the × inside it can be
 *  a real button — a button in a button is not a thing HTML allows. */
function SubTab({
  selected,
  onSelect,
  onClose,
  name,
  tip,
  children,
}: {
  selected: boolean;
  onSelect: () => void;
  onClose?: () => void;
  /** For the ×'s label; the conductor has no ×. */
  name?: string;
  tip: string;
  children: ReactNode;
}) {
  return (
    <div
      role="tab"
      aria-selected={selected}
      tabIndex={selected ? 0 : -1}
      title={tip}
      onClick={onSelect}
      onKeyDown={(event) => {
        // Only the tab's own keys: Enter on the × inside it must close, not select on the way out.
        if (event.target !== event.currentTarget) return;
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onSelect();
      }}
      className={cn(
        "flex h-6 max-w-[200px] shrink-0 cursor-default items-center gap-1.5 rounded-md text-[12px] transition-colors",
        onClose ? "ps-2 pe-0.5" : "px-2",
        "focus-visible:ring-kith/60 focus-visible:ring-2 focus-visible:outline-none",
        selected
          ? "bg-background text-foreground ring-border shadow-xs ring-1"
          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
      )}
    >
      {children}
      {onClose ? (
        <button
          type="button"
          aria-label={`Close ${name ?? "this agent"}`}
          title="Close — the agent carries on; this only takes it off the strip"
          onClick={(event) => {
            event.stopPropagation();
            onClose();
          }}
          className="text-muted-foreground hover:text-foreground hover:bg-accent flex size-4 shrink-0 cursor-pointer items-center justify-center rounded opacity-60 hover:opacity-100"
        >
          <X className="size-3" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
