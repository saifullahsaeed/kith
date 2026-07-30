import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Check, CircleDot, Lock, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  addDependency,
  fetchRoadmap,
  removeDependency,
  savePositions,
  type Roadmap,
  type RoadmapNode,
} from "@/lib/backend";
import { cn } from "@/lib/utils";

/**
 * A project's roadmap, as the graph it actually is.
 *
 * This is not a diagram of the work — it decides the work. A milestone whose predecessors
 * are unfinished holds its own tasks back, so dragging an edge here changes what he picks up
 * on his next tick. Before dependencies existed the roadmap was a progress bar: it described
 * order after the fact while raw task priority decided it, which is why milestones felt
 * pointless. On his Flappy Bird project, chaining the five milestones took the available
 * work from seven tasks to two — he can no longer package a release before the gameplay
 * exists.
 *
 * A graph rather than a list because the useful question is not "what is next" but "what is
 * available", and that has more than one answer whenever two branches are independent. A
 * list can only lie about that.
 *
 * Nodes carry their state in colour and in words: ready, waiting on something named, or
 * done. "Waiting" without the reason is not information, and it is the thing someone stares
 * at wondering why nothing is happening.
 */
export function RoadmapGraph({
  projectId,
  selected,
  onSelect,
  onChanged,
  onRoadmap,
}: {
  projectId: number;
  /** Which milestone's work is being shown below. Null means "everything available". */
  selected: number | null;
  onSelect: (milestoneId: number | null) => void;
  onChanged?: () => void;
  /** The graph as the server computes it. The page needs the real `ready`/`blocked_by`: its
   *  own first attempt at deriving them marked every unfinished milestone as waiting, which
   *  told you the work you could do was held back. */
  onRoadmap?: (roadmap: Roadmap) => void;
}) {
  return (
    <ReactFlowProvider>
      <Canvas
        projectId={projectId}
        selected={selected}
        onSelect={onSelect}
        onChanged={onChanged}
        onRoadmap={onRoadmap}
      />
    </ReactFlowProvider>
  );
}

/** Laid out by depth when nobody has arranged it: predecessors left, dependents right. */
function layout(milestones: RoadmapNode[]): Map<number, { x: number; y: number }> {
  const byId = new Map(milestones.map((one) => [one.id, one]));
  const depth = new Map<number, number>();

  const depthOf = (id: number, seen: Set<number>): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0; // a cycle cannot be created through the UI, but never hang
    seen.add(id);
    const parents = byId.get(id)?.waits_for ?? [];
    const value = parents.length === 0 ? 0 : Math.max(...parents.map((p) => depthOf(p, seen) + 1));
    depth.set(id, value);
    return value;
  };

  const columns = new Map<number, RoadmapNode[]>();
  for (const one of milestones) {
    const column = depthOf(one.id, new Set());
    columns.set(column, [...(columns.get(column) ?? []), one]);
  }

  const placed = new Map<number, { x: number; y: number }>();
  for (const [column, entries] of columns) {
    entries.forEach((one, index) => {
      placed.set(one.id, { x: column * 300, y: index * 150 - ((entries.length - 1) * 150) / 2 });
    });
  }
  return placed;
}

