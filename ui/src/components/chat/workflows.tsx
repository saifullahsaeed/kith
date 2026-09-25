import { Check, CircleSlash, Loader2, Square, X } from "lucide-react";
import { useState } from "react";

import { stopWorkflow } from "@/lib/commands";
import { describeCall } from "@/lib/tool-language";
import type { ActivityItem } from "@/lib/backend/activity";

/**
 * The workflows running for this conversation, and the agents inside each one.
 *
 * The panel this sits in used to be a round-by-round list of every tool call, and that was
 * deleted on purpose: it was the same list the thread renders, four hundred pixels from where
 * you read it. This is the exception the deletion leaves room for, and the test of it is
 * whether the information exists anywhere else. For a workflow it does not — the run happens
 * after the turn that started it has ended, and its agents' greps and reads are discarded by
 * design and never reach the thread. Without this, a fan-out of six builders and nothing at all
 * look identical until the result arrives.
 *
 * One header per run — name, the phase it is in, and Stop, which is the only way to end a run
 * from outside the conversation — with its agents under it. A running agent is open with its
 * last few steps; a finished one collapses to one dim line with its tally, kept because "it
 * looked at eleven things" is the context for the result you are about to read.
 *
 * Folded from the live feed, scoped to this conversation, same as everything else in the column.
 */

/** How many steps of one running agent to show. Enough to see it moving; not so many that a
 *  thorough scout pushes the rest of the panel off the screen. */
const RECENT_STEPS = 5;

/** A running agent's prompt is folded to two lines. A good builder prompt says exactly what to
 *  change, in which files, and what done looks like — hundreds of words — and rendered whole in a
 *  320px column two of them filled the screen and pushed their own steps off the bottom. Folded
 *  rather than truncated, because the text is worth reading: click opens it. */
const FOLDED_LINES = "line-clamp-2";

export interface Agent {
  id: string;
  /** The label the script gave it, or the start of its prompt. */
  objective: string;
  steps: { tool: string; args: Record<string, string> }[];
  running: boolean;
  /** `scout` or `builder`. Decides the tint and the label — see `AgentHead`. */
  role: string;
  /** `grep x3, read_file x8`, or how it failed — what the closing line carried. */
  tally: string;
  /** Which run sent it. */
  run: string;
}

export interface Run {
  id: string;
  name: string;
  description: string;
  phases: string[];
  phase: string;
  state: "running" | "finished" | "failed" | "stopped";
  /** The last `log()` line while running; the first line of the closing note once it ends. */
  said: string;
  agents: Agent[];
}

/** The feed's agent lines, folded into one entry per agent, oldest first.
 *
 * Exported for its test. A reducer over a flat event log is the kind of thing that looks
 * obviously right and silently drops the last step, and the panel is the worst place to find
 * that out. */
export function foldAgents(lines: ActivityItem[], conversationId: string): Agent[] {
  const byId = new Map<string, Agent>();
  for (const line of lines) {
    const mark = line.errand;
    if (!mark) continue;
    // A line with no conversation is about the machine, not about one piece of work.
    if (conversationId && line.conversation !== conversationId) continue;
    const found = byId.get(mark.id) ?? {
      id: mark.id,
      objective: mark.objective,
      steps: [],
      running: true,
      role: mark.role ?? "scout",
      tally: "",
      run: mark.run ?? "",
    };
    if (mark.state === "step" && line.tool) {
      found.steps.push({ tool: line.tool, args: line.args ?? {} });
    }
    if (mark.state === "done") {
      found.running = false;
      found.tally = line.text;
    }
    byId.set(mark.id, found);
  }
  return [...byId.values()];
}

/** The feed folded into runs, each with its agents, oldest first. Exported for its test. */
export function foldRuns(lines: ActivityItem[], conversationId: string): Run[] {
  const byId = new Map<string, Run>();
  for (const line of lines) {
    const mark = line.workflow;
    if (!mark) continue;
    if (conversationId && line.conversation !== conversationId) continue;
    const found = byId.get(mark.run) ?? {
      id: mark.run,
      name: mark.name ?? "workflow",
      description: "",
      phases: [],
      phase: "",
      state: "running" as const,
      said: "",
      agents: [],
    };
    if (mark.state === "started") {
      found.description = mark.description ?? "";
      found.phases = mark.phases ?? [];
    } else if (mark.state === "phase") {
      found.phase = line.text;
    } else if (mark.state === "log") {
      found.said = line.text;
    } else if (mark.state === "finished" || mark.state === "failed" || mark.state === "stopped") {
      found.state = mark.state;
      found.said = line.text;
    }
    byId.set(mark.run, found);
  }
  for (const agent of foldAgents(lines, conversationId)) {
    byId.get(agent.run)?.agents.push(agent);
  }
  return [...byId.values()];
}

/** One agent's first line: what it is, what kind it is, and whether it is still going.
 *
 *  Its own component only because it holds state. A single `open` id on the list would collapse
 *  whichever one you were reading the moment you opened another. */
