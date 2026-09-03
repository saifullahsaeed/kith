import { Fragment, type ReactNode, useCallback, useState } from "react";
import { X } from "lucide-react";
import { Group, Panel, Separator, type Layout } from "react-resizable-panels";

import { cn } from "@/lib/utils";

import { carriesTab, edgeAt, highlightFor, TAB_MIME } from "./drag";
import { useLayout } from "./store";
import { MIN_HEIGHT, SURFACES, paneMinWidth, tabTitle } from "./surfaces";
import { tabKey, type Edge, type Node, type PaneNode, type TabRef } from "./tree";

/**
 * The layout tree, drawn.
 *
 * Deliberately knows nothing about what a surface *is*. `render` is handed a tab and gives back
 * an element, so this module never imports the Work panel or the Thread — which keeps their
 * dependencies out of the entry chunk, keeps the tree testable without mounting the app, and
 * keeps every prop those components need where it already lives, in `Workspace`.
 */
export function LayoutView({
  render,
  titleFor,
}: {
  render: (ref: TabRef) => ReactNode;
  /** A chat's title. Everything else is named by the surface registry. */
  titleFor?: (ref: TabRef) => string | undefined;
}) {
  const tree = useLayout((s) => s.tree);
  return <NodeView node={tree} render={render} titleFor={titleFor} />;
}

function NodeView({
  node,
  render,
  titleFor,
}: {
  node: Node;
  render: (ref: TabRef) => ReactNode;
  titleFor?: (ref: TabRef) => string | undefined;
}) {
  const resize = useLayout((s) => s.resize);

  if (node.kind === "pane") {
    return <PaneView pane={node} render={render} titleFor={titleFor} />;
  }

  const stacked = node.direction === "column";
  const onLayoutChanged = (layout: Layout, meta: { isUserInteraction: boolean }) => {
    // Only a real drag is worth remembering. The library also reports mounts, constraint
    // recomputes and default-size changes through here, and persisting those would rewrite
    // the stored layout on every window resize with numbers nobody chose.
    if (!meta.isUserInteraction) return;
    resize(
      node.id,
      node.children.map((child) => layout[child.id] ?? 0),
    );
  };

  return (
    <Group
      orientation={stacked ? "vertical" : "horizontal"}
      className={cn("flex min-h-0 min-w-0", stacked ? "flex-col" : "flex-row")}
      defaultLayout={Object.fromEntries(
        node.children.map((child, index) => [child.id, node.sizes[index] ?? 0]),
      )}
      onLayoutChanged={onLayoutChanged}
    >
      {node.children.map((child, index) => (
        <Fragment key={child.id}>
          {index > 0 ? (
            <Separator
              className={cn(
                "group relative shrink-0",
                stacked ? "h-px w-full cursor-row-resize" : "w-px cursor-col-resize",
              )}
            >
              {/* A one-pixel line to look at, a wider band to hit. Anything less than about
                  six pixels is a divider you chase with the mouse. */}
              <span className="bg-border/60 group-hover:bg-kith/60 group-data-[state=drag]:bg-kith absolute inset-0 transition-colors" />
              <span
                className={cn(
                  "absolute",
                  stacked ? "-top-1 -bottom-1 inset-x-0" : "-left-1 -right-1 inset-y-0",
                )}
              />
            </Separator>
          ) : null}
          <Panel
            id={child.id}
            minSize={minFor(child, stacked)}
            className="flex min-h-0 min-w-0 flex-col"
          >
            <NodeView node={child} render={render} titleFor={titleFor} />
          </Panel>
        </Fragment>
      ))}
    </Group>
  );
}

/** The narrowest (or shortest) this node may be, in pixels.
 *
 * A split's minimum is its children's, because a row of two panes cannot be narrower than both
 * of them — without this a nested split collapses past its own contents and the innermost pane
 * ends up at 40px with a scrollbar. */
function minFor(node: Node, stacked: boolean): number {
  if (stacked) return MIN_HEIGHT;
  if (node.kind === "pane") return paneMinWidth(node.tabs);
  if (node.direction === "row") {
    return node.children.reduce((sum, child) => sum + minFor(child, false), 0);
  }
  return Math.max(...node.children.map((child) => minFor(child, false)));
}

