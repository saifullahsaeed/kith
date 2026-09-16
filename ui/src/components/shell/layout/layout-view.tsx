import { Fragment, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowLeftRight,
  ArrowRight,
  Check,
  Columns2,
  Focus,
  Layers,
  Maximize2,
  Minimize2,
  Pin,
  PinOff,
  Rows2,
  SplitSquareHorizontal,
  SplitSquareVertical,
  Trash2,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Group, Panel, Separator, type Layout } from "react-resizable-panels";

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { cn } from "@/lib/utils";

import {
  carriesTab,
  edgeAt,
  highlightFor,
  insertIndexAt,
  landingIndex,
  TAB_MIME,
} from "./drag";
import { useLayoutKeys } from "./keys";
import { DEFAULT_PLACEMENT, type PlacementMode } from "./place";
import { useLayout } from "./store";
import { MIN_HEIGHT, paneMinWidth, surfaceFor, tabTitle } from "./surfaces";
import {
  panes as panesOf,
  splitAbove,
  tabKey,
  type Edge,
  type Node,
  type PaneNode,
  type TabRef,
} from "./tree";

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
  markFor,
}: {
  render: (ref: TabRef) => ReactNode;
  /** A chat's title. Everything else is named by the surface registry. */
  titleFor?: (ref: TabRef) => string | undefined;
  /** A one-word status for a tab, drawn as a mark on it. `"draft"` is unsent text.
   *
   *  A function passed in rather than a store this module reads, for the same reason
   *  `titleFor` is one: `layout/` answers for the arrangement and must not learn what a
   *  surface holds. It also keeps the ranking of every per-chat signal in one place —
   *  `workspace`, beside `working` and `elsewhere`. */
  markFor?: (ref: TabRef) => "draft" | undefined;
}) {
  const tree = useLayout((s) => s.tree);
  const zoomed = useLayout((s) => s.zoomed);
  useLayoutKeys();

  /* Zoom is answered here and nowhere else.
   *
   * A render-time short-circuit rather than a tree operation: nothing is resized and nothing is
   * moved, so leaving zoom restores the arrangement exactly rather than to whatever the sizes
   * were rounded to on the way out. `zoomed` naming a pane that has since gone is impossible —
   * `commit` clears it with the pane — but it is read defensively anyway, because the failure
   * would be a window with nothing in it. */
  const only = zoomed ? panesOf(tree).find((one) => one.id === zoomed) : undefined;
  if (only) return <PaneView pane={only} render={render} titleFor={titleFor} markFor={markFor} />;

  return <NodeView node={tree} render={render} titleFor={titleFor} markFor={markFor} />;
}

