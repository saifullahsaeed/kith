import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  Position,
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
} from "@xyflow/react";

import "@xyflow/react/dist/style.css";
import "./board.css";
import type { KithValue } from "./kith";

/**
 * A flow diagram he can build a node at a time, and you can rearrange.
 *
 * ## What this example is for
 *
 * `sketchpad` next door proves a plugin surface can draw. This one proves the more useful
 * thing: **a surface is an ordinary web page, so any React library works inside it.** React
 * Flow brings dragging, panning, zooming, edge routing, a minimap and keyboard handling that
 * would be several hundred lines of hand-written SVG — and none of it needs the seal widened,
 * because the host inlines the whole bundle into the document before serving it.
 *
 * ## The loop, which is the part worth reading
 *
 * He calls `plugin__flowpad__node`, and the host appends the whole call to `pending` in this
 * plugin's store. The host pushes the store here. This component folds every record newer than
 * `seen` into `nodes`/`edges` and writes the result back — so the *shape of the diagram* is this
 * page's business, and the model only says "add a node called X after Y".
 *
 * Two properties that took bugs to learn, both of them about the fold:
 *
 * * **`pending` is a queue, not a slot.** A single slot loses every call but the last, because
 *   this page only folds when the host pushes — seventeen calls produced one node.
 * * **`seen` lives in the store, not in a variable.** A pane renders only its active tab, so
 *   switching away and back re-runs this module with a fresh `seen` while the queue is still
 *   there. That folded everything twice and duplicated every node.
 */

type Record_ = { seq?: number; command?: string; args?: Record<string, KithValue> };

/** The four shapes a node can be, which is a closed list on purpose: a diagram in nine styles
 *  is a diagram nobody can scan. */
const KINDS: Record<string, string> = {
  step: "flow-step",
  decision: "flow-decision",
  data: "flow-data",
  note: "flow-note",
};