function AgentHead({ agent }: { agent: Agent }) {
  const [open, setOpen] = useState(false);
  const builder = agent.role === "builder";

  return (
    <div className="flex items-start gap-2 text-[11px]">
      {agent.running ? (
        <Loader2
          className={`mt-0.5 size-3 shrink-0 animate-spin ${builder ? "text-amber-400/80" : "text-teal-400/80"}`}
          aria-hidden
        />
      ) : (
        <Check className="text-muted-foreground/50 mt-0.5 size-3 shrink-0" aria-hidden />
      )}
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        title={open ? "Fold" : agent.objective}
        aria-expanded={open}
        className={`min-w-0 flex-1 cursor-pointer text-left leading-relaxed ${
          agent.running ? "" : "text-muted-foreground/70"
        } ${open ? "whitespace-pre-wrap" : agent.running ? FOLDED_LINES : "truncate"}`}
      >
        {/* Named, not only coloured. A builder is editing a copy of your repository and a scout
            is not, and that difference must survive someone who cannot tell teal from amber. */}
        {builder ? <span className="mr-1 font-medium text-amber-400/80">builder</span> : null}
        {agent.objective}
      </button>
    </div>
  );
}

function RunIcon({ state }: { state: Run["state"] }) {
  if (state === "running") return <Loader2 className="text-kith/80 size-3.5 shrink-0 animate-spin" aria-hidden />;
  if (state === "finished") return <Check className="size-3.5 shrink-0 text-emerald-400/80" aria-hidden />;
  if (state === "stopped") return <CircleSlash className="text-muted-foreground/70 size-3.5 shrink-0" aria-hidden />;
  return <X className="size-3.5 shrink-0 text-red-400/80" aria-hidden />;
}

function RunCard({ run }: { run: Run }) {
  const [stopping, setStopping] = useState(false);
  const running = run.state === "running";
  const reached = run.phases.indexOf(run.phase);

  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <RunIcon state={run.state} />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium" title={run.description || run.name}>
          {run.name}
        </span>
        {running ? (
          <button
            type="button"
            disabled={stopping}
            onClick={() => {
              setStopping(true);
              void stopWorkflow(run.id);
            }}
            className="text-muted-foreground hover:text-foreground flex cursor-pointer items-center gap-1 rounded px-1.5 py-0.5 text-[10px] ring-1 ring-border/60 disabled:cursor-default disabled:opacity-50"
            title="Stop this workflow and its agents. He is still told what finished."
          >
            <Square className="size-2.5" aria-hidden />
            {stopping ? "stopping" : "stop"}
          </button>
        ) : (
          <span className="text-muted-foreground/60 text-[10px]">{run.state}</span>
        )}
      </div>

      {run.phases.length ? (
        // Where in its own plan it is. Phases before the current one are done, the current one is
        // lit, the rest are still ahead — which is the one thing a list of agents cannot say.
        <ol className="ml-[1.375rem] flex flex-wrap gap-1">
          {run.phases.map((phase, i) => (
            <li
              key={`${run.id}-${phase}`}
              className={`rounded px-1.5 py-px text-[10px] ${
                running && i === reached
                  ? "bg-kith-soft/60 text-kith"
                  : i < reached || !running
                    ? "text-muted-foreground/60"
                    : "text-muted-foreground/40"
              }`}
            >
              {phase}
            </li>
          ))}
        </ol>
      ) : null}

      {run.agents.length ? (
        <ul className="ml-[1.375rem] flex flex-col gap-1.5">
          {run.agents.map((agent) => (
            <li key={agent.id} className="flex flex-col gap-1">
              <AgentHead agent={agent} />
              {agent.running ? (
                <ul className="border-border/40 ml-[1.375rem] flex flex-col gap-0.5 border-l pl-2">
                  {agent.steps.slice(-RECENT_STEPS).map((step, i) => {
                    const call = describeCall(step.tool, step.args);
                    return (
                      <li
                        key={`${agent.id}-${agent.steps.length - Math.min(agent.steps.length, RECENT_STEPS) + i}`}
                        className="text-muted-foreground/60 truncate text-[10px]"
                        title={`${call.verb} ${call.subject}`}
                      >
                        {call.verb}
                        {call.subject ? <span className="font-mono"> {call.subject}</span> : null}
                      </li>
                    );
                  })}
                </ul>
              ) : agent.tally ? (
                <span className="text-muted-foreground/50 ml-[1.375rem] truncate font-mono text-[10px]">
                  {agent.tally}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {run.said ? (
        <p className="text-muted-foreground/70 ml-[1.375rem] line-clamp-2 text-[10px] leading-relaxed" title={run.said}>
          {run.said}
        </p>
      ) : null}
    </li>
  );
}

export function Workflows({
  lines,
  conversationId = "",
}: {
  lines: ActivityItem[];
  conversationId?: string;
}) {
  const runs = foldRuns(lines, conversationId);
  // Nothing to say beats a header over an empty list — the same rule the background section follows.
  if (!runs.length) return null;
  const running = runs.filter((one) => one.state === "running").length;

  return (
    <div className="border-border/60 flex flex-col gap-2 border-b px-4 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium">Workflows</span>
        <span className="text-muted-foreground/60 font-mono text-[10px] tabular-nums">
          {running ? `${running} running` : runs.length}
        </span>
      </div>
      <ul className="flex flex-col gap-3">
        {runs.map((run) => (
          <RunCard key={run.id} run={run} />
        ))}
      </ul>
    </div>
  );
}

/** A workflow's own line, for the panel's round counter to skip. An agent's tool calls are not the
 *  turn's rounds, and counting them made one fan-out read as ninety steps. */
export function isWorkflowLine(item: ActivityItem): boolean {
  return item.kind === "errand" || item.kind === "workflow";
}