function Canvas({
  projectId,
  selected,
  onSelect,
  onChanged,
  onRoadmap,
}: {
  projectId: number;
  selected: number | null;
  onSelect: (milestoneId: number | null) => void;
  onChanged?: () => void;
  onRoadmap?: (roadmap: Roadmap) => void;
}) {
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null);
  const [error, setError] = useState("");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const load = useCallback(() => {
    fetchRoadmap(projectId)
      .then(setRoadmap)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [projectId]);

  useEffect(load, [load]);

  useEffect(() => {
    if (roadmap) onRoadmap?.(roadmap);
  }, [roadmap, onRoadmap]);

  // Always re-read, faster while something is in progress. It only polled *while* working
  // before, which meant a milestone he finished on a tick — the exact moment the graph
  // changes shape — never appeared until something else happened to refetch it.
  const working = roadmap?.milestones.some((one) => one.tasks_doing > 0) ?? false;
  useEffect(() => {
    const timer = setInterval(load, working ? 4_000 : 15_000);
    window.addEventListener("focus", load);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", load);
    };
  }, [working, load]);

  // Rebuild the canvas whenever the graph changes. Positions come from the server when
  // someone has arranged them and from the layout when they have not.
  useEffect(() => {
    if (!roadmap) return;
    const auto = layout(roadmap.milestones);
    setNodes(
      roadmap.milestones.map((one) => ({
        id: String(one.id),
        type: "milestone",
        position:
          one.x !== null && one.y !== null
            ? { x: one.x, y: one.y }
            : (auto.get(one.id) ?? { x: 0, y: 0 }),
        data: { ...one, selected: one.id === selected } as unknown as Record<string, unknown>,
      })),
    );
    setEdges(
      roadmap.dependencies.map((edge) => {
        const source = roadmap.milestones.find((one) => one.id === edge.depends_on_id);
        const settled = source?.status === "done";
        return {
          id: `${edge.depends_on_id}->${edge.milestone_id}`,
          source: String(edge.depends_on_id),
          target: String(edge.milestone_id),
          // Animated only while the predecessor is unfinished: a moving edge means "this is
          // what is holding the next one up", so a settled graph is visually quiet.
          animated: !settled,
          style: {
            stroke: settled ? "var(--color-roam)" : "var(--color-kith)",
            strokeWidth: 2,
            opacity: settled ? 0.5 : 0.9,
          },
        };
      }),
    );
  }, [roadmap, selected, setNodes, setEdges]);

  const nodeTypes = useMemo(() => ({ milestone: MilestoneNode }), []);

  const connect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      // Dragged from predecessor to dependent, which is how the arrows read.
      addDependency(projectId, Number(connection.target), Number(connection.source))
        .then((next) => {
          setRoadmap(next);
          onChanged?.();
        })
        .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
    },
    [projectId, onChanged],
  );

  const deleteEdges = useCallback(
    (removed: Edge[]) => {
      Promise.all(
        removed.map((edge) =>
          removeDependency(projectId, Number(edge.target), Number(edge.source)),
        ),
      )
        .then((results) => {
          if (results.length) setRoadmap(results[results.length - 1]);
          onChanged?.();
        })
        .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
    },
    [projectId, onChanged],
  );

  // Positions are saved when a drag ends, not while it is happening: a request per pointer
  // move would be hundreds of writes to say one thing.
  const settle = useCallback(
    (changes: NodeChange<Node>[]) => {
      onNodesChange(changes);
      const dragged = changes.filter(
        (change) => change.type === "position" && change.dragging === false,
      );
      if (!dragged.length) return;
      setNodes((current) => {
        void savePositions(
          projectId,
          current.map((node) => ({ id: Number(node.id), x: node.position.x, y: node.position.y })),
        ).catch(() => {});
        return current;
      });
    },
    [onNodesChange, projectId, setNodes],
  );

  if (error) {
    return (
      <div className="rounded-xl border p-4">
        <p className="text-destructive text-sm">{error}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={load}>
          Try again
        </Button>
      </div>
    );
  }

  if (!roadmap) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 p-4 text-sm">
        <Loader2 className="size-4 animate-spin" /> Reading the roadmap…
      </p>
    );
  }

  if (roadmap.milestones.length === 0) {
    return (
      <div className="rounded-xl border border-dashed p-6 text-center">
        <p className="text-sm">No milestones yet.</p>
        <p className="text-muted-foreground mx-auto mt-1 max-w-sm text-xs leading-relaxed">
          Add a few below and drag between them to say which waits for which. He works the ones that
          are ready and leaves the rest alone — that is what makes a roadmap more than a list.
        </p>
      </div>
    );
  }

  const ready = roadmap.milestones.filter((one) => one.ready);
  const waiting = roadmap.milestones.filter((one) => !one.ready && one.status !== "done");

  return (
    <div className="space-y-2">
      <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
        <span className="flex items-center gap-1.5">
          <CircleDot className="text-kith size-3.5" />
          {ready.length} ready to work
        </span>
        <span className="flex items-center gap-1.5">
          <Lock className="size-3.5" />
          {waiting.length} waiting their turn
        </span>
        <span className="flex items-center gap-1.5">
          <Check className="text-roam size-3.5" />
          {roadmap.milestones.filter((one) => one.status === "done").length} done
        </span>
        <span className="text-muted-foreground/60">
          Drag from one to another to make it wait. Select an edge and press ⌫ to undo it.
        </span>
      </div>

      <div className="h-[26rem] overflow-hidden rounded-xl border">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={settle}
          onEdgesChange={onEdgesChange}
          onConnect={connect}
          onNodeClick={(_event, node) => onSelect(Number(node.id))}
          onPaneClick={() => onSelect(null)}
          onEdgesDelete={deleteEdges}
          fitView
          fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
          proOptions={{ hideAttribution: true }}
          colorMode="dark"
          className="bg-transparent"
        >
          <Background variant={BackgroundVariant.Dots} gap={18} size={1} className="opacity-40" />
          <Controls showInteractive={false} className="!bg-card/80 !border-border/60" />
          {/* Worth its space only once the graph outgrows the frame. */}
          {roadmap.milestones.length > 6 ? (
            <MiniMap pannable className="!bg-card/70" maskColor="rgba(0,0,0,0.4)" />
          ) : null}
        </ReactFlow>
      </div>
    </div>
  );
}