function Board() {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [title, setTitle] = useState("Flowpad");
  /** How far through the queue we have folded. Mirrored from the store on every push. */
  const seen = useRef(0);
  /** What we last wrote, so a push carrying our own write does not start a loop. */
  const mine = useRef("");

  /** Everything back to the store, in one message. `seen` travels with the nodes it accounts
   *  for — the two disagreeing drops records one way and duplicates them the other. */
  const commit = useCallback(
    (next: { nodes: Node[]; edges: Edge[]; title: string; note: string }) => {
      const payload = {
        nodes: next.nodes.map((one) => ({
          id: one.id,
          label: String(one.data?.label ?? ""),
          kind: String((one.data as { kind?: string })?.kind ?? "step"),
          x: Math.round(one.position.x),
          y: Math.round(one.position.y),
        })),
        edges: next.edges.map((one) => ({
          id: one.id,
          from: one.source,
          to: one.target,
          label: typeof one.label === "string" ? one.label : "",
        })),
        title: next.title,
        note: next.note,
        seen: seen.current,
      };
      mine.current = JSON.stringify(payload.nodes) + JSON.stringify(payload.edges);
      window.kith.state.set(payload as unknown as Record<string, KithValue>);
    },
    [],
  );

  useEffect(() => {
    window.kith.render((state) => {
      const heldNodes = Array.isArray(state.nodes) ? (state.nodes as unknown as StoredNode[]) : [];
      const heldEdges = Array.isArray(state.edges) ? (state.edges as unknown as StoredEdge[]) : [];
      let held = heldNodes.map(toNode);
      let wires = heldEdges.map(toEdge);
      let named = typeof state.title === "string" && state.title ? state.title : title;

      // Read the high-water mark back before folding, so a remount picks up where the last one
      // left off. `Math.max` because a push can land while our own write is still in flight.
      if (typeof state.seen === "number") seen.current = Math.max(seen.current, state.seen);

      const queue = Array.isArray(state.pending) ? (state.pending as Record_[]) : [];
      let folded: Record_ | null = null;
      for (const record of queue) {
        if (!record || typeof record !== "object") continue;
        if (Number(record.seq ?? 0) <= seen.current) continue;
        seen.current = Number(record.seq);
        folded = record;
        const args = record.args ?? {};
        if (record.command === "clear") {
          held = [];
          wires = [];
        } else if (record.command === "node") {
          const id = String(args.id ?? `n${held.length + 1}`);
          held = held.filter((one) => one.id !== id).concat(
            toNode({
              id,
              label: String(args.label ?? id),
              kind: String(args.kind ?? "step"),
              /* Laid out left to right, wrapping every fourth node, when he gives no
               * position — which he usually does not.
               *
               * A grid three wide with a sequential chain through it produces long diagonal
               * edges crossing the whole canvas, which reads as spaghetti however correct it
               * is. Four across with a tighter vertical gap keeps each hop short, and a flow
               * described as "left to right" comes out reading left to right. */
              x: Number(args.x ?? 30 + (held.length % 4) * 210),
              y: Number(args.y ?? 30 + Math.floor(held.length / 4) * 105),
            }),
          );
        } else if (record.command === "edge") {
          const from = String(args.from ?? "");
          const to = String(args.to ?? "");
          if (from && to) {
            const id = `${from}->${to}`;
            wires = wires
              .filter((one) => one.id !== id)
              .concat(toEdge({ id, from, to, label: String(args.label ?? "") }));
          }
        } else if (record.command === "set_title") {
          named = String(args.title ?? named);
        }
      }

      setNodes(held);
      setEdges(wires);
      setTitle(named);

      // Only write back when we actually folded something. A push that changed nothing must not
      // produce a write, or the write's own `plugin_state` event pushes again, forever.
      if (folded) {
        commit({
          nodes: held,
          edges: wires,
          title: named,
          note: describe(folded, held.length, wires.length),
        });
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Dragging a node is the half he cannot watch happen, so it writes straight back. */
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes((current) => {
        const next = applyNodeChanges(changes, current);
        // Only when a drag *finishes*: committing on every pointer move would be a write per
        // frame, and each one would invalidate the query that pushes the next.
        if (changes.some((one) => one.type === "position" && one.dragging === false)) {
          commit({ nodes: next, edges, title, note: "moved a node" });
        }
        return next;
      });
    },
    [commit, edges, title],
  );

  const counts = useMemo(
    () => `${nodes.length} node${nodes.length === 1 ? "" : "s"} · ${edges.length} edge${edges.length === 1 ? "" : "s"}`,
    [nodes.length, edges.length],
  );

  return (
    <div className="flow-shell">
      <header className="flow-chrome">
        <span className="flow-title">{title}</span>
        <span className="flow-count">{nodes.length ? counts : ""}</span>
        <button
          type="button"
          className="flow-clear"
          onClick={() => {
            setNodes([]);
            setEdges([]);
            commit({ nodes: [], edges: [], title, note: "cleared" });
          }}
        >
          Clear
        </button>
      </header>

      {nodes.length === 0 ? (
        <p className="flow-empty">Nothing on it yet. Ask him to draw a flow.</p>
      ) : (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          fitView
          // Room around the graph, so the outermost nodes are not flush against the pane edge
          // and the zoom controls do not sit on top of one.
          fitViewOptions={{ padding: 0.18, maxZoom: 1 }}
          proOptions={{ hideAttribution: true }}
          // No `colorMode` prop: the host repaints `--kith-*` on a theme change without
          // remounting, and `board.css` maps React Flow's own variables onto those — so the
          // graph follows the app's theme with nothing to keep in step.
          minZoom={0.2}
          maxZoom={2}
        >
          <Background gap={20} size={1} />
          <Controls showInteractive={false} />
          {/* Only once the graph is big enough to get lost in. On eight nodes it is chrome
              covering content, which is what it was doing at thirteen before it was styled. */}
          {nodes.length > 14 ? <MiniMap pannable zoomable /> : null}
        </ReactFlow>
      )}
    </div>
  );
}

interface StoredNode {
  id: string;
  label: string;
  kind: string;
  x: number;
  y: number;
}
interface StoredEdge {
  id: string;
  from: string;
  to: string;
  label: string;
}

function toNode(held: StoredNode): Node {
  return {
    id: held.id,
    position: { x: Number(held.x) || 0, y: Number(held.y) || 0 },
    data: { label: held.label, kind: held.kind },
    className: KINDS[held.kind] ?? KINDS.step,
    /* Handles on the sides, because the layout runs left to right.
     *
     * React Flow defaults to top and bottom, so every edge in a horizontal chain left its
     * source downwards, ran along under the row and came back up into the next node — a little
     * rounded loop under each box that reads as a mistake. Sides make each hop a straight
     * line. The one edge that wraps to the next row still loops, which is honest: that is a
     * step backwards across the diagram and it should look like one. */
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
  };
}

function toEdge(held: StoredEdge): Edge {
  return {
    id: held.id,
    source: held.from,
    target: held.to,
    label: held.label || undefined,
    animated: false,
    /* Orthogonal rather than the default bezier. A flow chart is read as a sequence of steps,
     * and a bezier between two boxes in a grid sweeps across everything between them; a
     * smoothstep hugs the gap and stays legible when a dozen of them overlap. */
    type: "smoothstep",
  };
}

/** One short phrase for the digest, so the line he gets says what happened rather than a count. */
function describe(record: Record_, nodes: number, edges: number): string {
  const args = record.args ?? {};
  if (record.command === "clear") return "cleared";
  if (record.command === "node") return `added ${String(args.label ?? args.id ?? "a node")}`;
  if (record.command === "edge") return `linked ${String(args.from)} → ${String(args.to)}`;
  if (record.command === "set_title") return "renamed it";
  return `${nodes} nodes, ${edges} edges`;
}

createRoot(document.getElementById("root")!).render(<Board />);