function NodeView({
  node,
  render,
  titleFor,
  markFor,
}: {
  node: Node;
  render: (ref: TabRef) => ReactNode;
  titleFor?: (ref: TabRef) => string | undefined;
  markFor?: (ref: TabRef) => "draft" | undefined;
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
    return <PaneView pane={node} render={render} titleFor={titleFor} markFor={markFor} />;
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
                  six pixels is a divider you chase with the mouse.
                  `focus-visible` on the group as well as hover: the library makes the separator
                  keyboard-operable, and a handle you can move with the arrow keys but cannot
                  see you have hold of is a handle nobody finds twice. */}
              <span className="bg-border/60 group-hover:bg-kith/60 group-focus-visible:bg-kith group-data-[state=drag]:bg-kith absolute inset-0 transition-colors" />
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
              <Rail node={child} titleFor={titleFor} markFor={markFor} onOpen={focus} />
            ) : (
              <NodeView node={child} render={render} titleFor={titleFor} markFor={markFor} />
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
  markFor,
}: {
  pane: PaneNode;
  render: (ref: TabRef) => ReactNode;
  titleFor?: (ref: TabRef) => string | undefined;
  markFor?: (ref: TabRef) => "draft" | undefined;
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
  const move = useLayout((s) => s.move);
  const trackWidth = useLayout((s) => s.trackWidth);
  const focused = useLayout((s) => s.focused === pane.id);
  const zoomed = useLayout((s) => s.zoomed === pane.id);
  const [over, setOver] = useState<Edge | null>(null);
  /* Where a strip drop would land — the index the caret sits before. Null while the drag is
   * over the pane body, where the question is an edge and not a position. */
  const [caret, setCaret] = useState<number | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  const strip = useRef<HTMLDivElement | null>(null);
  const active = pane.tabs[pane.active];

  /* Which of this pane's tabs have ever been looked at, and are therefore built.
   *
   * The whole point of keeping tabs mounted is that the *second* visit is free; it is not that
   * the first one should happen before you ask for it. A pane restored from a stored layout can
   * hold eight chats, and building all eight at startup would pay every cost this change exists
   * to avoid, at the worst possible moment. So a tab is built when it is first activated and kept
   * from then on.
   *
   * A closed tab needs no cleanup here: it leaves `pane.tabs`, so the map below stops rendering
   * it and React unmounts it. What is left behind is its key, a string, in a set that dies with
   * the pane. */
  const activeKey = active ? (active.uid ?? tabKey(active)) : "";

  /* Built during render, not in an effect.
   *
   * This was `useState` plus a `useEffect` that added the newly-active key, and the order that
   * produces is the bug: on the render where you activate a tab for the first time, its key is
   * not in the set yet, so the map below returns `null` for it — React commits a pane with
   * nothing in it, *then* runs the effect, *then* commits again with the content. Two commits
   * and an empty one in between, which is the blank the eye reads as the old text fading out
   * before the new text arrives. Measured at 44ms from click to content with no long task
   * anywhere: not one slow mount, just a round trip through the effect queue.
   *
   * A ref instead, because this is a cache and not state — nothing needs to re-render *because*
   * it changed; it is read in the same render that writes it. Appending the key before the map
   * runs means the tab you just activated is built in that first commit, so there is no frame
   * where the pane is empty.
   *
   * Still lazy, which is the point of the set: a pane restored from storage holding eight chats
   * builds one, not eight. And still append-only — a closed tab leaves `pane.tabs`, React
   * unmounts it, and its key stays behind harmlessly in a set that dies with the pane. */
  const openedRef = useRef<Set<string>>(null);
  openedRef.current ??= new Set();
  if (activeKey) openedRef.current.add(activeKey);
  const opened = openedRef.current;

  /* Placement reads this pane's real width — what the yielding rule left it with — and here is
   * where it is measured. Live state, never stored: a width is a fact about right now, and a
   * stored one would answer for a pane that has since been railed, resized, or re-homed by a
   * reload. Unmeasured is allowed for: placement runs before layout has had its turn, and a
   * wrong first open corrects itself on the next one. */
  useEffect(() => {
    const element = box.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(([entry]) => {
      const width = entry?.contentRect.width;
      if (width) trackWidth(pane.id, width);
    });
    watch.observe(element);
    return () => watch.disconnect();
  }, [pane.id, trackWidth]);

  /** The gap the pointer is over, read off the tab buttons as they are laid out.
   *
   * Rects come from the buttons and not the strip, so variable-width titles put the caret
   * where the tabs actually are — and a strip scrolled to its end measures buttons by their
   * on-screen position, which is the one the pointer can point at. */
  const caretAt = (event: React.DragEvent<HTMLDivElement>): number | null => {
    const element = strip.current;
    if (!element) return null;
    const rects = Array.from(element.querySelectorAll<HTMLElement>("[data-tab]")).map((one) =>
      one.getBoundingClientRect(),
    );
    return insertIndexAt(rects, event.clientX);
  };

  /** The tab the strip currently points at, as the DOM has it. */
  const chosen = () =>
    strip.current?.querySelector<HTMLElement>('[data-tab][aria-selected="true"]') ?? null;

  /* Arrow keys along the strip.
   *
   * The tabs were `div`s with an `onClick` and no `tabIndex`, so a keyboard could not reach
   * them at all — every one of switch, close, sort and split was mouse-only. A roving tabindex
   * plus this handler is the standard tablist pattern, and it is the role `index.css` already
   * opts out of the window drag region, so the markup it needs was anticipated there. */
  const onStripKeys = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const last = pane.tabs.length - 1;
    if (last < 0) return;
    const to =
      event.key === "ArrowRight"
        ? Math.min(pane.active + 1, last)
        : event.key === "ArrowLeft"
          ? Math.max(pane.active - 1, 0)
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? last
              : null;
    if (to === null || to === pane.active) return;
    event.preventDefault();
    activate(pane.id, to);
    // Focus has to follow the selection, or the next arrow key arrives at an element that is
    // no longer the tab it is about — the roving tabindex moved out from under it.
    requestAnimationFrame(() => chosen()?.focus());
  };

  /* The chosen tab, kept on screen.
   *
   * Activating from the header, the rail or the keyboard leaves the strip scrolled wherever it
   * last was, so the tab you just picked can sit past its left edge: selected, rendered, and
   * invisible. Optional call because jsdom has no `scrollIntoView`. */
  useEffect(() => {
    strip.current
      ?.querySelector<HTMLElement>('[data-tab][aria-selected="true"]')
      ?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [pane.active, pane.tabs.length]);

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
      ref={box}
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
        // A ring you can actually see. At `kith/20` this was a 1px line at a fifth of its
        // opacity over a warm ground — present in the markup, absent on screen — so which pane
        // the next tab would open into was a guess. The strip's ground and the active tab's
        // mark below carry the same fact more quietly; this is the outline that scopes it.
        focused && "ring-kith/40 ring-1 ring-inset",
      )}
    >
      {/* The strip is a drop target of its own, which is what makes tabs sortable: the pane
       * body answers "which edge", the strip answers "which gap". Both see dragover for the
       * same pointer — the strip stops propagation, or the pane under it lights up too and the
       * two answers fight over one highlight. */}
      <div
        ref={strip}
        role="tablist"
        aria-orientation="horizontal"
        aria-label="Open tabs"
        onKeyDown={onStripKeys}
        className={cn(
          "flex shrink-0 items-center overflow-x-auto",
          /* The shelf the tabs sit on.
           *
           * There was no ground here at all — a transparent strip over `bg-background` with one
           * border under it — and the active tab was `bg-background` too, which is to say the
           * selected tab was painted the same colour as the empty space beside it. `muted` at a
           * low alpha is the app's own recessed-chrome idiom (the header's segment group is
           * `bg-muted/40`), and it is what makes the active tab read as lifted out of it. */
          focused ? "bg-muted/55" : "bg-muted/40",
          /* The strip's own scrollbar is 9px of a 29px shelf — a third of the chrome spent
           * saying "there is more", which a tab clipped at the edge already says.
           *
           * Both spellings, and the webkit one is not redundant: `index.css` styles
           * `*::-webkit-scrollbar` globally, and once that pseudo-element is styled Chromium
           * takes the legacy path and ignores `scrollbar-width` entirely. */
          "scrollbar-none [&::-webkit-scrollbar]:hidden",
        )}
        onDragOver={(event) => {
          if (!carriesTab(event.dataTransfer)) return;
          event.preventDefault();
          event.stopPropagation();
          event.dataTransfer.dropEffect = "move";
          setOver(null);
          setCaret(caretAt(event));
        }}
        onDragLeave={(event) => {
          // As at the pane: leaving into a child is crossing your own border, not leaving.
          if (event.currentTarget.contains(event.relatedTarget as globalThis.Node | null)) return;
          setCaret(null);
        }}
        onDrop={(event) => {
          if (!carriesTab(event.dataTransfer)) return;
          event.preventDefault();
          event.stopPropagation();
          const key = event.dataTransfer.getData(TAB_MIME);
          const before = caretAt(event);
          setOver(null);
          setCaret(null);
          if (!key || before === null) return;
          /* The caret counts the strip as it is on screen, dragged tab still in it; a move
           * counts the strip without it. Same-pane drops translate, cross-pane ones do not —
           * removing from the source never shifts the target. */
          const from = pane.tabs.findIndex((one) => tabKey(one) === key);
          move(key, pane.id, from >= 0 ? landingIndex(from, before) : before);
        }}
      >
        {pane.tabs.map((tab, index) => (
          <TabButton
            key={tab.uid ?? tabKey(tab)}
            tab={tab}
            title={tabTitle(tab, titleFor?.(tab))}
            mark={markFor?.(tab)}
            active={index === pane.active}
            paneFocused={focused}
            onSelect={() => activate(pane.id, index)}
            onClose={() => close(tabKey(tab))}
            others={pane.tabs.filter((one) => one.uid !== tab.uid).map(tabKey)}
            paneId={pane.id}
            index={index}
            count={pane.tabs.length}
            caret={
              caret === index
                ? "before"
                : caret !== null && caret === pane.tabs.length && index === pane.tabs.length - 1
                  ? "after"
                  : null
            }
          />
        ))}
        {/* The rule under the strip, carried past the last tab to the pane's edge — and the
         * pane's own right-click target.
         *
         * The rule lives on the tabs and on this filler rather than on the strip, and that is
         * what lets the active tab break it: a tab connected to the body below it, which is the
         * whole of what "this pane is showing that one" looks like. It cannot be done with a
         * border on the strip, because a scroll container clips at its padding box and nothing
         * inside can paint over a border belonging to the container.
         *
         * The pane menu hangs here because this is the only part of the strip that is not a
         * tab, so a right-click on it is unambiguously about the pane. */}
        <PaneMenu pane={pane}>
          <div className="border-border/60 min-w-0 flex-1 self-stretch border-b" />
        </PaneMenu>
        {zoomed ? (
          /* An exit that exists only on the keyboard is a trap for whoever got here from the
             menu. Sits after the filler, so it is pinned to the far end of the strip. */
          <button
            type="button"
            aria-label="Leave zoom"
            title="Leave zoom"
            onClick={() => useLayout.getState().zoom(null)}
            className="text-muted-foreground hover:text-foreground hover:bg-accent/60 focus-visible:ring-kith/60 mr-1 shrink-0 rounded p-1 transition-colors focus-visible:ring-2 focus-visible:outline-none"
          >
            <Minimize2 className="size-3.5" />
          </button>
        ) : null}
      </div>

      <div
        role="tabpanel"
        aria-labelledby={active ? `tab-${tabKey(active)}` : undefined}
        className="relative min-h-0 flex-1 overflow-hidden"
      >
        {/* **Every tab you have opened stays mounted; the inactive ones are hidden.**
            
            Switching tabs used to render `active` alone, so the pane you left was *unmounted* —
            its runtime destroyed, its thread thrown away, its scroll and its composer with it.
            Coming back was not showing it again, it was opening it from nothing: measured on a
            real forty-turn conversation, 4.5MB refetched, 3,000 parts rebuilt, 1.46M characters
            of markdown re-parsed and 229 code fences re-highlighted, on every single click. That
            is the lag, and it is also why a reply could arrive into a pane that did not exist and
            why a half-typed message had nowhere to live.

            Hidden with `visibility`, not `display: none`, and that is the load-bearing detail: a
            box with no layout has no scroll offset either, so `display: none` would preserve the
            thread and lose your place in it every time. `visibility: hidden` keeps the layout,
            keeps `scrollTop`, and skips only the paint.

            Mounted lazily and then kept, rather than all at once — a pane holding eight chats
            must not open eight conversations at startup to make the second click fast. So a tab
            is built the first time you look at it, and after that it simply stays.

            Keyed on the tab's permanent `uid`, which is the older fix this one sits on: `render`
            returns an element whose identity React decides by position, and keying on the
            conversation instead unmounted the pane the moment a draft chat was named. */}
        {pane.tabs.length === 0 ? <EmptyPane /> : null}
        {pane.tabs.map((tab, index) => {
          const key = tab.uid ?? tabKey(tab);
          if (!opened.has(key)) return null;
          return (
            <PaneBody key={key} showing={index === pane.active}>
              {render(tab)}
            </PaneBody>
          );
        })}
      </div>

      {over ? (
        <div
          aria-hidden
          style={highlightFor(over)}
          /* Where the drop lands. The edge carries the shape and the tint only says which half
           * — at `border-kith/60` over a dense thread the boundary was the thing that went
           * missing, and a tinted region with no readable edge is not a target. */
          className="bg-kith/15 border-kith/80 pointer-events-none absolute z-30 rounded-sm border-2 transition-all duration-75"
        />
      ) : null}
    </div>
  );
}

