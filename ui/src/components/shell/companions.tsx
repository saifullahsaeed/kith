import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Maximize2, Minimize2, Plus, X, type LucideIcon } from "lucide-react";

import { fetchWorkingOn } from "@/components/assistant-ui/working-on";
import { fetchProcesses } from "@/components/chat/background-tasks";
import { fetchRunBoard } from "@/components/chat/workflows";
import { useLayout } from "@/components/shell/layout/store";
import { tabKey, type TabRef } from "@/components/shell/layout/tree";
import { fetchConversation, fetchLiveTurns } from "@/lib/backend";
import type { ActivityItem } from "@/lib/backend/activity";
import { subscribe } from "@/lib/backend/events";
import { useBoardOverlay, type OpenBoard } from "@/lib/design/board-overlay";
import { indexSettled, pluginSurfaces, watchPluginSurfaces, type PluginSurface } from "@/lib/plugin-index";
import { keys } from "@/lib/query-keys";
import { pathForTab } from "@/lib/router";
import { cn } from "@/lib/utils";

/**
 * The column a chat carries with it: one surface at a time, picked from a row of tabs.
 *
 * Work opened from a chat is *about* that chat, so it belongs with it — not as a sibling in the
 * pane's tab strip, where it sits at the same level as the conversation and has to be told apart
 * from every other Work tab by reading its title. Binding the surface to the conversation settled
 * which chat it is about; this settles where it goes.
 *
 * **Tabs, not a stack.** It used to stack every attached panel and share the height between
 * them, so a Browser with nothing loaded took half the column from the Work panel beside it, and
 * three open panels were three slivers. Now the column is one surface at full height and a 44px
 * row above it: Activity · Context · Browser · Canvas · Files, a dot on a tab with something live
 * in it, and a button that opens the column across the whole tab.
 *
 * A tab is shown by *attaching* its surface — the same `attach` the header button and the store's
 * `open` use — so what a tab draws is still `workspace.surfaceBody`, the one switch every surface
 * goes through, and nothing here renders a surface of its own. Canvas and Files are the exception
 * and say so: neither has a body that fits a column (the board is a window-sized canvas, the file
 * list a control-panel page), so those two tabs open their screen instead of filling this one.
 *
 * **Deliberately not a second tiling tree.** No nesting, no drag-to-split. The outer tree already
 * answers "two things side by side", and answering it twice, differently, in two places, is how a
 * window manager gets a class of bug nobody can reproduce.
 */

const KEY = "kith-companion-width";
const MIN = 280;
const MAX = 720;
/** The design's column, measured: 424px beside a chat. */
const DEFAULT = 424;

