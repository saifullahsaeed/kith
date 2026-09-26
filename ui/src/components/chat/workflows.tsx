import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleSlash, Network, Square, X, XCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { useNow } from "@/hooks/use-now";
import { useAgentMap } from "@/lib/agent-map";
import { stopWorkflow } from "@/lib/commands";
import { useProjectRoot } from "@/lib/project-root";
import { callText, describeCall } from "@/lib/tool-language";
import type { ActivityItem } from "@/lib/backend/activity";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

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
  /** When its opening and closing lines arrived, in epoch milliseconds. */
  startedAt: number;
  endedAt: number;
  prompt: string;
  phase: string;
  after: string[];
  cost: number;
  calls: number;
  report: string;
  patch: string;
  outcome: string;
  failed: boolean;
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
  startedAt: number;
  endedAt: number;
  cost: number;
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
    const at = Date.parse(line.at) || 0;
    const found: Agent = byId.get(mark.id) ?? {
      id: mark.id,
      objective: mark.objective,
      steps: [],
      running: true,
      role: mark.role ?? "scout",
      tally: "",
      run: mark.run ?? "",
      startedAt: at,
      endedAt: 0,
      prompt: "",
      phase: "",
      after: [],
      cost: 0,
      calls: 0,
      report: "",
      patch: "",
      outcome: "",
      failed: false,
    };
    if (mark.state === "running") {
      found.startedAt = at;
      found.prompt = mark.prompt ?? found.prompt;
      found.phase = mark.phase ?? found.phase;
      found.after = mark.after ?? found.after;
    }
    if (mark.state === "step" && line.tool) {
      found.steps.push({ tool: line.tool, args: line.args ?? {} });
    }
    if (mark.state === "done") {
      found.running = false;
      found.tally = line.text;
      found.endedAt = at;
      found.cost = mark.cost ?? 0;
      found.calls = mark.calls ?? found.steps.length;
      found.report = mark.report ?? "";
      found.patch = mark.patch ?? "";
      found.outcome = mark.outcome ?? "";
      found.failed = Boolean(mark.failed);
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
    const at = Date.parse(line.at) || 0;
    const found: Run = byId.get(mark.run) ?? {
      id: mark.run,
      name: mark.name ?? "workflow",
      description: "",
      phases: [],
      phase: "",
      state: "running" as const,
      said: "",
      agents: [],
      startedAt: at,
      endedAt: 0,
      cost: 0,
    };
    if (mark.state === "started") {
      found.startedAt = at;
      found.description = mark.description ?? "";
      found.phases = mark.phases ?? [];
    } else if (mark.state === "phase") {
      found.phase = line.text;
    } else if (mark.state === "log") {
      found.said = line.text;
    } else if (mark.state === "finished" || mark.state === "failed" || mark.state === "stopped") {
      found.state = mark.state;
      found.said = line.text;
      found.endedAt = at;
      found.cost = mark.cost ?? 0;
    }
    byId.set(mark.run, found);
  }
  for (const agent of foldAgents(lines, conversationId)) {
    byId.get(agent.run)?.agents.push(agent);
  }
  return [...byId.values()];
}

/** A run as the server's board keeps it — see `services/workflows.Run.public`. */
export interface BoardRun {
  run: string;
  name: string;
  description?: string;
  phases?: string[];
  phase?: string;
  started?: number;
  ended?: number;
  state: Run["state"];
  said?: string;
  cost?: number;
  agents?: {
    id: string;
    label?: string;
    kind?: string;
    phase?: string;
    state?: string;
    prompt?: string;
    report?: string;
    patch?: string;
    outcome?: string;
    tally?: string;
    cost?: number;
    calls?: number;
    failed?: boolean;
    after?: string[];
    started?: number;
    ended?: number;
    steps?: { tool: string; args: Record<string, string> }[];
  }[];
}