/* The placement choices, as the menu names them.
 *
 * The words do the work: "own" is jargon for "a section that keeps this kind — and raises one
 * when there is none", which is the default; the others are the overrides for when the rule is
 * wrong for one surface. The person reading this menu is mid-click, not reading a manual. */
const PLACEMENT_CHOICES: { mode: PlacementMode; label: string; icon: LucideIcon }[] = [
  { mode: "own", label: "With their kind, or a new pane", icon: Columns2 },
  { mode: "grouped", label: "With their kind, else here", icon: Layers },
  { mode: "focused", label: "Always where I am", icon: Focus },
  { mode: "beside", label: "Always a new pane", icon: SplitSquareHorizontal },
];

function TabButton({
  tab,
  title,
  mark,
  active,
  paneFocused,
  onSelect,
  onClose,
  others,
  paneId,
  index,
  count,
  caret,
}: {
  tab: TabRef;
  title: string;
  /** What this tab is holding that its name cannot say. `"draft"` is unsent text. */
  mark: "draft" | undefined;
  active: boolean;
  /** Whether this tab's pane is the focused one — which is what the active tab's mark says. */
  paneFocused: boolean;
  onSelect: () => void;
  onClose: () => void;
  /** The keys of the other tabs in this pane, for "close the rest". */
  others: string[];
  paneId: string;
  /** This tab's position and the strip's length: the move menu greys itself out at the ends,
   *  where there is nothing to move toward. */
  index: number;
  count: number;
  /** Which side of this tab a pending strip drop would land on, while the drag is over the
   *  strip; null the rest of the time, when there is nothing to draw. */
  caret: "before" | "after" | null;
}) {
  const Icon = surfaceFor(tab).icon;
  const close = useLayout((s) => s.close);
  const dock = useLayout((s) => s.dock);
  const move = useLayout((s) => s.move);
  const setPlacement = useLayout((s) => s.setPlacement);
  const addPin = useLayout((s) => s.pin);
  const dropPin = useLayout((s) => s.unpin);
  /* Pinned-ness is derived, never stored on the tab.
   *
   * One source of truth — the pins map — so there is no second copy to fall out of step, and
   * no new field on the persisted tree for something that is a preference rather than part of
   * the arrangement. */
  const pinned = useLayout((s) => tabKey(tab) in s.pins);
  const pins = useLayout((s) => s.pins);
  /* Unset reads as the default: the checkmark has to sit on the mode that will actually run,
   * not on a field that happens to be empty. */
  const placement = useLayout((s) => s.placements[tab.surface]) ?? DEFAULT_PLACEMENT;
  const key = tabKey(tab);
  const closeable = others.filter((one) => !(one in pins));

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
      data-tab={key}
      id={`tab-${key}`}
      role="tab"
      aria-selected={active}
      /* The draft mark's words. The ring on the icon is `aria-hidden` — a `<span>` has no role
       * for an `aria-label` to attach to — so hovering is answered here and the `sr-only` span
       * below the title answers the reader. */
      title={mark === "draft" ? "You typed something here and did not send it" : undefined}
      // Roving tabindex: one stop per strip, arrows move within it. Tab lands you on the tab
      // you are looking at rather than walking you through eight of them to reach the body.
      tabIndex={active ? 0 : -1}
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData(TAB_MIME, tabKey(tab));
        event.dataTransfer.effectAllowed = "move";
      }}
      onClick={onSelect}
      onKeyDown={(event) => {
        // Only the tab's own keys. Without the target check, Enter on the close button inside
        // it would both close the tab and select it on the way out.
        if (event.target !== event.currentTarget) return;
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onSelect();
      }}
      className={cn(
        "group relative flex max-w-[220px] shrink-0 cursor-default items-center gap-1.5 border-b px-3 py-1.5 text-xs transition-colors",
        "focus-visible:ring-kith/60 focus-visible:ring-2 focus-visible:ring-inset focus-visible:outline-none",
        active
          ? /* Lifted onto the pane's own ground and continuous with it: the bottom rule stops
             * here. `border-b-transparent` rather than no border, so switching tabs cannot
             * change the strip's height by a pixel. */
            "bg-background text-foreground border-b-transparent"
          : "border-border/60 text-muted-foreground hover:bg-background/50 hover:text-foreground",
      )}
    >
      {/* Which tab — and, by its colour, which pane.
          Amber only in the focused pane; a grey rule everywhere else. Two pixels answering both
          "which of these am I on" and "is this the pane my next tab opens into", which is one
          device instead of the near-invisible ring that used to carry the second question
          alone. It sits at the top edge because the bottom one is now the join to the body. */}
      {active ? (
        <span
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-x-0 top-0 h-0.5 transition-colors",
            paneFocused ? "bg-kith" : "bg-muted-foreground/60",
          )}
        />
      ) : null}
      {/* The insertion point of a pending strip drop, on the tab it would land before — or on
          the last one, after. A span and not a border, so showing it never shifts the strip the
          caret was measured against. Inset and rounded so it reads as a caret between two tabs
          rather than as an edge belonging to one of them. */}
      {caret ? (
        <span
          aria-hidden
          className={cn(
            "bg-kith pointer-events-none absolute inset-y-1 z-10 w-0.5 rounded-full",
            caret === "before" ? "left-0" : "right-0",
          )}
        />
      ) : null}
      {/* Something typed here and not sent, worn on the corner of the icon.
        *
        * The strip has never carried a per-tab status of any kind, so this is new vocabulary
        * rather than a copy — but it is built out of the conversation dots' size (`size-1.5`, a
        * rounded full span) with their fill and motion inverted. Every status dot in this app is
        * solid and pulsing, because every one of them is about something in flight; a draft is the
        * opposite — nothing is moving and nothing is waiting on him — so it is hollow and still.
        * That also spares it a colour: `--kith` already means "waiting on you" *and* marks the
        * active tab, `--roam` means "running", and a third hue for a fourth meaning is how a
        * vocabulary stops being one.
        *
        * **On the icon rather than in the row**, which is the difference between a mark and a
        * flinch. As a real child of this flex row it took 12px of width — gap plus dot — so the
        * first character typed pushed every tab to its right along, and backspace pulled them
        * back; on a tab already at its width cap the title's truncation shortened mid-sentence
        * instead. Absolutely positioned on the icon it costs no layout at all, and it is the same
        * badge in the same place as the railed version, which is what makes the two read as one
        * thing rather than two.
        *
        * The tab's `title` carries the words. A bare `aria-label` on a `<span>` is not an
        * accessible name — the element has no role to hang one on — and the visible label beside
        * it is the tab's name, so putting it on the tooltip is both the honest place and the one a
        * screen reader reaches. */}
      <span className="relative shrink-0">
        <Icon className={cn("size-3.5 transition-opacity", active ? "opacity-90" : "opacity-60")} />
        {mark === "draft" ? (
          <span
            aria-hidden
            className="border-muted-foreground/70 bg-muted/40 absolute -top-0.5 -right-0.5 size-1.5 rounded-full border"
          />
        ) : null}
      </span>
      <span className="truncate">{title}</span>
      {mark === "draft" ? <span className="sr-only">— unsent text</span> : null}
      {/* A pinned tab wears the pin where the close button would be.
       *
       * Not a button. The X in this slot is the accidental close — a 22px target you pass over
       * on the way to the tab you meant — and putting "unpin" under the same pixel would make
       * the pin one slip away from being undone. Unpinning is the context menu or Cmd+Alt+P,
       * both of which you have to mean. */}
      {pinned ? (
        <Pin
          aria-hidden
          className="-mr-0.5 size-3 shrink-0 rotate-45 opacity-50"
        />
      ) : (
      <button
        type="button"
        aria-label={`Close ${title}`}
        // Reachable only once its tab is, and only from the tab: Tab steps onto the tab, then
        // onto its close button, then out into the body.
        tabIndex={active ? 0 : -1}
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
        className={cn(
          // 22px of hit target instead of 13px. A 12px glyph in half a pixel of padding is a
          // target you miss and select the tab instead — which, on a tab you meant to close,
          // is the one wrong outcome available.
          "-mr-1.5 rounded p-1 transition-[color,background-color,opacity]",
          "hover:bg-destructive/10 hover:text-destructive",
          "focus-visible:ring-kith/60 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:outline-none",
          // Always there on the tab you are looking at — you can always close what is in front
          // of you — and revealed on hover for the rest, so a strip of eight is not eight X's.
          active
            ? "opacity-60 hover:opacity-100"
            : "opacity-0 group-hover:opacity-60 hover:opacity-100",
        )}
      >
        <X className="size-3.5" />
      </button>
      )}
    </div>
  );

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{body}</ContextMenuTrigger>
      <ContextMenuContent className="w-48">
        <ContextMenuLabel className="truncate">{title}</ContextMenuLabel>
        <ContextMenuSeparator />
        {/* `icon` rather than an icon in the children, which is the component's own contract
            and not decoration: children land in a `flex-1 truncate` span, and Tailwind's
            preflight sets `svg { display: block }` — so an inline icon pushed every label onto
            a second line and the menu rendered as eight stacked rows instead of four. */}
        <ContextMenuItem icon={<X className="size-3.5" />} onSelect={onClose}>
          Close
        </ContextMenuItem>
        {/* Pinned tabs survive it. That is half of what pinning is for, and a "close the
            others" that took them with it would make the pin worth nothing on the one gesture
            most likely to be aimed at a strip you have arranged. */}
        <ContextMenuItem
          icon={<XCircle className="size-3.5" />}
          disabled={!closeable.length}
          onSelect={() => closeable.forEach((one) => close(one))}
        >
          Close the others
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          icon={pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
          onSelect={() => (pinned ? dropPin(key) : addPin(key))}
        >
          {pinned ? "Unpin" : "Pin to this pane"}
        </ContextMenuItem>
        <ContextMenuSeparator />
        {/* Docking onto its own pane, which is how a tab becomes a pane of its own. Disabled
            when it is the only tab: splitting a pane against its one tab would close the source
            and leave nothing to split, so the tree refuses it — better greyed than inert. */}
        <ContextMenuItem
          icon={<SplitSquareHorizontal className="size-3.5" />}
          disabled={!others.length}
          onSelect={() => dock(key, paneId, "right")}
        >
          Split to the right
        </ContextMenuItem>
        <ContextMenuItem
          icon={<SplitSquareVertical className="size-3.5" />}
          disabled={!others.length}
          onSelect={() => dock(key, paneId, "bottom")}
        >
          Split below
        </ContextMenuItem>
        <ContextMenuSeparator />
        {/* Sorting, for anyone in a hurry: drag is discoverable by accident or not at all, and
            two items that always work cost nothing. Greyed at the ends rather than hidden, for
            the same reason Split is greyed on a lone tab — disabled is honest, missing is not. */}
        <ContextMenuItem
          icon={<ArrowLeft className="size-3.5" />}
          disabled={index === 0}
          onSelect={() => move(key, paneId, index - 1)}
        >
          Move left
        </ContextMenuItem>
        <ContextMenuItem
          icon={<ArrowRight className="size-3.5" />}
          disabled={index === count - 1}
          onSelect={() => move(key, paneId, index + 1)}
        >
          Move right
        </ContextMenuItem>
        <ContextMenuSeparator />
        {/* Where the next tab of this surface opens — the preference the placement policy
            honours, set on the tab because that is where the question "where do these live?"
            gets asked. It survives panes, which come and go; it is a choice, not a layout. */}
        <ContextMenuSub>
          <ContextMenuSubTrigger icon={<Layers className="size-3.5" />}>
            Where new ones open
          </ContextMenuSubTrigger>
          {/* Wide enough for the labels that name the difference — "or a new pane" is the
              whole of what own means, and a truncated menu cannot say it. */}
          <ContextMenuSubContent className="w-64">
            {PLACEMENT_CHOICES.map((one) => (
              <ContextMenuItem
                key={one.mode}
                icon={
                  placement === one.mode ? (
                    <Check className="size-3.5" />
                  ) : (
                    <one.icon className="size-3.5" />
                  )
                }
                onSelect={() => setPlacement(tab.surface, one.mode)}
              >
                {one.label}
              </ContextMenuItem>
            ))}
          </ContextMenuSubContent>
        </ContextMenuSub>
      </ContextMenuContent>
    </ContextMenu>
  );
}