/**
 * One milestone.
 *
 * The state is the point of the card, so it is said three ways — a colour, an icon, and
 * words — because "waiting" with no reason is what someone stares at wondering why nothing
 * is happening. Ready is amber (his colour, meaning he may act), done is green, waiting is
 * muted and names what it waits for.
 */
function MilestoneNode({ data }: NodeProps) {
  const milestone = data as unknown as RoadmapNode & { selected?: boolean };
  const done = milestone.status === "done";
  const waiting = !done && !milestone.ready;
  const here = milestone.tasks_doing > 0;

  return (
    <div
      className={cn(
        "w-56 cursor-pointer rounded-xl border px-3 py-2.5 shadow-sm backdrop-blur transition-all",
        done && "border-roam/40 bg-roam/5",
        milestone.ready && "border-kith/60 bg-kith-soft/40",
        waiting && "border-border/60 bg-card/60 opacity-75",
        // Where he is, right now. The one thing on this canvas that moves on its own.
        here && "ring-kith/50 shadow-kith/20 animate-pulse ring-2 shadow-lg",
        milestone.selected && "ring-ring ring-2",
      )}
    >
      <Handle type="target" position={Position.Left} className="!size-2 !border-0 !bg-kith/70" />
      <Handle type="source" position={Position.Right} className="!size-2 !border-0 !bg-kith/70" />

      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0">
          {done ? (
            <Check className="text-roam size-3.5" />
          ) : milestone.ready ? (
            <CircleDot className="text-kith size-3.5" />
          ) : (
            <Lock className="text-muted-foreground/70 size-3.5" />
          )}
        </span>
        <p
          className={cn(
            "min-w-0 flex-1 text-[13px] leading-snug",
            done && "line-through opacity-70",
          )}
        >
          {milestone.title}
        </p>
      </div>

      <div className="text-muted-foreground/80 mt-1.5 flex items-center gap-2 ps-5 text-[10px] tabular-nums">
        {milestone.tasks_total > 0 ? (
          <span>
            {milestone.tasks_done}/{milestone.tasks_total} tasks
          </span>
        ) : (
          <span className="opacity-60">no tasks yet</span>
        )}
        {milestone.target_at ? <span>· {milestone.target_at.slice(0, 10)}</span> : null}
      </div>

      {here ? (
        <p className="text-kith mt-1 ps-5 text-[10px] leading-snug">working on this now</p>
      ) : null}
      {milestone.tasks_waiting > 0 ? (
        <p className="text-orange-400/90 mt-1 ps-5 text-[10px] leading-snug">
          {milestone.tasks_waiting} waiting on you
        </p>
      ) : null}
      {waiting && milestone.blocked_by.length > 0 ? (
        <p className="text-muted-foreground/70 mt-1 ps-5 text-[10px] leading-snug">
          waits for {milestone.blocked_by.join(", ")}
        </p>
      ) : null}
    </div>
  );
}