function readWidth(): number {
  try {
    const held = Number(localStorage.getItem(KEY));
    return Number.isFinite(held) && held > 0 ? clampWidth(held) : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function clampWidth(width: number): number {
  return Math.min(MAX, Math.max(MIN, Math.round(width)));
}

export type CompanionPanel = {
  /** The companion's tab key — its identity, and what `detach` takes. */
  key: string;
  title: string;
  icon: LucideIcon;
  body: React.ReactNode;
};

/**
 * A tab's body, with whatever that tab carries beside it.
 *
 * **Every chat's body comes back inside a `Companions`, including a chat carrying nothing**, and
 * that is the entire reason this is a function rather than a branch at the call site.
 *
 * React decides a component's identity by its type and its position. Return the host itself when
 * there are no panels and a `<Companions>` when there is one, and the type at that position
 * changes the moment the first panel opens: the whole subtree below it is unmounted and built
 * again. For a chat that means its runtime, its thread, its scroll offset and its half-written
 * message are thrown away, and the conversation is refetched to replace them — which is the work
 * panel appearing to reload the chat, once when it opens and again when it closes.
 *
 * That shape existed twice, at two layers — here and in `workspace.renderSurface` — and fixing
 * the inner one left the outer one free to do the same damage on its own. So the decision has one
 * home, and `Companions` renders a bare host when it has no panels rather than refusing to render.
 *
 * Non-chat surfaces are handed straight back. A companion is a surface that is *about a
 * conversation*, and a Board with a Work panel bolted to its side is a shape with nothing to mean.
 */
export function withCompanions(
  ref: { surface: string; conversationId?: string },
  host: React.ReactNode,
  panels: CompanionPanel[],
  onClose: (key: string) => void,
): React.ReactNode {
  if (ref.surface !== "chat") return host;
  return (
    <Companions
      host={host}
      panels={panels}
      onClose={onClose}
      conversationId={ref.conversationId ?? ""}
    />
  );
}

/** Which tab a panel belongs to, read off its key — `tabKey` is the one spelling of identity.
 *  Core tabs by name; a plugin's surface by `plugin:<id>/<view>`, so every plugin that declares
 *  one is placed the same way and none is special. */
type Slot = string;

function slotOf(key: string): Slot | null {
  if (key === "work" || key.startsWith("work@")) return "activity";
  if (key === "context" || key.startsWith("context@")) return "context";
  const plugin = /^plugin:([^/@#]+)\/([^/@#]+)/.exec(key);
  if (plugin) return `plugin:${plugin[1]}/${plugin[2]}`;
  return null;
}

/** What is live in a tab: a workflow (teal, its colour everywhere), or him working (green). */
type Live = "workflow" | "working" | null;

type Tab = {
  id: string;
  label: string;
  /** The attached panel this tab shows, when it is attached. */
  panel?: CompanionPanel;
  /** What to attach when it is picked and is not attached yet. */
  attach?: TabRef;
  /** The plugin this tab is a surface of, for lighting it while the turn uses that plugin. */
  plugin?: string;
  /** A plugin's tab can be closed on its own; the core tabs cannot. */
  closable?: boolean;
  /** A tab that opens something rather than showing it here. */
  act?: () => void;
  disabled?: boolean;
  title?: string;
  live?: Live;
};

/** The tab each chat's column was last showing, so switching away from a chat tab — which
 *  unmounts its pane — and back lands on the same one. Module state on purpose: it is a fact
 *  about this window's session, and a column restored from storage already on Browser would be a
 *  column you did not leave that way. */
const PICKED = new Map<string, string>();

/**
 * The surfaces a chat's column offers a tab for: every *enabled* plugin's view that is about a
 * conversation (`answers: "conversation"` in its manifest), in the order the server lists them.
 *
 * Read from the plugin index, not written into this file. The index is the server's list of
 * enabled plugins' surfaces — it ships in the page and is refreshed while the app runs — so
 * installing a plugin adds its tab, and disabling or removing one takes its tab away, with no
 * change here. Browser is one of these like any other; it was a hard-coded tab, which drew a
 * dead "Browser" button on a machine where it was switched off.
 */
function useChatSurfaces(): PluginSurface[] {
  const all = useSyncExternalStore(watchPluginSurfaces, pluginSurfaces, pluginSurfaces);
  return all.filter((one) => one.answers === "conversation");
}

export function Companions({
  host,
  panels,
  onClose,
  conversationId,
}: {
  host: React.ReactNode;
  panels: CompanionPanel[];
  onClose: (key: string) => void;
  /** The chat this column belongs to. Absent only where there is no chat to attach to, and then
   *  the tab row shows what is attached and nothing more. */
  conversationId?: string;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(readWidth);
  const [dragging, setDragging] = useState(false);
  /* The column taking the whole tab. Local and unstored on purpose: it is a fact about what you
   * are doing right now, the way zoom is for a pane, and a tab that came back from storage
   * already filled by a panel would read as a tab that had lost its conversation. */
  const [expanded, setExpanded] = useState(false);
  const attach = useLayout((state) => state.attach);
  const surfaces = useChatSurfaces();
  const memo = conversationId ?? "";

  /* Which tab is showing. A panel that arrives is shown — whoever attached it asked to see it,
   * whether that was a tab here, the header, the model opening the browser, or the context meter
   * — and otherwise the one last picked. Tracked against the keys as they were, the "adjust
   * state while rendering" way, so a new panel is on screen in the same commit it arrives in. */
  const present = panels.map((one) => one.key).join("\n");
  const [picked, setPicked] = useState<string | null>(
    () => PICKED.get(memo) ?? panels[panels.length - 1]?.key ?? null,
  );
  const [seen, setSeen] = useState(present);
  if (seen !== present) {
    const before = new Set(seen.split("\n"));
    const arrived = panels.filter((one) => !before.has(one.key));
    setSeen(present);
    if (arrived.length) setPicked(arrived[arrived.length - 1].key);
  }
  useEffect(() => {
    if (picked) PICKED.set(memo, picked);
  }, [memo, picked]);

  const shown =
    panels.find((one) => one.key === picked) ??
    panels.find((one) => slotOf(one.key) === "activity") ??
    panels[panels.length - 1] ??
    null;

  /* Context is not a tab any more — it opens full screen from the composer's ring — so a Context
   * panel left attached from before (the column is persisted) is put away rather than drawn with
   * no tab to reach it by. */
  const staleContext = panels.find((one) => slotOf(one.key) === "context")?.key;
  useEffect(() => {
    if (staleContext) onClose(staleContext);
  }, [staleContext, onClose]);

  const settle = useCallback((next: number) => {
    try {
      localStorage.setItem(KEY, String(next));
    } catch {
      /* a width is a convenience, not the work */
    }
  }, []);

  /* Pointer capture rather than window listeners, for the reason the rail gives: the pointer
   * stays with the handle when it outruns a 5px target, and a cancelled gesture releases
   * itself instead of leaving a listener behind. */
  const onMove = useCallback(
    (event: React.PointerEvent) => {
      if (!dragging) return;
      const right = box.current?.getBoundingClientRect().right ?? 0;
      setWidth(clampWidth(right - event.clientX));
    },
    [dragging],
  );

  const end = useCallback(
    (event: React.PointerEvent) => {
      if (!dragging) return;
      setDragging(false);
      const right = box.current?.getBoundingClientRect().right ?? 0;
      const next = clampWidth(right - event.clientX);
      setWidth(next);
      settle(next);
    },
    [dragging, settle],
  );

  useEffect(() => {
    if (!dragging) return;
    const style = document.body.style;
    const heldCursor = style.cursor;
    const heldSelect = style.userSelect;
    style.cursor = "col-resize";
    style.userSelect = "none";
    return () => {
      style.cursor = heldCursor;
      style.userSelect = heldSelect;
    };
  }, [dragging]);

  // Escape puts a full-width column back beside the chat — the chat never left, it was hidden.
  useEffect(() => {
    if (!expanded) return;
    const back = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) setExpanded(false);
    };
    addEventListener("keydown", back);
    return () => removeEventListener("keydown", back);
  }, [expanded]);

  const full = expanded && panels.length > 0;

  /* The row, before anything live is known about it. */
  const bySlot = (slot: Slot) => panels.find((one) => slotOf(one.key) === slot);
  const bound = conversationId !== undefined && conversationId !== "";
  const tabs: Tab[] = [
    {
      id: "activity",
      label: "Activity",
      panel: bySlot("activity"),
      attach: bound ? { surface: "work", conversationId } : undefined,
    },
    /* A plugin's surface is a tab only while it is open in this chat — he opened it (`show`), or
     * you did from "+" below. Nothing about a plugin is permanent here: switch one off and it
     * leaves the "+" list, and its open tab closes (see `gone` below). */
    ...panels
      .filter((one) => {
        const slot = slotOf(one.key);
        return slot !== "activity" && slot !== "context";
      })
      .map((one): Tab => {
        const slot = slotOf(one.key) ?? "";
        const plugin = /^plugin:([^/]+)\//.exec(slot)?.[1];
        return { id: one.key, label: one.title, panel: one, closable: true, ...(plugin ? { plugin } : {}) };
      }),
    { id: "canvas", label: "Canvas" },
    { id: "files", label: "Files" },
  ];
  /** What "+" offers: every enabled plugin surface about a chat that is not open here yet. */
  const more: Tab[] = bound
    ? surfaces
        .filter((one) => !bySlot(`plugin:${one.plugin}/${one.view}`))
        .map((one) => ({
          id: `plugin:${one.plugin}/${one.view}`,
          label: one.title,
          plugin: one.plugin,
          attach: { surface: "plugin", plugin: one.plugin, view: one.view, conversationId },
        }))
    : [];

  /* A plugin switched off while its tab is open: the index no longer lists it, so the tab is
   * closed rather than left showing a surface that cannot mount. Only once the index has been
   * read at all — before that, an empty list means "not known yet", not "none installed". */
  const known = indexSettled();
  const listed = new Set(surfaces.map((one) => one.plugin));
  const gone = known
    ? panels
        .filter((one) => /^plugin:/.test(one.key))
        .filter((one) => !listed.has(/^plugin:([^/]+)\//.exec(one.key)?.[1] ?? ""))
        .map((one) => one.key)
        .join("\n")
    : "";
  useEffect(() => {
    if (gone) for (const key of gone.split("\n")) onClose(key);
  }, [gone, onClose]);

  const pick = (tab: Tab) => {
    if (tab.act) return tab.act();
    if (tab.panel) return setPicked(tab.panel.key);
    if (tab.attach && bound) {
      setPicked(tabKey(tab.attach));
      attach(tabKey({ surface: "chat", conversationId: conversationId ?? "" }), tab.attach);
    }
  };

  const row = {
    tabs,
    more,
    onCloseTab: onClose,
    shown: shown?.key ?? null,
    expanded: full,
    onPick: pick,
    onExpand: () => setExpanded((was) => !was),
    onCloseAll: () => {
      setExpanded(false);
      for (const one of panels) onClose(one.key);
    },
  };

  /* **The host's position in this tree never changes, and that is the whole point.**
   *
   * This used to `return <>{host}</>` when there were no panels, on the reasoning that no
   * companions is not a layout. The reasoning is right about the *pixels* and was wrong about the
   * *tree*: the element at this position went from a Fragment to a `div` the moment the first
   * panel opened, and React reconciles by type, so opening Work or Context on a chat that had
   * neither unmounted the chat underneath it. Its runtime, its thread, its scroll and its
   * half-written message went with it, and it came back by refetching the conversation.
   *
   * So the wrapper is unconditional and the *column* is what appears: `flex-1` on the host with
   * no siblings is the whole tab. Siblings added and removed after index 0 cannot disturb index 0.
   */
  return (
    <div ref={box} className="flex h-full min-h-0 w-full">
      {/* Hidden rather than unmounted while the column is full width. Unmounting the chat would
          throw away its runtime and its scroll the way switching tabs used to — and leaving a
          full-width column would then be an open, not a return. */}
      <div className={cn("min-w-0 flex-1", full && "hidden")}>{host}</div>

      {panels.length === 0 ? null : (
        <>
          {/* The handle sits *on* the column's left edge rather than beside it: its 1px line is the
              column's border, and the 5px it catches the pointer with overlap both sides instead of
              opening a gap between the chat and the column. */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize the panel column"
            tabIndex={0}
            onPointerDown={(event) => {
              event.preventDefault();
              event.currentTarget.setPointerCapture(event.pointerId);
              setDragging(true);
            }}
            onPointerMove={onMove}
            onPointerUp={end}
            onPointerCancel={end}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft") setWidth((was) => clampWidth(was + 16));
              else if (event.key === "ArrowRight") setWidth((was) => clampWidth(was - 16));
              else return;
              event.preventDefault();
              settle(width);
            }}
            className={cn(
              "relative z-10 -ms-[2px] -me-[3px] w-[5px] shrink-0 cursor-col-resize",
              full && "hidden",
              "after:bg-border after:absolute after:inset-y-0 after:left-[2px] after:w-px after:transition-colors",
              "hover:after:bg-kith/50 focus-visible:after:bg-kith focus-visible:outline-none",
              dragging && "after:bg-kith/70",
            )}
          />

          <aside
            aria-label="Beside the chat"
            className={cn(
              "flex min-h-0 flex-col overflow-hidden",
              // Between the app's ground and the sidebar's, in both themes — the design's companion
              // tone, made of the two tokens it sits between rather than a colour of its own.
              "bg-[color-mix(in_oklab,var(--sidebar)_60%,var(--background))]",
              full ? "w-full flex-1" : "shrink-0",
            )}
            style={full ? undefined : { width }}
          >
            {bound ? (
              <LiveRow {...row} conversationId={conversationId ?? ""} />
            ) : (
              <TabRow {...row} />
            )}
            <div
              role="tabpanel"
              aria-label={tabs.find((one) => one.panel && one.panel.key === shown?.key)?.label ?? shown?.title}
              className="min-h-0 flex-1 overflow-hidden"
            >
              {/* Only the tab on screen is mounted. A hidden Browser is then not merely covered but
                  gone — `PluginWebView` hides the native view on unmount, which is the one thing a
                  page painted over the whole window must never miss — and a hidden Context detail
                  is not left listening for Escape while you type in the chat. */}
              {shown ? (
                <div key={shown.key} className="h-full min-h-0">
                  {shown.body}
                </div>
              ) : null}
            </div>
          </aside>
        </>
      )}
    </div>
  );
}

type RowProps = {
  tabs: Tab[];
  /** The plugin surfaces "+" can open here. */
  more: Tab[];
  onCloseTab: (key: string) => void;
  shown: string | null;
  expanded: boolean;
  onPick: (tab: Tab) => void;
  onExpand: () => void;
  onCloseAll: () => void;
  /** What the column is about, said only when it has the room — full width. */
  about?: string;
};

/** The last board opened in this window, for a chat's Canvas tab when the chat is on no task of
 *  its own. One slot for the window, because the board overlay is one for the window. */
let lastBoard: OpenBoard | null = null;
useBoardOverlay.subscribe((state) => {
  if (state.open) lastBoard = state.open;
});

/**
 * The row, with what is live in it and the two tabs that open a screen instead.
 *
 * Split from `TabRow` because this is the half that reads the server. A column with no chat to
 * belong to has nothing to ask about, and rendering one — a test's, say — should not need a
 * query client and a router to draw five buttons.
 */
function LiveRow({
  conversationId,
  ...row
}: RowProps & { conversationId: string }) {
  const cache = useQueryClient();
  const navigate = useNavigate();
  const showBoard = useBoardOverlay((state) => state.show);

  // Every one of these is a key something else on screen already holds — the Activity cards, the
  // header, the pane — so the row adds a subscriber to each, not a request.
  const { data: board = [] } = useQuery({
    queryKey: keys.workflows(conversationId),
    queryFn: () => fetchRunBoard(conversationId),
  });
  const { data: liveTurns = [] } = useQuery({ queryKey: keys.liveTurns(), queryFn: fetchLiveTurns });
  const { data: processes = [] } = useQuery({
    queryKey: keys.processes(conversationId),
    queryFn: () => fetchProcesses(conversationId),
  });
  const { data: task = null } = useQuery({
    queryKey: keys.workingOn(conversationId),
    queryFn: () => fetchWorkingOn(conversationId),
  });
  // The chat's own record, for its title when the column is full width. A cache read only — see
  // the same lookup in `WorkPanel` for why this key must never be fetched from here.
  const { data: detail } = useQuery({
    queryKey: keys.conversation(conversationId),
    queryFn: () => fetchConversation(conversationId),
    enabled: false,
  });

  /* Two things only the live feed says. A run or an agent changing state, so the dot on Activity
   * follows a workflow even while the Activity tab — whose cards otherwise re-read the board —
   * is not the one mounted. And whether the turn in flight is driving the browser: a browser call
   * lights Browser until that turn ends. */
  const [using, setUsing] = useState<ReadonlySet<string>>(() => new Set());
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type !== "activity") return;
        const item = event.data as ActivityItem;
        if (item.conversation !== conversationId) return;
        if (item.kind === "workflow" || (item.kind === "errand" && item.errand?.state !== "step")) {
          void cache.invalidateQueries({ queryKey: keys.workflows(conversationId) });
        }
        // Which plugins the turn in flight is using: a call to `plugin__<id>__…` lights that
        // plugin's tab until the turn ends. Any plugin, not just the browser.
        const plugin = item.kind === "tool" ? /^plugin__(.+?)__/.exec(item.tool ?? "")?.[1] : undefined;
        if (plugin) setUsing((was) => (was.has(plugin) ? was : new Set([...was, plugin])));
        if (item.kind === "done") setUsing((was) => (was.size ? new Set() : was));
      }),
    [cache, conversationId],
  );

  const turnLive = liveTurns.includes(conversationId);
  const activity: Live = board.some((one) => one.state === "running")
    ? "workflow"
    : turnLive || processes.some((one) => one.alive)
      ? "working"
      : null;

  const canvas: OpenBoard | null = task
    ? { taskId: task.id, projectId: null, title: task.goal }
    : lastBoard;

  const tabs = row.tabs.map((tab): Tab => {
    switch (tab.id) {
      case "activity":
        return { ...tab, live: activity };
      case "canvas":
        return canvas
          ? {
              ...tab,
              // `projectId: null` so the overlay anchors the board to the *task's* project once
              // it has read the task — a write against a guessed project makes a second board file.
              act: () => showBoard(canvas),
              title: `Open the canvas for ${canvas.title ?? `task ${canvas.taskId}`}`,
            }
          : { ...tab, disabled: true, title: "No designs to show — the canvas opens on a task with designs" };
      case "files":
        return {
          ...tab,
          act: () => navigate(pathForTab("workspace")),
          title: "Open his workspace files",
        };
      default:
        return tab.plugin ? { ...tab, live: using.has(tab.plugin) && turnLive ? "working" : null } : tab;
    }
  });

  return <TabRow {...row} tabs={tabs} about={detail?.title || undefined} />;
}

