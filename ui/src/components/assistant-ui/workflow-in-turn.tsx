"use client";

/**
 * The workflow a turn started, as a card in the run strip — outside the fold, the same colour and
 * the same name as its live copy in the Activity panel.
 *
 * A `run_workflow` call — or an `add_agents` step — is the one step in a turn whose result is not
 * in the turn: the run happens
 * after the turn ends, in the background, and comes back as a new turn when it finishes. So its
 * row in the rail ("Started a workflow") says almost nothing, and it was the most consequential
 * thing the turn did. The card says what was started, how far it has got, and where to watch it —
 * with the script one click away, because the script *is* the plan, and reading it is how you
 * check he split the work the way you would have.
 *
 * Live state comes from the same board the Work panel reads (`useRuns`), so the two cannot
 * disagree. When the board no longer knows the run — it was long ago, or the server restarted —
 * the card says only what the call itself recorded: the name, the phases, the script.
 */

import { useAuiState } from "@assistant-ui/react";
import { ArrowRight, Code2 } from "lucide-react";
import { useMemo, useState } from "react";

import { useLayout } from "@/components/shell/layout/store";
import { useRuns, type Run } from "@/components/chat/workflows";
import { useActivity } from "@/hooks/use-activity";
import { useConversationId } from "@/lib/conversation";
import { formatClock, formatWorked } from "@/lib/turn-plan";
import { cn } from "@/lib/utils";
import { useTicking } from "@/lib/use-ticking";

import { Spin } from "./spin";

interface Started {
  run: string;
  name: string;
  phases: string[];
  script: string;
  /** Which step of the run an `add_agents` call sent, and the labels of its agents. */
  step: number;
  agents: string[];
}

/** The workflows these calls started, read off the calls themselves. */
export function useStartedWorkflows(calls: readonly number[]): Started[] {
  const key = calls.join(",");
  const encoded = useAuiState((s) => {
    const out: Started[] = [];
    for (const index of key ? key.split(",").map(Number) : []) {
      const part = s.message.parts[index];
      if (part?.type !== "tool-call" || part.result === undefined) continue;
      if (part.toolName !== "run_workflow" && part.toolName !== "add_agents") continue;
      const wrapped = part.result as { ok?: boolean; result?: Record<string, unknown> } | null;
      const value = (wrapped && typeof wrapped === "object" && "result" in wrapped ? wrapped.result : wrapped) as
        | Record<string, unknown>
        | null;
      if (!value || typeof value.run !== "string") continue;
      const script = (part.args as { script?: unknown } | undefined)?.script;
      out.push({
        run: value.run,
        name: typeof value.name === "string" ? value.name : "workflow",
        phases: Array.isArray(value.phases) ? value.phases.map(String) : [],
        script: typeof script === "string" ? script : "",
        step: typeof value.step === "number" ? value.step : 0,
        agents: Array.isArray(value.agents) ? value.agents.map(String) : [],
      });
    }
    return out.length ? JSON.stringify(out) : "";
  });
  return useMemo(() => (encoded ? (JSON.parse(encoded) as Started[]) : []), [encoded]);
}

/** One run's live record from the board, or undefined when the board does not have it. */
export function useWorkflowRun(runId: string): Run | undefined {
  const conversation = useConversationId();
  const { activity } = useActivity();
  const runs = useRuns(conversation, activity);
  return runs.find((one) => one.id === runId);
}

export function WorkflowCards({ calls }: { calls: readonly number[] }) {
  const started = useStartedWorkflows(calls);
  if (!started.length) return null;
  return (
    <>
      {started.map((one) => (
        <WorkflowCard key={`${one.run}-${one.step}`} started={one} />
      ))}
    </>
  );
}


