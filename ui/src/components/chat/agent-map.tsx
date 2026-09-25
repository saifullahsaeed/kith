import { Check, CircleSlash, Loader2, Network, Square, X } from "lucide-react";
import { useState } from "react";

import { useRuns, type Agent, type Run } from "@/components/chat/workflows";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { useActivity } from "@/hooks/use-activity";
import { useNow } from "@/hooks/use-now";
import { useAgentMap } from "@/lib/agent-map";
import { stopWorkflow } from "@/lib/commands";
import { describeCall } from "@/lib/tool-language";
import type { ActivityItem } from "@/lib/backend/activity";

/**
 * The agent map: a workflow drawn as what it is — a run, its phases, and the agents in each —
 * with the one you click opened up beside it.
 *
 * The Work panel already lists a run's agents, and that is the right place to glance at. This is
 * the place to *understand* one: which phase each agent ran in, which ones built on which, how
 * long each took and what it cost, and — the thing no list can show without becoming a wall —
 * the full prompt it was given and what it said back. A workflow runs after the turn that started
 * it has ended and its agents' work reaches the thread nowhere, so without this the only record of
 * why a builder came back empty is a line in the note that wakes him.
 *
 * Opened from the chip on the composer (drawn by `AgentsChip` below), the Work panel, or the card
 * a `run_workflow` call leaves in the thread. All three go through `useAgentMap`.
 */

/** How long something took, in the shortest form that is still true. */
function took(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60)
    return `${minutes}m${String(seconds % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}m`;
}

function money(usd: number): string {
  return usd >= 0.01 ? `$${usd.toFixed(2)}` : usd > 0 ? "<$0.01" : "";
}

type AgentState = "running" | "done" | "failed";

function stateOf(agent: Agent): AgentState {
  if (agent.running) return "running";
  return agent.failed ? "failed" : "done";
}

function Dot({ state }: { state: AgentState | Run["state"] }) {
  const tone =
    state === "running"
      ? "bg-kith animate-pulse"
      : state === "failed"
        ? "bg-red-400"
        : state === "stopped"
          ? "bg-muted-foreground/50"
          : "bg-emerald-400";
  return (
    <span
      className={`inline-block size-2 shrink-0 rounded-full ${tone}`}
      aria-hidden
    />
  );
}

/** The phases as columns, in the order the script declared them. An agent that started before
 *  any phase, or in one the plan did not list, still gets a column rather than vanishing. */
function columns(run: Run): { phase: string; agents: Agent[] }[] {
  const order = [...run.phases];
  for (const agent of run.agents) {
    const phase = agent.phase || "—";
    if (!order.includes(phase)) order.push(phase);
  }
  return order
    .map((phase) => ({
      phase,
      agents: run.agents.filter((agent) => (agent.phase || "—") === phase),
    }))
    .filter(
      (column, i) =>
        column.agents.length || run.phases.includes(column.phase) || i === 0,
    );
}