function TabRow({ tabs, more, onCloseTab, shown, expanded, onPick, onExpand, onCloseAll, about }: RowProps) {
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    if (!adding) return;
    const close = () => setAdding(false);
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && close();
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [adding]);
  const list = useRef<HTMLDivElement | null>(null);
  const label = tabs.find((one) => one.panel && one.panel.key === shown)?.label ?? "the column";

  /* Arrow keys move between tabs, the way a tab list is expected to behave. Focus only: picking
   * stays a click or Enter, because Browser and Canvas do real work when picked. */
  const arrows = (event: React.KeyboardEvent) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const buttons = [...(list.current?.querySelectorAll<HTMLButtonElement>("[role=tab]") ?? [])];
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0) return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    buttons[(at + step + buttons.length) % buttons.length]?.focus();
  };

  return (
    <div className="flex h-11 shrink-0 items-center gap-0.5 border-b border-foreground/7 px-2">
      <div
        ref={list}
        role="tablist"
        aria-label="Beside the chat"
        onKeyDown={arrows}
        className="flex min-w-0 items-center gap-0.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {tabs.map((tab) => {
          const selected = !!tab.panel && tab.panel.key === shown;
          const inert = tab.disabled || (!tab.panel && !tab.attach && !tab.act);
          const button = (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-disabled={inert || undefined}
              title={tab.title}
              onClick={() => {
                if (!inert) onPick(tab);
              }}
              className={cn(
                "flex h-[30px] shrink-0 items-center gap-1.5 rounded-lg px-3 text-[12px] whitespace-nowrap transition-colors",
                "focus-visible:ring-kith/60 focus-visible:ring-2 focus-visible:outline-none",
                selected
                  ? "bg-accent text-foreground font-semibold dark:bg-muted"
                  : "text-muted-foreground",
                !selected && !inert && "hover:bg-accent/60 hover:text-foreground cursor-pointer",
                inert && "cursor-default opacity-50",
                tab.closable && "pe-7",
              )}
            >
              {tab.label}
              {tab.live ? (
                <span
                  role="img"
                  aria-label={tab.live === "workflow" ? "a workflow is running" : "live"}
                  className={cn(
                    "size-1.5 shrink-0 rounded-full",
                    tab.live === "workflow" ? "bg-teal-600 dark:bg-teal-400" : "bg-roam",
                  )}
                />
              ) : null}
            </button>
          );
          // A plugin's tab closes on its own. The × is a sibling laid over the tab's end, not a
          // button inside a button, which a screen reader cannot tell apart from the tab.
          return tab.closable && tab.panel ? (
            <div key={tab.id} className="group/tab relative flex shrink-0 items-center">
              {button}
              <button
                type="button"
                aria-label={`Close ${tab.label}`}
                title={`Close ${tab.label}`}
                onClick={() => tab.panel && onCloseTab(tab.panel.key)}
                className="text-muted-foreground/70 hover:text-foreground hover:bg-foreground/10 focus-visible:ring-kith/60 absolute end-1.5 flex size-5 items-center justify-center rounded focus-visible:ring-2 focus-visible:outline-none"
              >
                <X className="size-3" aria-hidden />
              </button>
            </div>
          ) : (
            button
          );
        })}
      </div>

      {more.length ? (
        <div className="relative shrink-0">
          <button
            type="button"
            aria-label="Open a plugin here"
            aria-haspopup="menu"
            aria-expanded={adding}
            title="Open a plugin here"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => setAdding((was) => !was)}
            className={cn(
              "text-muted-foreground/80 hover:text-foreground hover:bg-accent/60 flex size-7 items-center justify-center rounded-[7px] transition-colors",
              adding && "bg-accent text-foreground",
            )}
          >
            <Plus className="size-3.5" aria-hidden />
          </button>
          {adding ? (
            <div
              role="menu"
              onPointerDown={(event) => event.stopPropagation()}
              className="border-border bg-popover text-popover-foreground absolute start-0 top-full z-50 mt-1.5 flex min-w-44 flex-col rounded-xl border p-1 shadow-xl"
            >
              <span className="text-muted-foreground/75 px-2.5 pt-1.5 pb-1 text-[10px] font-semibold tracking-[0.12em] uppercase">
                Plugins
              </span>
              {more.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAdding(false);
                    onPick(tab);
                  }}
                  className="hover:bg-accent/60 flex h-8 items-center rounded-lg px-2.5 text-start text-[12.5px]"
                >
                  {tab.label}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {expanded && about ? (
        <span className="text-muted-foreground/80 ms-3 min-w-0 truncate border-s border-foreground/9 ps-3 text-[12px]">
          of <span className="text-muted-foreground">{about}</span>
        </span>
      ) : null}
      <span className="min-w-2 flex-1" />
      {expanded ? (
        <span className="text-muted-foreground/80 me-1 hidden shrink-0 text-[11px] sm:inline">
          Esc puts it back
        </span>
      ) : null}
      <button
        type="button"
        onClick={onExpand}
        aria-label={expanded ? "Put it back beside the chat" : `Open ${label} full width`}
        aria-pressed={expanded}
        title={expanded ? "Put it back beside the chat (Esc)" : `Open ${label} full width`}
        className="text-muted-foreground/80 hover:text-foreground hover:bg-accent focus-visible:ring-kith/60 flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-[7px] transition-colors focus-visible:ring-2 focus-visible:outline-none"
      >
        {expanded ? <Minimize2 className="size-[13px]" /> : <Maximize2 className="size-[13px]" />}
      </button>
      {expanded ? (
        <button
          type="button"
          onClick={onCloseAll}
          aria-label="Close the column"
          title="Close the column"
          className="text-muted-foreground/80 hover:text-foreground hover:bg-accent focus-visible:ring-kith/60 flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-[7px] transition-colors focus-visible:ring-2 focus-visible:outline-none"
        >
          <X className="size-3.5" />
        </button>
      ) : null}
    </div>
  );
}