function WorkflowCard({ started }: { started: Started }) {
  const run = useWorkflowRun(started.run);
  const open = useLayout((state) => state.open);
  const [reading, setReading] = useState(false);
  const running = run?.state === "running";
  const now = useTicking(running);

  const phases = run?.phases.length ? run.phases : started.phases;
  const current = run ? phases.indexOf(run.phase) : -1;
  const lines = started.script ? started.script.split("\n").length : 0;

  const clock = running && run?.startedAt ? formatClock(now - run.startedAt) : "";
  const took =
    run && !running && run.endedAt && run.startedAt ? formatWorked(run.endedAt - run.startedAt) : "";
  const description = started.agents.length
    ? started.agents.join(", ")
    : run?.description || (phases.length ? phases.join(" → ") : "");
  const footnote = running
    ? "Patches come back to this chat when it finishes"
    : run?.state === "finished"
      ? `Finished${took ? ` after ${took}` : ""} — the result came back in a new turn`
      : run?.state === "failed"
        ? `Failed${took ? ` after ${took}` : ""}`
        : run?.state === "stopped"
          ? "Stopped"
          : "";

  return (
    <div
      data-slot="kith_workflow-in-turn"
      className="mx-1 mt-1.5 flex flex-col gap-2.5 rounded-xl border border-teal-500/35 bg-teal-400/[0.06] px-3.5 py-3"
    >
      <div className="flex items-center gap-2.5">
        <span className="flex size-[26px] shrink-0 items-center justify-center rounded-lg bg-teal-400/15 text-teal-700 dark:text-teal-400">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            aria-hidden
            className="size-[15px]"
          >
            <circle cx="12" cy="12" r="2" />
            <path d="M16.24 7.76a6 6 0 1 0 .01 8.48M19.07 4.93a10 10 0 1 0 .01 14.14" />
          </svg>
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-px">
          <span className="text-foreground text-[13px] font-semibold">
            {started.step > 1 ? `Step ${started.step} of ` : "Started workflow "}
            <span className="font-mono text-teal-700 dark:text-teal-400">{started.name}</span>
          </span>
          {description ? (
            <span className="text-muted-foreground truncate text-xs">{description}</span>
          ) : null}
        </span>
        {clock ? (
          <span className="flex shrink-0 items-center gap-1.5 text-xs text-teal-700 tabular-nums dark:text-teal-400">
            <Spin tone="teal" className="size-3" />
            {clock}
          </span>
        ) : null}
      </div>

      {phases.length ? (
        <div
          className="grid gap-1.5"
          style={{ gridTemplateColumns: `repeat(${phases.length}, minmax(0, 1fr))` }}
        >
          {phases.map((phase, index) => {
            const done = run ? (run.state === "finished" ? true : current > index) : false;
            const now = running && index === current;
            const agents = run ? run.agents.filter((agent) => agent.phase === phase).length : 0;
            return (
              <div key={`${phase}-${index}`} className="flex min-w-0 flex-col gap-[5px]">
                <span
                  className={cn(
                    "h-1 rounded-full",
                    now ? "bg-teal-500 dark:bg-teal-400" : done ? "bg-teal-500/45 dark:bg-teal-400/45" : "bg-foreground/12",
                  )}
                />
                <span
                  className={cn(
                    "flex gap-[5px] truncate text-[11px]",
                    now ? "text-teal-700 dark:text-teal-300" : "text-muted-foreground",
                  )}
                >
                  <span className="truncate">{phase}</span>
                  {agents ? <span className="text-muted-foreground/75">{agents}</span> : null}
                </span>
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => open({ surface: "work" })}
          className="flex h-7 items-center gap-1.5 rounded-lg bg-teal-400/15 px-2.5 text-xs font-semibold text-teal-800 transition-colors hover:bg-teal-400/25 dark:text-teal-300"
        >
          Watch in Activity
          <ArrowRight className="size-3" strokeWidth={2.2} aria-hidden />
        </button>
        {started.script ? (
          <button
            type="button"
            aria-expanded={reading}
            onClick={() => setReading((was) => !was)}
            className="border-foreground/12 text-foreground/75 hover:text-foreground flex h-7 items-center gap-1.5 rounded-lg border px-2.5 text-xs transition-colors"
          >
            <Code2 className="size-3" aria-hidden />
            Script · {lines} {lines === 1 ? "line" : "lines"}
          </button>
        ) : null}
        <span className="flex-1" />
        {footnote ? <span className="text-muted-foreground/75 text-[11px]">{footnote}</span> : null}
      </div>

      {reading ? (
        <pre className="border-border bg-background/60 text-foreground/85 max-h-80 overflow-auto rounded-lg border px-3 py-2 font-mono text-[11.5px] leading-[1.55]">
          {started.script}
        </pre>
      ) : null}
    </div>
  );
}