function AgentNode({
  agent,
  now,
  selected,
  onSelect,
}: {
  agent: Agent;
  now: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const state = stateOf(agent);
  const elapsed = (agent.endedAt || now) - agent.startedAt;
  const builder = agent.role === "builder";
  return (
    // The tick joining this node to its column's bracket, drawn by the `before:` pseudo-element.
    <li className="before:bg-border relative before:absolute before:top-1/2 before:-left-3 before:h-px before:w-3">
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className={`bg-card flex w-56 cursor-pointer flex-col gap-0.5 rounded-lg border px-3 py-2 text-left transition-colors ${
          selected
            ? "border-kith/70 ring-kith/25 ring-2"
            : "border-border hover:border-muted-foreground/40"
        }`}
      >
        <span className="flex min-w-0 items-center gap-2">
          <Dot state={state} />
          <span
            className="min-w-0 flex-1 truncate text-[12.5px] font-medium"
            title={agent.objective}
          >
            {agent.objective}
          </span>
        </span>
        <span className="text-muted-foreground flex items-center gap-1.5 pl-4 text-[10.5px] whitespace-nowrap tabular-nums">
          {builder ? (
            <span className="font-medium text-amber-600 dark:text-amber-400/90">builder</span>
          ) : (
            <span>scout</span>
          )}
          <span>·</span>
          <span>{took(elapsed)}</span>
          {agent.calls || agent.steps.length ? (
            <>
              <span>·</span>
              <span>{agent.calls || agent.steps.length} calls</span>
            </>
          ) : null}
          {agent.cost ? (
            <>
              <span>·</span>
              <span>{money(agent.cost)}</span>
            </>
          ) : null}
        </span>
      </button>
    </li>
  );
}

function RunNode({ run, now }: { run: Run; now: number }) {
  const elapsed = (run.endedAt || now) - run.startedAt;
  const running = run.agents.filter((agent) => agent.running).length;
  return (
    <div className="bg-card border-border flex w-60 shrink-0 flex-col gap-1 rounded-lg border px-3 py-2.5">
      <span className="flex items-center gap-2">
        <Dot state={run.state} />
        <span
          className="min-w-0 flex-1 truncate text-[13px] font-semibold"
          title={run.description || run.name}
        >
          {run.name}
        </span>
      </span>
      <span className="text-muted-foreground pl-4 text-[10.5px] tabular-nums">
        {run.agents.length} agent{run.agents.length === 1 ? "" : "s"}
        {running ? ` · ${running} running` : ""} · {took(elapsed)}
        {run.cost ? ` · ${money(run.cost)}` : ""}
      </span>
      {run.description ? (
        <span className="text-muted-foreground/80 line-clamp-3 pl-4 text-[10.5px] leading-snug">
          {run.description}
        </span>
      ) : null}
    </div>
  );
}

function Details({ agent, now }: { agent: Agent; now: number }) {
  const [promptOpen, setPromptOpen] = useState(false);
  const state = stateOf(agent);
  const elapsed = (agent.endedAt || now) - agent.startedAt;
  return (
    <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
      <div className="flex items-start gap-2">
        <Dot state={state} />
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium break-words">
            {agent.objective}
          </div>
          <div className="text-muted-foreground text-[11px] tabular-nums">
            {agent.role === "builder" ? "builder" : "scout"} · {state}
            {agent.phase ? ` · ${agent.phase}` : ""} · {took(elapsed)}
            {agent.calls ? ` · ${agent.calls} calls` : ""}
            {agent.cost ? ` · ${money(agent.cost)}` : ""}
          </div>
        </div>
      </div>

      {agent.after.length ? (
        <p className="text-muted-foreground text-[11px]">
          Started on top of{" "}
          <span className="text-foreground">{agent.after.join(", ")}</span>.
        </p>
      ) : null}

      {agent.running && agent.steps.length ? (
        <section className="flex flex-col gap-1">
          <h4 className="text-muted-foreground text-[10px] font-medium tracking-wide uppercase">
            Now
          </h4>
          <ul className="flex flex-col gap-0.5">
            {agent.steps.slice(-8).map((step, i) => {
              const call = describeCall(step.tool, step.args);
              return (
                <li
                  key={`${agent.id}-step-${agent.steps.length - i}`}
                  className="text-muted-foreground truncate text-[11px]"
                >
                  {call.verb}
                  {call.subject ? (
                    <span className="font-mono"> {call.subject}</span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {agent.outcome || agent.patch ? (
        <section className="flex flex-col gap-1">
          <h4 className="text-muted-foreground text-[10px] font-medium tracking-wide uppercase">
            Patch
          </h4>
          <p className="font-mono text-[11px] break-all">
            {agent.patch || agent.outcome}
          </p>
        </section>
      ) : null}

      {agent.report ? (
        <section className="flex flex-col gap-1">
          <h4 className="text-muted-foreground text-[10px] font-medium tracking-wide uppercase">
            What it said
          </h4>
          <p className="bg-muted/30 max-h-60 overflow-y-auto rounded-md p-2 text-[11.5px] leading-relaxed whitespace-pre-wrap">
            {agent.report}
          </p>
        </section>
      ) : !agent.running && agent.tally ? (
        <p className="text-muted-foreground font-mono text-[11px]">
          {agent.tally}
        </p>
      ) : null}

      {agent.prompt ? (
        <section className="flex flex-col gap-1">
          <button
            type="button"
            onClick={() => setPromptOpen((was) => !was)}
            aria-expanded={promptOpen}
            className="text-muted-foreground hover:text-foreground w-fit cursor-pointer text-[10px] font-medium tracking-wide uppercase"
          >
            {promptOpen ? "Hide the prompt" : "The prompt it was given"}
          </button>
          {promptOpen ? (
            <pre className="bg-muted/30 max-h-72 overflow-auto rounded-md p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
              {agent.prompt}
            </pre>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

export function AgentMapDialog({
  lines,
  conversationId,
}: {
  lines: ActivityItem[];
  conversationId: string;
}) {
  const { conversation, run: chosen, choose, close } = useAgentMap();
  const open = Boolean(conversation) && conversation === conversationId;
  const runs = useRuns(conversationId, lines);
  // Newest first for the tabs; the one asked for, or the latest.
  const ordered = [...runs].reverse();
  const run = ordered.find((one) => one.id === chosen) ?? ordered[0];
  const [selected, setSelected] = useState("");
  // Ticks while anything is running, so durations count up; idle otherwise.
  const now = useNow(run?.state === "running" ? 1000 : 60_000);
  const [stopping, setStopping] = useState("");
  const agent = run?.agents.find((one) => one.id === selected);

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? null : close())}>
      <DialogContent className="flex max-h-[82vh] w-[min(1120px,calc(100%-2rem))] flex-col gap-3 sm:max-w-none">
        <div className="flex flex-col gap-0.5 pr-8">
          <DialogTitle className="flex items-center gap-2 text-base">
            <Network className="text-kith size-4" aria-hidden />
            Agent map
          </DialogTitle>
          <DialogDescription className="text-[12px]">
            {run
              ? `${run.agents.length} agent${run.agents.length === 1 ? "" : "s"} · click an agent for details`
              : "No workflow has run in this conversation yet. Start one with /workflow."}
          </DialogDescription>
        </div>

        {ordered.length > 1 ? (
          <div
            className="flex flex-wrap gap-1"
            role="tablist"
            aria-label="Workflow runs"
          >
            {ordered.map((one) => (
              <button
                key={one.id}
                type="button"
                role="tab"
                aria-selected={one.id === run?.id}
                onClick={() => {
                  choose(one.id);
                  setSelected("");
                }}
                className={`flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-[11px] ${
                  one.id === run?.id
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Dot state={one.state} />
                {one.name}
              </button>
            ))}
          </div>
        ) : null}

        {run ? (
          <>
            <div
              className={`grid min-h-0 flex-1 gap-4 ${agent ? "grid-cols-[minmax(0,1fr)_320px]" : "grid-cols-1"}`}
            >
              {/* The map. Scrolls sideways when a plan has more phases than fit. */}
              <div className="min-h-0 overflow-auto pb-2">
                {/* One horizontal axis: the run, then each phase's column centred on it, joined by a
                    short line — so the connectors meet the middle of each bracket, the way a tree
                    is drawn, rather than whatever height the tallest column happens to reach. */}
                <div className="flex w-max items-center">
                  <RunNode run={run} now={now} />
                  {columns(run).map((column) => (
                    <div key={column.phase} className="flex items-center">
                      <span
                        className="bg-border h-px w-5 shrink-0"
                        aria-hidden
                      />
                      <section className="flex flex-col gap-2">
                        <h3
                          className={`pl-3 text-[10.5px] font-medium tracking-wide uppercase ${
                            run.state === "running" &&
                            run.phase === column.phase
                              ? "text-kith"
                              : "text-muted-foreground"
                          }`}
                        >
                          {column.phase}
                        </h3>
                        {column.agents.length ? (
                          // The bracket every node in this column hangs from.
                          <ul className="before:bg-border relative flex flex-col gap-2 pl-3 before:absolute before:top-4 before:bottom-4 before:left-0 before:w-px">
                            {column.agents.map((one) => (
                              <AgentNode
                                key={one.id}
                                agent={one}
                                now={now}
                                selected={one.id === selected}
                                onSelect={() =>
                                  setSelected((was) =>
                                    was === one.id ? "" : one.id,
                                  )
                                }
                              />
                            ))}
                          </ul>
                        ) : (
                          <p className="text-muted-foreground/60 w-32 pl-3 text-[11px]">
                            {run.state === "running"
                              ? "not started"
                              : "nothing ran"}
                          </p>
                        )}
                      </section>
                    </div>
                  ))}
                </div>
              </div>
              {agent ? (
                <aside className="border-border flex min-h-0 flex-col border-l pl-4">
                  <Details agent={agent} now={now} />
                </aside>
              ) : null}
            </div>

            <div className="border-border flex items-center gap-3 border-t pt-3">
              <span className="flex min-w-0 flex-1 items-center gap-2 text-[11.5px]">
                {run.state === "running" ? (
                  <Loader2
                    className="text-kith size-3.5 shrink-0 animate-spin"
                    aria-hidden
                  />
                ) : run.state === "finished" ? (
                  <Check
                    className="size-3.5 shrink-0 text-emerald-400"
                    aria-hidden
                  />
                ) : run.state === "stopped" ? (
                  <CircleSlash
                    className="text-muted-foreground size-3.5 shrink-0"
                    aria-hidden
                  />
                ) : (
                  <X className="size-3.5 shrink-0 text-red-400" aria-hidden />
                )}
                <span
                  className="text-muted-foreground truncate"
                  title={run.said}
                >
                  {run.said ||
                    (run.state === "running"
                      ? `running${run.phase ? ` · ${run.phase}` : ""}`
                      : run.state)}
                </span>
              </span>
              {run.state === "running" ? (
                <button
                  type="button"
                  disabled={stopping === run.id}
                  onClick={() => {
                    setStopping(run.id);
                    void stopWorkflow(run.id);
                  }}
                  className="text-muted-foreground hover:text-foreground flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] ring-1 ring-border disabled:cursor-default disabled:opacity-50"
                  title="Stop this workflow and its agents. He is still told what finished."
                >
                  <Square className="size-3" aria-hidden />
                  {stopping === run.id ? "stopping" : "Stop workflow"}
                </button>
              ) : null}
            </div>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

/**
 * The running-agents indicator on the composer, and the door to the map.
 *
 * Only there when this conversation has a workflow: pulsing with the count of agents working
 * while one runs, and a quiet check or cross once it has ended — so the map stays one click away
 * after the fact, which is when "why did that builder come back empty" gets asked.
 */
export function AgentsChip({ conversationId }: { conversationId: string }) {
  const { activity } = useActivity();
  const show = useAgentMap((s) => s.show);
  const runs = useRuns(conversationId, activity);
  const latest = runs[runs.length - 1];
  if (!conversationId || !latest) return null;
  const working = latest.agents.filter((agent) => agent.running).length;
  const running = latest.state === "running";

  return (
    <>
      <button
        type="button"
        onClick={() => show(conversationId, latest.id)}
        className={`flex h-7 cursor-pointer items-center gap-1.5 rounded-full px-2.5 text-[11px] ring-1 transition-colors ${
          running
            ? "bg-kith-soft/40 text-kith ring-kith/30 hover:bg-kith-soft/60"
            : "text-muted-foreground ring-border hover:text-foreground"
        }`}
        title={`${latest.name} — open the agent map`}
        aria-label={`Agent map: ${latest.name}, ${latest.state}`}
      >
        <Dot state={running ? "running" : latest.state} />
        {running
          ? working
            ? `${working} agent${working === 1 ? "" : "s"}${latest.phase ? ` · ${latest.phase}` : ""}`
            : latest.phase || "starting"
          : `workflow ${latest.state}`}
      </button>
      <AgentMapDialog lines={activity} conversationId={conversationId} />
    </>
  );
}