/** The board's runs in the shape the panel and the map draw. Exported for its test. */
export function runsFromBoard(board: BoardRun[]): Run[] {
  return [...board]
    .sort((a, b) => (a.started ?? 0) - (b.started ?? 0))
    .map((one) => ({
      id: one.run,
      name: one.name,
      description: one.description ?? "",
      phases: one.phases ?? [],
      phase: one.phase ?? "",
      state: one.state,
      said: one.said ?? "",
      startedAt: (one.started ?? 0) * 1000,
      endedAt: (one.ended ?? 0) * 1000,
      cost: one.cost ?? 0,
      agents: (one.agents ?? []).map((agent) => ({
        id: agent.id,
        objective: agent.label ?? agent.id,
        steps: agent.steps ?? [],
        running: agent.state !== "done",
        role: agent.kind ?? "scout",
        tally: agent.tally ?? "",
        run: one.run,
        startedAt: (agent.started ?? 0) * 1000,
        endedAt: (agent.ended ?? 0) * 1000,
        prompt: agent.prompt ?? "",
        phase: agent.phase ?? "",
        after: agent.after ?? [],
        cost: agent.cost ?? 0,
        calls: agent.calls ?? 0,
        report: agent.report ?? "",
        patch: agent.patch ?? "",
        outcome: agent.outcome ?? "",
        failed: Boolean(agent.failed),
      })),
    }));
}

/** The board's account of each run, with the feed's live steps laid over it.
 *
 *  The board is the record — it survives the feed's ring rolling over, which a busy conversation
 *  does in minutes — and the feed is the heartbeat, carrying every step as it happens. A run only
 *  the feed knows (the board has not been re-read yet) is kept too, so a run that just started
 *  appears at once. Exported for its test. */
export function mergeRuns(board: Run[], live: Run[]): Run[] {
  const known = new Set(board.map((one) => one.id));
  const merged = board.map((run) => {
    const fresh = live.find((one) => one.id === run.id);
    if (!fresh) return run;
    const agents = run.agents.map((agent) => {
      const seen = fresh.agents.find((one) => one.id === agent.id);
      return seen && seen.steps.length > agent.steps.length ? { ...agent, steps: seen.steps } : agent;
    });
    // An agent the feed has seen start and the board has not been re-read for yet.
    for (const agent of fresh.agents) if (!agents.some((one) => one.id === agent.id)) agents.push(agent);
    return { ...run, agents };
  });
  return [...merged, ...live.filter((one) => !known.has(one.id))].sort((a, b) => a.startedAt - b.startedAt);
}

/** The server's board of runs for one conversation.
 *
 *  Its own function because two readers share the cache entry: the section below, and the tab
 *  row above the companion column, which lights Activity while a run is going. Two fetchers
 *  under one key would be two opinions about the same answer. */
export async function fetchRunBoard(conversationId: string): Promise<BoardRun[]> {
  const response = await fetch(`/api/workflows?conversation=${encodeURIComponent(conversationId)}`);
  if (!response.ok) return [];
  const body = (await response.json()) as { runs?: BoardRun[] } | null;
  return body?.runs ?? [];
}

/** Every workflow run in this conversation, merged from the board and the live feed. */
export function useRuns(conversationId: string, lines: ActivityItem[]): Run[] {
  const cache = useQueryClient();
  const live = foldRuns(lines, conversationId);
  // A run or an agent changing state is what makes the board worth re-reading; a step is not.
  const pulse = lines.filter(
    (line) =>
      line.conversation === conversationId &&
      (line.kind === "workflow" || (line.kind === "errand" && line.errand?.state !== "step")),
  ).length;
  const { data } = useQuery({
    queryKey: keys.workflows(conversationId),
    queryFn: () => fetchRunBoard(conversationId),
    enabled: Boolean(conversationId),
    placeholderData: keepPreviousData,
  });
  useEffect(() => {
    if (conversationId && pulse) void cache.invalidateQueries({ queryKey: keys.workflows(conversationId) });
  }, [cache, conversationId, pulse]);
  return mergeRuns(runsFromBoard(data ?? []), live);
}

/* ── The card ───────────────────────────────────────────────────────────────────────────────
 *
 * One card per run, laid out the way the companion's Activity tab draws it: the run's own line
 * (name, id, clock, Stop), where it is in its plan, the agents of the phase it is in, what is
 * still ahead, and how close it is to each of its limits. Teal throughout, because teal is what
 * a workflow is everywhere else — the card in the thread that started it, the chip on the tab.
 */

/** The server's limits for one run — `AT_ONCE`, `MAX_AGENTS` and `MAX_SECONDS` in
 *  `server/kith/tools/workflow.py`. Mirrored by hand: the board does not carry them, and this
 *  footer, which says how close a run is to each, is their only reader. */