function PaneView({
  pane,
  render,
  titleFor,
}: {
  pane: PaneNode;
  render: (ref: TabRef) => ReactNode;
  titleFor?: (ref: TabRef) => string | undefined;
}) {
  /* One selector each, rather than one returning an object.
   *
   * zustand v5 compares a selector's result by identity, so a selector building `{ dock,
   * activate, ... }` returns a new object on every store change and re-renders for ever. The
   * actions are stable references, so selecting them one at a time is both correct and free. */
  const dock = useLayout((s) => s.dock);
  const activate = useLayout((s) => s.activate);
  const close = useLayout((s) => s.close);
  const focus = useLayout((s) => s.focus);
  const focused = useLayout((s) => s.focused === pane.id);
  const [over, setOver] = useState<Edge | null>(null);
  const active = pane.tabs[pane.active];

  const onDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!carriesTab(event.dataTransfer)) return;
    // Both are required, and for different reasons: without `preventDefault` the browser
    // refuses the drop, and without stopping propagation a nested pane's parent lights up too.
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "move";
    const rect = event.currentTarget.getBoundingClientRect();
    setOver(edgeAt(rect, event.clientX, event.clientY));
  }, []);

  return (
    <div
      data-pane={pane.id}
      onMouseDownCapture={() => focus(pane.id)}
      onDragOver={onDragOver}
      onDragLeave={(event) => {
        // `dragleave` fires when the pointer crosses into a *child*, so a naive clear makes the
        // highlight strobe as you move over the tab strip. Only a leave that actually exits
        // this element counts.
        if (event.currentTarget.contains(event.relatedTarget as globalThis.Node | null)) return;
        setOver(null);
      }}
      onDrop={(event) => {
        if (!carriesTab(event.dataTransfer)) return;
        event.preventDefault();
        event.stopPropagation();
        const key = event.dataTransfer.getData(TAB_MIME);
        const rect = event.currentTarget.getBoundingClientRect();
        setOver(null);
        if (key) dock(key, pane.id, edgeAt(rect, event.clientX, event.clientY));
      }}
      className={cn(
        "bg-background relative flex min-h-0 min-w-0 flex-1 flex-col",
        focused && "ring-kith/20 ring-1 ring-inset",
      )}
    >
      <div className="border-border/60 flex shrink-0 items-center gap-px overflow-x-auto border-b">
        {pane.tabs.map((tab, index) => (
          <TabButton
            key={tabKey(tab)}
            tab={tab}
            title={tabTitle(tab, titleFor?.(tab))}
            active={index === pane.active}
            onSelect={() => activate(pane.id, index)}
            onClose={() => close(tabKey(tab))}
          />
        ))}
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        {active ? render(active) : <EmptyPane />}
      </div>

      {over ? (
        <div
          aria-hidden
          style={highlightFor(over)}
          className="bg-kith/15 border-kith/60 pointer-events-none absolute z-30 rounded-sm border-2 transition-all duration-75"
        />
      ) : null}
    </div>
  );
}

function TabButton({
  tab,
  title,
  active,
  onSelect,
  onClose,
}: {
  tab: TabRef;
  title: string;
  active: boolean;
  onSelect: () => void;
  onClose: () => void;
}) {
  const Icon = SURFACES[tab.surface].icon;
  return (
    <div
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData(TAB_MIME, tabKey(tab));
        event.dataTransfer.effectAllowed = "move";
      }}
      onClick={onSelect}
      className={cn(
        "group flex max-w-[220px] shrink-0 cursor-default items-center gap-1.5 px-3 py-1.5 text-xs transition-colors",
        active
          ? "bg-background text-foreground border-kith border-b-2"
          : "text-muted-foreground hover:text-foreground border-b-2 border-transparent",
      )}
    >
      <Icon className="size-3.5 shrink-0 opacity-70" />
      <span className="truncate">{title}</span>
      <button
        type="button"
        aria-label={`Close ${title}`}
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
        className="hover:bg-muted -mr-1 rounded p-0.5 opacity-0 transition-opacity group-hover:opacity-60 hover:opacity-100"
      >
        <X className="size-3" />
      </button>
    </div>
  );
}

/** What the last closed tab leaves behind.
 *
 * A pane rather than a blank window, because something has to be on screen and "nothing" is
 * indistinguishable from a crash. */
function EmptyPane() {
  return (
    <div className="text-muted-foreground flex h-full items-center justify-center text-xs">
      Nothing open here — drag a tab in, or open something from the header.
    </div>
  );
}
