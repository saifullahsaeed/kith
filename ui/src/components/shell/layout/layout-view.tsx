import { Fragment, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { SplitSquareHorizontal, SplitSquareVertical, X, XCircle } from "lucide-react";
import { Group, Panel, Separator, type Layout } from "react-resizable-panels";

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { cn } from "@/lib/utils";

import { carriesTab, edgeAt, highlightFor, TAB_MIME } from "./drag";
import { useLayout } from "./store";
import { MIN_HEIGHT, SURFACES, paneMinWidth, tabTitle } from "./surfaces";
import { panes as panesOf, tabKey, type Edge, type Node, type PaneNode, type TabRef } from "./tree";

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
  const order = useLayout((s) => s.order);
  const focus = useLayout((s) => s.focus);
  const box = useRef<HTMLDivElement | null>(null);
  const [room, setRoom] = useState(0);

  /* How much room this group actually has.
   *
   * Measured, because the minimums cannot be enforced without it — see `railed`. A
   * `ResizeObserver` rather than a window listener: a nested group's width changes when a
   * *sibling divider* moves, which no window event reports. */
  // Read before the early return below, because hooks cannot sit after one — hence reading
  // the direction defensively rather than off the narrowed type.
  const along = node.kind === "split" && node.direction === "column" ? "height" : "width";
  useEffect(() => {
    const element = box.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(([entry]) => {
      const size = entry?.contentRect;
      if (size) setRoom(along === "height" ? size.height : size.width);
    });
    watch.observe(element);
    return () => watch.disconnect();
  }, [along]);

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

  /* Which children give way, when there is not room for all of them.
   *
   * **The panel library does not enforce `minSize`.** It applies a layout as `flexGrow` onto
   * panels styled `min-width: 0`, and when the minimums over-subscribe the container it
   * renormalises and leaves them under-sized rather than refusing. Measured at the app's own
   * 940px floor with the default layout, whose minimums sum to 1120: the three panes came out
   * 201 / 469 / 268, every one below its declared minimum and the chat 91px under the 560 that
   * `CHAT_FLOOR` existed to defend. No overflow, no clamp, no error. So `surfaces.ts` claiming
   * the minimums are "enforced by the panel library" was simply wrong, and deleting the old
   * viewport-driven yielding on that basis left nothing in its place.
   *
   * This is the rule the design specified and the code did not have: when a split cannot
   * honour every child's minimum, the least-recently-focused child collapses to a tab rail
   * instead of every child getting narrower. A rail is a real answer — the pane is one click
   * from coming back and its tabs are still legible — where 469px of chat is not. */
  const railed = new Set<string>();
  if (!stacked && room > 0) {
    const minimums = node.children.map((child) => minFor(child, false));
    let needed = minimums.reduce((sum, one) => sum + one, 0);
    // Least-recently-focused first, and never the last one standing: a group of all rails
    // shows nothing at all, which is worse than one pane that is too narrow.
    const giving = [...node.children]
      .map((child, index) => ({ child, index }))
      .sort((a, b) => rank(order, b.child) - rank(order, a.child));
    for (const { child, index } of giving) {
      if (needed <= room || railed.size >= node.children.length - 1) break;
      railed.add(child.id);
      needed -= minimums[index] - RAIL;
    }
  }

  return (
    <Group
      elementRef={box}
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
            // A railed child is pinned: min and max together, so the library cannot grow it and
            // its divider is inert. That is also what removes the deficit — the group's demand
            // drops from this child's real minimum to the width of a rail.
            minSize={railed.has(child.id) ? RAIL : minFor(child, stacked)}
            {...(railed.has(child.id) ? { maxSize: RAIL } : {})}
            className="flex min-h-0 min-w-0 flex-col"
          >
            {railed.has(child.id) ? (
              <Rail node={child} titleFor={titleFor} onOpen={focus} />
            ) : (
              <NodeView node={child} render={render} titleFor={titleFor} />
            )}
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
            key={tab.uid ?? tabKey(tab)}
            tab={tab}
            title={tabTitle(tab, titleFor?.(tab))}
            active={index === pane.active}
            onSelect={() => activate(pane.id, index)}
            onClose={() => close(tabKey(tab))}
            others={pane.tabs.filter((one) => one.uid !== tab.uid).map(tabKey)}
            paneId={pane.id}
          />
        ))}
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        {/* Keyed on the tab's permanent `uid`, which is the fix for a remount mid-reply.
            `render` returns an element whose identity React decides by position, so switching
            tabs would otherwise reuse the previous tab's component instance with a new
            conversation prop — and keying on the conversation instead unmounted the pane the
            moment a draft chat was named, discarding the runtime that was streaming into it. */}
        {active ? (
          <PaneBody key={active.uid ?? tabKey(active)}>{render(active)}</PaneBody>
        ) : (
          <EmptyPane />
        )}
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
  others,
  paneId,
}: {
  tab: TabRef;
  title: string;
  active: boolean;
  onSelect: () => void;
  onClose: () => void;
  /** The keys of the other tabs in this pane, for "close the rest". */
  others: string[];
  paneId: string;
}) {
  const Icon = SURFACES[tab.surface].icon;
  const close = useLayout((s) => s.close);
  const dock = useLayout((s) => s.dock);
  const key = tabKey(tab);

  /* Right-click on a tab.
   *
   * A tab strip you can only close one at a time, and only by hitting a 12px X that appears on
   * hover, is a tab strip missing the half of the interaction people reach for first — and
   * splitting was drag-only, which is discoverable by accident or not at all.
   *
   * Radix rather than the app's global `contextmenu` handler: that one opens only over a
   * selection or an editable field, so a tab had nothing to show. Radix calls
   * `preventDefault()` on its own trigger and the global handler bails on
   * `event.defaultPrevented`, so the two cannot both open. */
  const body = (
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

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{body}</ContextMenuTrigger>
      <ContextMenuContent className="w-48">
        <ContextMenuLabel className="truncate">{title}</ContextMenuLabel>
        <ContextMenuSeparator />
        <ContextMenuItem onSelect={onClose}>
          <X className="size-3.5" /> Close
        </ContextMenuItem>
        <ContextMenuItem
          disabled={!others.length}
          onSelect={() => others.forEach((one) => close(one))}
        >
          <XCircle className="size-3.5" /> Close the others
        </ContextMenuItem>
        <ContextMenuSeparator />
        {/* Docking onto its own pane, which is how a tab becomes a pane of its own. Disabled
            when it is the only tab: splitting a pane against its one tab would close the source
            and leave nothing to split, so the tree refuses it — better greyed than inert. */}
        <ContextMenuItem
          disabled={!others.length}
          onSelect={() => dock(key, paneId, "right")}
        >
          <SplitSquareHorizontal className="size-3.5" /> Split to the right
        </ContextMenuItem>
        <ContextMenuItem
          disabled={!others.length}
          onSelect={() => dock(key, paneId, "bottom")}
        >
          <SplitSquareVertical className="size-3.5" /> Split below
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}

/** How wide a collapsed pane is: enough for an icon and its hit target, and no more. */
const RAIL = 36;

/** Where a node sits in the focus order — the *most* recent of its panes, so a split is not
 *  collapsed because one corner of it is stale. `order.length` for a pane never focused, which
 *  puts it first in line to give way. */
function rank(order: string[], node: Node): number {
  const mine = panesOf(node).map((one) => order.indexOf(one.id));
  const known = mine.filter((at) => at >= 0);
  return known.length ? Math.min(...known) : order.length;
}

/** A pane with no room for its contents, shown as the tabs it holds.
 *
 * Its own answer rather than nothing: the pane is one click from coming back, and what it holds
 * stays readable. Clicking focuses it, which moves it to the front of the focus order — so the
 * pane that was giving way becomes the one that stays and something else rails instead. */
function Rail({
  node,
  titleFor,
  onOpen,
}: {
  node: Node;
  titleFor?: (ref: TabRef) => string | undefined;
  onOpen: (paneId: string) => void;
}) {
  const tabs = panesOf(node).flatMap((one) => one.tabs.map((tab) => ({ tab, pane: one.id })));
  return (
    <div className="border-border/60 bg-background flex h-full w-full flex-col items-center gap-1 border-e py-2">
      {tabs.map(({ tab, pane }) => {
        const Icon = SURFACES[tab.surface].icon;
        return (
          <button
            key={tab.uid ?? tabKey(tab)}
            type="button"
            title={tabTitle(tab, titleFor?.(tab))}
            aria-label={`Show ${tabTitle(tab, titleFor?.(tab))}`}
            onClick={() => onOpen(pane)}
            className="text-muted-foreground hover:text-foreground hover:bg-muted rounded p-1.5 transition-colors"
          >
            <Icon className="size-4" />
          </button>
        );
      })}
    </div>
  );
}

/** A keyed wrapper, and nothing else. `render`'s element cannot carry a key the caller does
 *  not control, so the key goes here. */
function PaneBody({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

/** What the last closed tab leaves behind.
 *
 * A pane rather than a blank window, because something has to be on screen and "nothing" is
 * indistinguishable from a crash. */
function EmptyPane() {
  const reset = useLayout((s) => s.reset);
  return (
    <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-3 text-xs">
      <span>Nothing open here — drag a tab in, or open something from the header.</span>
      {/* The only way back to the default from inside the app. `reset` existed with no caller
          at all, which is a recovery action you cannot reach — and the layout is stored, so a
          bad one survives a reload. */}
      <button
        type="button"
        onClick={reset}
        className="border-border/60 hover:text-foreground hover:border-border rounded-full border px-3 py-1 transition-colors"
      >
        Reset the layout
      </button>
    </div>
  );
}