const AT_ONCE = 6;
const MAX_AGENTS = 30;
const MAX_MINUTES = 60;

const TEAL = "text-teal-600 dark:text-teal-400";

/** `1:52`, `12:05`, `1:02:05` — a stopwatch, because that is what a running thing is timed by. */
export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** The small ring the design uses for "going": a spinner that reads as a status, not a load. */
function Ring({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "size-3 shrink-0 animate-spin rounded-full border-2 border-teal-600/25 border-t-teal-600 dark:border-teal-400/25 dark:border-t-teal-400",
        className,
      )}
    />
  );
}

/** What kind of agent it is, in words. A builder is editing a copy of your repository and a
 *  scout is not, and that difference must survive someone who cannot tell blue from amber. */
function Kind({ role, chip = true, quiet = false }: { role: string; chip?: boolean; quiet?: boolean }) {
  const builder = role === "builder";
  return (
    <span
      className={cn(
        "shrink-0 text-[10px] font-bold uppercase tracking-[0.06em]",
        chip && "rounded-[5px] px-1.5 py-0.5",
        quiet
          ? "text-muted-foreground/80"
          : builder
            ? cn("text-kith", chip && "bg-kith-soft")
            : cn("text-blue-600 dark:text-blue-300", chip && "bg-blue-500/14 dark:bg-blue-400/14"),
      )}
    >
      {builder ? "builder" : "scout"}
    </span>
  );
}

/** A section's small-caps heading, the same one every card in the column uses. */
function Label({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "px-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/80",
        className,
      )}
    >
      {children}
    </div>
  );
}

const SWEEP = "@keyframes kith-sweep{from{background-position:-120px 0}to{background-position:calc(100% + 120px) 0}}";

/** The thin light that runs under a working agent. Its keyframes ride with it — React hoists and
 *  dedupes a `<style>` with an `href` — so the card does not depend on a stylesheet elsewhere. */
function Sweep() {
  return (
    <>
      <style href="kith-sweep" precedence="default">
        {SWEEP}
      </style>
      <div
        aria-hidden
        className={cn(
          "h-0.5 rounded-full opacity-70 animate-[kith-sweep_1.6s_linear_infinite]",
          // Longhands, not `background:` — the shorthand resets the size, and the 120px light
          // became a smear the width of the card.
          "bg-no-repeat [background-image:linear-gradient(90deg,transparent,currentColor,transparent)] [background-size:120px_2px]",
          TEAL,
        )}
      />
    </>
  );
}

/** How far one agent has got: `14 steps · 1:52`. */
function agentMeta(agent: Agent, now: number): string {
  const count = Math.max(agent.calls, agent.steps.length);
  const until = agent.running ? now : agent.endedAt || now;
  const took = agent.startedAt ? clock(until - agent.startedAt) : "";
  return [`${count} step${count === 1 ? "" : "s"}`, took].filter(Boolean).join(" · ");
}