/** The pane itself, right-clicked — the verbs that are about a pane rather than a tab.
 *
 * These four had no home before. Closing a pane meant closing its tabs one at a time; turning a
 * row of columns into a stack, or moving a column to the other side, meant three drags and a
 * resize; and zoom had only a keyboard shortcut, which is a feature nobody finds.
 *
 * `flip` and `reverse` act on the split *above* this pane, which is the thing a person means
 * when they say "put that column on the other side" while pointing at a pane. Absent for a pane
 * that is the whole window: there is no split, and greying out an item whose target does not
 * exist says less than not offering it. */
function PaneMenu({ pane, children }: { pane: PaneNode; children: ReactNode }) {
  const zoomed = useLayout((s) => s.zoomed === pane.id);
  const zoom = useLayout((s) => s.zoom);
  const closePane = useLayout((s) => s.closePane);
  const flipSplit = useLayout((s) => s.flipSplit);
  const rotateSplit = useLayout((s) => s.rotateSplit);
  const above = useLayout((s) => splitAbove(s.tree, pane.id));
  const alone = useLayout((s) => panesOf(s.tree).length === 1);

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className="w-52">
        <ContextMenuLabel>This pane</ContextMenuLabel>
        <ContextMenuSeparator />
        <ContextMenuItem
          icon={zoomed ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
          onSelect={() => zoom(zoomed ? null : pane.id)}
        >
          {zoomed ? "Leave zoom" : "Fill the window"}
        </ContextMenuItem>
        {/* The last pane stays, emptied — invariant 5 — so this is honest rather than disabled:
            it does close everything in the pane either way. */}
        <ContextMenuItem
          icon={<Trash2 className="size-3.5" />}
          onSelect={() => closePane(pane.id)}
        >
          {alone ? "Close everything here" : "Close this pane"}
        </ContextMenuItem>
        {above ? (
          <>
            <ContextMenuSeparator />
            <ContextMenuItem
              icon={
                above.direction === "row" ? (
                  <Rows2 className="size-3.5" />
                ) : (
                  <Columns2 className="size-3.5" />
                )
              }
              onSelect={() => flipSplit(above.id)}
            >
              {above.direction === "row" ? "Stack these" : "Side by side"}
            </ContextMenuItem>
            <ContextMenuItem
              icon={<ArrowLeftRight className="size-3.5" />}
              onSelect={() => rotateSplit(above.id)}
            >
              Swap them round
            </ContextMenuItem>
          </>
        ) : null}
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
  markFor,
  onOpen,
}: {
  node: Node;
  titleFor?: (ref: TabRef) => string | undefined;
  markFor?: (ref: TabRef) => "draft" | undefined;
  onOpen: (paneId: string) => void;
}) {
  const trackWidth = useLayout((s) => s.trackWidth);
  const activate = useLayout((s) => s.activate);
  const focused = useLayout((s) => s.focused);
  const box = useRef<HTMLDivElement | null>(null);
  const groups = panesOf(node);

  /* A railed pane is 36px of icons, and placement should know it: without this the pane keeps
   * reporting the width it had when it was last a real pane, and a wide surface keeps opening
   * into a rail — open, and invisible. Every pane under the rail reports the rail's width,
   * because that is how much of each of them there is. */
  useEffect(() => {
    const element = box.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(([entry]) => {
      const width = entry?.contentRect.width;
      if (!width) return;
      for (const one of panesOf(node)) trackWidth(one.id, width);
    });
    watch.observe(element);
    return () => watch.disconnect();
  }, [node, trackWidth]);

  return (
    <div
      ref={box}
      // The same shelf the strip is, stood on its end — because that is what a rail is.
      className="border-border/60 bg-muted/40 flex h-full w-full flex-col items-center gap-0.5 border-e py-2"
    >
      {groups.map((one, at) => (
        <Fragment key={one.id}>
          {/* Which of these icons belong together. A rail can cover a whole split, and flattened
              into one column there was nothing to say that the top two are one pane and the
              third is another — so clicking the third looked like it should join the first two. */}
          {at > 0 ? <span aria-hidden className="bg-border/60 my-1 h-px w-4 shrink-0" /> : null}
          {one.tabs.map((tab, index) => {
            const Icon = surfaceFor(tab).icon;
            const label = tabTitle(tab, titleFor?.(tab));
            const current = index === one.active;
            /* The badge cannot name itself.
             *
             * A `<span>` has no role for an `aria-label` to attach to, and this button already
             * carries an explicit one — which *is* the accessible name, so a child's label is
             * never read. The mark is a corner ring with no text anywhere near it, and a rail
             * shows no title to put words beside, so both the tooltip and the announced name have
             * to say it here or it is a decoration nobody can account for. */
            const drafting = markFor?.(tab) === "draft";
            const said = drafting ? `${label} — unsent text` : label;
            return (
              <button
                key={tab.uid ?? tabKey(tab)}
                type="button"
                title={said}
                aria-label={`Show ${said}`}
                aria-current={current || undefined}
                /* Activate *this* tab, then focus the pane.
                 *
                 * `onOpen(pane)` alone only focused the pane, so clicking the second icon in a
                 * rail expanded it onto the first tab — the one interaction a rail exists for,
                 * answering with something other than what you clicked. */
                onClick={() => {
                  if (!current) activate(one.id, index);
                  onOpen(one.id);
                }}
                className={cn(
                  "relative rounded-md p-2 transition-colors",
                  "focus-visible:ring-kith/60 focus-visible:ring-2 focus-visible:outline-none",
                  // Its pane's own selection, wearing the same amber the strip's mark does: what
                  // this rail is holding open, and whether it is the pane you are in.
                  current
                    ? focused === one.id
                      ? "bg-kith-soft text-kith"
                      : "text-foreground bg-accent/60"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/60",
                )}
              >
                <Icon className="size-4" />
                {/* The rail carries the mark too, and it is the surface that needs it most:
                    railing a pane is one of the ways a draft went missing, and a rail shows no
                    title to hang it beside — so it sits on the icon's corner, where a badge goes.
                    Hollow and still for the reason the strip's is. */}
                {drafting ? (
                  <span
                    aria-hidden
                    className="border-muted-foreground/70 bg-muted/40 absolute top-0.5 right-0.5 size-1.5 rounded-full border"
                  />
                ) : null}
              </button>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}

/** A keyed wrapper, and nothing else. `render`'s element cannot carry a key the caller does
 *  not control, so the key goes here. */
/**
 * One tab's surface, shown or held.
 *
 * `absolute inset-0` because every mounted tab occupies the same box and only one is visible;
 * the parent is already `relative` and `overflow-hidden`, so this changes nothing about how a
 * surface sizes itself — it still resolves `h-full` against the pane.
 *
 * `inert` rather than `aria-hidden`, because a held tab must be unreachable as well as unread:
 * without it Tab walks you through the composer of a chat you cannot see, and a screen reader
 * reads three conversations as one. `inert` implies `aria-hidden` and also takes the subtree out
 * of hit-testing, which `pointer-events-none` alone would not do for focus.
 */
function PaneBody({ showing, children }: { showing: boolean; children: ReactNode }) {
  return (
    <div
      className={cn(
        "absolute inset-0",
        /* `opacity-0` beside `invisible`, and the second one is what actually holds.
         *
         * `visibility` is inherited but *overridable*: a descendant setting
         * `visibility: visible` paints straight through a hidden ancestor. Tailwind ships
         * `.visible{visibility:visible}` — the only rule in the stylesheet that sets it — and a
         * dependency's own classNames use it, so a held tab leaked pieces of itself over the
         * tab you had just switched to: a Copy button, an icon, and the text around them, drawn
         * on top of another conversation for as long as those elements were mounted. Measured
         * at seven elements computing to `visible` inside a body computing to `hidden`.
         *
         * An ancestor's `opacity: 0` cannot be undone from inside the subtree — it establishes
         * a stacking context and the whole group composites at zero — so this is the version of
         * "hidden" that no dependency can opt out of. `invisible` stays for the same reason it
         * was there: it takes the subtree out of the accessibility tree and out of find-in-page.
         *
         * Both keep the property this needs — the box stays laid out and its scroll offsets
         * survive — which is why `display: none` was rejected here in the first place. */
        showing ? null : "invisible opacity-0 pointer-events-none",
      )}
      inert={!showing}
    >
      {children}
    </div>
  );
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
        className="border-border/60 hover:text-foreground hover:border-border focus-visible:ring-kith/60 rounded-full border px-3 py-1 transition-colors focus-visible:ring-2 focus-visible:outline-none"
      >
        Reset the layout
      </button>
    </div>
  );
}