/** A running agent: what it is, what it was asked, and its last few steps as it takes them. */
function AgentCard({ agent, now }: { agent: Agent; now: number }) {
  const [open, setOpen] = useState(false);
  const root = useProjectRoot();
  /* The brief is the prompt. A label defaults to the prompt's first sixty characters, so a feed
   * line that carried no prompt still has its brief in the label when the label is long. */
  const brief = agent.prompt.trim() || (agent.objective.length > 60 ? agent.objective : "");
  const recent = agent.steps.slice(-RECENT_STEPS);
  const first = agent.steps.length - recent.length;

  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-[10px] border border-foreground/8 bg-muted/40 px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <Kind role={agent.role} />
        <span className="min-w-0 truncate text-[13px] font-semibold" title={agent.objective}>
          {agent.objective}
        </span>
        <span className="flex-1" />
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/80">
          {agentMeta(agent, now)}
        </span>
      </div>

      {brief ? (
        /* Folded to two lines and opened on click. A good builder brief says exactly what to
           change, in which files and what done looks like — hundreds of words, and two of them
           whole filled the column and pushed their own steps off it. */
        <button
          type="button"
          onClick={() => setOpen((was) => !was)}
          aria-expanded={open}
          title={open ? "Fold" : "Show the whole brief"}
          className={cn(
            "cursor-pointer text-left text-[12px] leading-[1.45] text-muted-foreground",
            open ? "whitespace-pre-wrap" : FOLDED_LINES,
          )}
        >
          {brief}
        </button>
      ) : null}

      {recent.length ? (
        <ul className="flex flex-col gap-px text-[12px]">
          {recent.map((step, i) => {
            // The newest step is the one it is on: present tense and lit, the rest quiet.
            const live = i === recent.length - 1;
            const call = describeCall(step.tool, step.args, root);
            return (
              <li
                key={`${agent.id}-${first + i}`}
                className={cn("flex h-[22px] min-w-0 items-center gap-2", live ? "text-foreground" : "text-foreground/75")}
                title={callText(call)}
              >
                <span
                  aria-hidden
                  className={cn(
                    "size-[5px] shrink-0 rounded-full",
                    live ? "bg-teal-500 dark:bg-teal-400" : "bg-foreground/25",
                  )}
                />
                <span className="shrink-0 text-muted-foreground/80">{capital(live ? call.doing : call.verb)}</span>
                {call.subject ? (
                  <code className="min-w-0 truncate font-mono text-[11.5px]">{call.subject}</code>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      <Sweep />
    </div>
  );
}

/** A finished agent: one quiet line with its tally, kept because "it looked at eleven things" is
 *  the context for the result you are about to read. */
function DoneRow({ agent, now }: { agent: Agent; now: number }) {
  if (agent.failed || /^failed\b/i.test(agent.tally)) return <FailedRow agent={agent} now={now} />;
  return (
    <div
      className="flex min-w-0 items-center gap-2 rounded-lg px-2.5 py-1.5 text-[12px]"
      title={agent.report || agent.objective}
    >
      <Check className="size-3 shrink-0 text-muted-foreground/60" aria-hidden />
      <Kind role={agent.role} chip={false} quiet />
      <span className="min-w-0 shrink truncate text-muted-foreground">{agent.objective}</span>
      {agent.tally ? (
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground/70">{agent.tally}</span>
      ) : (
        <span className="flex-1" />
      )}
      <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/70">{agentMeta(agent, now)}</span>
    </div>
  );
}

/** An agent that failed, with the reason — red, because failed is the one done state that asks
 *  for a look. The run carries on without it; `parallel()` hands the script back `None`. */
function FailedRow({ agent, now }: { agent: Agent; now: number }) {
  const reason = agent.tally.replace(/^failed:\s*/i, "").trim() || agent.outcome || "it stopped without a report";
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-[10px] border border-destructive/30 bg-destructive/8 px-3 py-2">
      <div className="flex min-w-0 items-center gap-2 text-[12px]">
        <XCircle className="size-3.5 shrink-0 text-destructive" aria-label="Failed" />
        <Kind role={agent.role} chip={false} />
        <span className="min-w-0 truncate text-foreground/85">{agent.objective}</span>
        <span className="flex-1" />
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/80">{agentMeta(agent, now)}</span>
      </div>
      <p className="line-clamp-2 text-[12px] leading-[1.45] text-destructive" title={reason}>
        failed: {reason}
      </p>
    </div>
  );
}

/** How a run ended — the only place the card says so, since the word beside it went to make room
 *  for the map button. Named for a screen reader and on hover. */
function EndIcon({ state }: { state: Run["state"] }) {
  const icon =
    state === "finished" ? (
      <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden />
    ) : state === "stopped" ? (
      <CircleSlash className="size-3.5 text-muted-foreground" aria-hidden />
    ) : (
      <X className="size-3.5 text-destructive" aria-hidden />
    );
  return (
    <span role="img" aria-label={state} title={state} className="flex shrink-0">
      {icon}
    </span>
  );
}

/** Where in its own plan it is: the phases it declared, the current one lit, with how many agents
 *  each has sent. A count is only shown once there is one — the server learns of an agent when it
 *  starts, so a phase still ahead has no honest number yet. */
function Phases({ run }: { run: Run }) {
  const running = run.state === "running";
  const reached = run.phases.indexOf(run.phase);
  return (
    <ol className="flex min-w-0 items-center">
      {run.phases.map((phase, i) => {
        const here = running && i === reached;
        const past = reached >= 0 && i < reached;
        const sent = run.agents.filter((one) => one.phase === phase).length;
        return (
          <li key={`${run.id}-${phase}`} className="flex min-w-0 flex-1 items-center">
            <span
              className={cn(
                "flex h-6 shrink-0 items-center gap-1.5 rounded-full border px-[9px] text-[11px] font-semibold",
                here
                  ? "border-teal-600/40 bg-teal-500/14 text-teal-700 dark:border-teal-400/40 dark:bg-teal-400/14 dark:text-teal-300"
                  : "border-foreground/12 text-muted-foreground",
              )}
            >
              {past ? <Check className="size-2.5 text-muted-foreground/80" aria-label="done" /> : null}
              {phase}
              {sent ? <span className="font-normal text-muted-foreground/80">{sent}</span> : null}
            </span>
            {i < run.phases.length - 1 ? <span aria-hidden className="mx-1 h-px min-w-2 flex-1 bg-foreground/12" /> : null}
          </li>
        );
      })}
    </ol>
  );
}

function RunCard({ run, conversationId }: { run: Run; conversationId: string }) {
  const [stopping, setStopping] = useState(false);
  const show = useAgentMap((s) => s.show);
  const running = run.state === "running";
  // A stopwatch while it runs; once it has ended nothing on the card moves.
  const now = useNow(running ? 1000 : 60_000);
  const elapsed = run.startedAt ? (running ? now : run.endedAt || now) - run.startedAt : 0;

  /* Which agents belong to the phase it is in. With no phase announced — a script that declared
   * none, or one that has not reached its first — they all do. An agent that started before any
   * phase and is still going stays in view rather than vanishing into "earlier". */
  const reached = run.phases.indexOf(run.phase);
  const inPhase = (one: Agent) => !run.phase || one.phase === run.phase || (one.running && !one.phase);
  const current = run.agents.filter(inPhase);
  const earlier = run.agents.filter((one) => !inPhase(one));
  const ahead = running && reached >= 0 ? run.phases.slice(reached + 1) : [];
  const going = run.agents.filter((one) => one.running).length;
  const left = Math.max(0, Math.ceil(MAX_MINUTES - elapsed / 60_000));

  return (
    <section
      aria-label={`Workflow ${run.name}`}
      className={cn(
        "flex shrink-0 flex-col rounded-[14px] border bg-card",
        running ? "border-teal-600/35 dark:border-teal-400/35" : "border-border",
      )}
    >
      <div className="flex flex-col gap-2.5 px-3.5 pt-3 pb-2.5">
        <div className="flex min-w-0 items-center gap-2">
          {running ? <Ring /> : <EndIcon state={run.state} />}
          {/* The name opens the agent map — the whole run drawn as a graph, every agent's brief,
              report and patch. It used to be a separate "map" link in a header this card no
              longer has. */}
          <button
            type="button"
            onClick={() => show(conversationId, run.id)}
            title={run.description ? `${run.description} — open the agent map` : "Open the agent map"}
            className="min-w-0 cursor-pointer truncate font-mono text-[14px] font-[650] hover:underline"
          >
            {run.name}
          </button>
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground/80">{run.id}</span>
          <span className="flex-1" />
          {elapsed ? (
            <span className={cn("shrink-0 text-[12px] tabular-nums", running ? TEAL : "text-muted-foreground/80")}>
              {clock(elapsed)}
            </span>
          ) : null}
          {/* The map, said as a button. The name above opens it too, but a hover underline is
              not how anyone finds a door. */}
          <button
            type="button"
            onClick={() => show(conversationId, run.id)}
            title="Open the agent map — the run as a graph, every agent's brief, report and patch"
            className="flex h-[26px] shrink-0 cursor-pointer items-center gap-[5px] rounded-[7px] border border-foreground/14 px-[9px] text-[12px] text-foreground/85 transition-colors hover:bg-accent"
          >
            <Network className="size-3" aria-hidden />
            Agent map
          </button>
          {running ? (
            <button
              type="button"
              disabled={stopping}
              onClick={() => {
                setStopping(true);
                void stopWorkflow(run.id);
              }}
              title="Stop this workflow and its agents. He is still told what finished."
              className="flex h-[26px] shrink-0 cursor-pointer items-center gap-[5px] rounded-[7px] border border-foreground/14 px-[9px] text-[12px] text-foreground/85 transition-colors hover:bg-accent disabled:cursor-default disabled:opacity-50"
            >
              <Square className="size-2.5 fill-current" aria-hidden />
              {stopping ? "Stopping" : "Stop"}
            </button>
          ) : null}
        </div>

        {run.phases.length ? <Phases run={run} /> : null}

        {run.said ? (
          <p className="line-clamp-2 text-[12px] leading-[1.45] text-muted-foreground" title={run.said}>
            {run.said}
          </p>
        ) : null}
      </div>

      {run.agents.length || ahead.length ? (
        <div className="flex flex-col gap-2 border-t border-foreground/7 px-2.5 pt-2.5 pb-1.5">
          {current.length ? (
            <>
              <Label>
                {run.phase || "Agents"} · {running ? (going ? "running" : "waiting") : run.state}
              </Label>
              {/* One column in the companion, several once the tab is opened full width. */}
              <div className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(min(300px,100%),1fr))]">
                {current.map((agent) =>
                  agent.running ? (
                    <AgentCard key={agent.id} agent={agent} now={now} />
                  ) : (
                    <DoneRow key={agent.id} agent={agent} now={now} />
                  ),
                )}
              </div>
            </>
          ) : null}

          {ahead.length ? (
            <>
              <Label className="pt-1">Up next</Label>
              {/* The phases still ahead, not named agents: the server learns of an agent only
                  when the script sends it, so what is known about the rest of the plan is its
                  phases and their order. */}
              <div className="flex flex-col gap-1 pb-1">
                {ahead.map((phase, i) => (
                  <div
                    key={`${run.id}-next-${phase}`}
                    className="flex h-[30px] min-w-0 items-center gap-2 rounded-lg border border-dashed border-foreground/12 px-2.5 text-[12px] text-muted-foreground"
                  >
                    <span className="shrink-0 text-[10px] font-bold uppercase tracking-[0.06em] text-muted-foreground/80">
                      phase {reached + i + 2}
                    </span>
                    <span className="min-w-0 truncate text-foreground/85">{phase}</span>
                    <span className="flex-1" />
                    <span className="shrink-0 text-[11px] text-muted-foreground/80">
                      after {run.phases[reached + i]}
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : null}

          {earlier.length ? (
            <>
              <Label className="pt-1">Earlier</Label>
              <div className="flex flex-col gap-0.5 pb-1">
                {earlier.map((agent) => (
                  <DoneRow key={agent.id} agent={agent} now={now} />
                ))}
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      {running ? (
        /* How close it is to each of the three things that end a run early. */
        <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-foreground/7 px-3.5 py-2 text-[11px] tabular-nums text-muted-foreground/80">
          <span>
            {going} of {AT_ONCE} running
          </span>
          <span>
            {run.agents.length} of {MAX_AGENTS} agents
          </span>
          {run.startedAt ? <span>{left} min left</span> : null}
        </div>
      ) : null}
    </section>
  );
}

/**
 * The runs, as cards. The running one first — while it runs it *is* what is happening, the
 * turn that started it having ended — and the finished ones after it, newest first.
 *
 * No header of its own: each card names its run, and a "Workflows" heading over a single card
 * was a title for a title.
 */
export function Workflows({
  lines,
  conversationId = "",
}: {
  lines: ActivityItem[];
  conversationId?: string;
}) {
  const runs = useRuns(conversationId, lines);
  // Nothing to say beats a header over an empty list — the same rule the background section follows.
  if (!runs.length) return null;
  const ordered = [
    ...runs.filter((one) => one.state === "running"),
    ...runs.filter((one) => one.state !== "running").reverse(),
  ];
  return (
    <>
      {ordered.map((run) => (
        <RunCard key={run.id} run={run} conversationId={conversationId} />
      ))}
    </>
  );
}

/** A workflow's own line, for the panel's round counter to skip. An agent's tool calls are not the
 *  turn's rounds, and counting them made one fan-out read as ninety steps. */
export function isWorkflowLine(item: ActivityItem): boolean {
  return item.kind === "errand" || item.kind === "workflow";
}
