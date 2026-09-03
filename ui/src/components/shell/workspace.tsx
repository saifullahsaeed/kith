import { Suspense, lazy, useCallback, useEffect, useMemo, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { TooltipProvider } from "@/components/ui/tooltip";
import { AppHeader } from "@/components/shell/app-header";
import { WorkspaceFileViewer } from "@/components/files/workspace-file-viewer";

import { ChatPane } from "@/components/chat/chat-pane";
import { WorkPanel } from "@/components/chat/work-panel";
import { HistoryPanel } from "@/components/chat/history-panel";
import { LayoutView } from "@/components/shell/layout/layout-view";
import { useLayout } from "@/components/shell/layout/store";
import {
  hasTab as treeHasTab,
  panes as panesOf,
  tabKey as tabKeyOf,
  type TabRef,
} from "@/components/shell/layout/tree";
import { DropZone } from "@/components/shell/drop-zone";
import { ErrorBoundary } from "@/components/shell/error-boundary";
import { useActivity } from "@/hooks/use-activity";
import { useMessages } from "@/hooks/use-messages";
import { keys } from "@/lib/query-keys";
import {
  parseLocation,
  pathForMessages,
  pathForSettings,
  pathForTab,
  pathForTask,
} from "@/lib/router";
import {
  fetchConversation,
  fetchLiveTurns,
  patchServerConfig,
  type ServerConfig,
} from "@/lib/backend";

/* The four screens that are not the chat, fetched when one is opened rather than before the
 * chat can paint.
 *
 * Every one of them is already rendered conditionally, so this costs nothing at the point of
 * use — it only stops them riding in the entry chunk, which is what the browser has to parse
 * before the first message appears. The Control Panel alone pulls in the whole board: the
 * roadmap graph, the file browser, the memory list.
 *
 * `WorkspaceFileViewer` above is deliberately not among them. It is mounted unconditionally
 * and renders nothing until a file is opened, so lazying it would trade a parse it already
 * does cheaply for a suspense boundary around the app's most common overlay. */
const ControlPanel = lazy(() =>
  import("@/components/control-panel").then((m) => ({ default: m.ControlPanel })),
);
const SettingsPage = lazy(() =>
  import("@/components/settings/settings-page").then((m) => ({ default: m.SettingsPage })),
);
const ContextDetailScreen = lazy(() =>
  import("@/components/chat/context-detail").then((m) => ({ default: m.ContextDetailScreen })),
);
const InboxPanel = lazy(() =>
  import("@/components/chat/inbox-panel").then((m) => ({ default: m.InboxPanel })),
);

/** Where the open conversation is remembered across a reload.
 *
 * Without it a refresh dropped you on a blank chat with your conversation one click away in
 * a panel that was also closed — so the app forgot what you were doing every time it
 * reloaded, which is the one thing a window is supposed to be good at. */
const LAST_CONVERSATION = "kith-conversation";



/** The ready-state app: chat runtime, header, and the activity ("Work") panel.
 * Split out so its hooks only run once the backend is reachable. */
export function Workspace({
  config,
  onSaveConfig,
  onConnectionSaved,
}: {
  config: ServerConfig;
  onSaveConfig: (config: ServerConfig) => void;
  /** Called after the provider/model is saved, so the header stops showing the old one. */
  onConnectionSaved: () => void;
}) {
  // The Control Panel (and which tab/task is open) lives in the URL, so deep
  // links, refresh, and back/forward all work.
  const location = useLocation();
  const navigate = useNavigate();
  const route = parseLocation(location.pathname);
  const panelOpen = route.panelOpen;
  // A notification with nothing narrower to point at (a reach-out, a stall, a session
  // resting itself) links here — so it has to be a real URL, the same way the task panel
  // and settings already are, not state a click sets and a notification has no way to reach.
  const inboxOpen = route.inboxOpen;

  /** Closing a surface from inside it — its own header X — is closing its tab. Two ways to
   *  shut the same thing that disagreed would be worse than one. */
  const closeTab = useLayout((state) => state.close);
  const openSurface = useLayout((state) => state.open);
  const layoutTree = useLayout((state) => state.tree);
  /** Whether a surface is on screen anywhere, for the header's pressed states. Read from the
   *  tree rather than from a flag beside it, so the button cannot disagree with the layout. */
  const hasTab = useCallback((key: string) => treeHasTab(layoutTree, key), [layoutTree]);
  const focusedPane = useLayout((state) => state.focused);

  /** The header's buttons are toggles: pressing one twice puts the surface away again. */
  const toggleSurface = useCallback(
    (surface: "conversations" | "work" | "board" | "settings") => {
      if (treeHasTab(layoutTree, surface)) closeTab(surface);
      else openSurface({ surface });
    },
    [closeTab, layoutTree, openSurface],
  );



  /* Everything about *a* conversation moved into `ChatPane`, one instance per chat tab.
   *
   * What used to be here was a single session: one runtime, one `resumed` array, one timeline
   * window, one scroll anchor, one `openConversation` that swapped all of it under a thread
   * that might still be streaming. That swap is gone rather than made safe — a runtime that
   * only ever holds one conversation has nothing to swap — and with it the cancel-then-wait
   * dance, the composer clearing, and the open-sequence counter that stopped a slow click
   * landing on top of a fast one. None of the three has anything to guard any more.
   *
   * What is left here is the part that was never about one conversation: which chat is in
   * front of you, and how a chat gets opened at all.
   */
  const cache = useQueryClient();

  /** A project picked for a chat that does not exist yet — "New chat here" from the sidebar.
   *  Read once by the pane that mounts next; the pane owns it from then on. */
  const draftProject = useRef<number | null>(null);

  /** Open a conversation: a tab, focused if it is already somewhere. */
  const openConversation = useCallback(
    (id: string) => {
      draftProject.current = null;
      openSurface({ surface: "chat", conversationId: id });
      remember(id);
    },
    [openSurface],
  );

  /** A fresh chat. Its tab is keyed on nothing until the first turn names it, which is why
   *  there can only be one draft open at a time — a second would collide with the first. */
  const newConversation = useCallback(
    (project: number | null = null) => {
      draftProject.current = project;
      openSurface({ surface: "chat", conversationId: "" });
      remember("");
    },
    [openSurface],
  );

  /** Which chat you are looking at, for the surfaces that are *about* a conversation without
   *  being one: Work, the context breakdown, and the header's "he is working" light.
   *
   *  The focused pane's active tab when that is a chat, and otherwise the first chat anywhere
   *  in the layout — so closing the focused pane does not leave Work pointed at nothing. */
  const conversationId = useMemo(() => {
    const all = panesOf(layoutTree);
    const here = all.find((one) => one.id === focusedPane)?.tabs[
      all.find((one) => one.id === focusedPane)?.active ?? 0
    ];
    if (here?.surface === "chat") return here.conversationId ?? "";
    const anywhere = all.flatMap((one) => one.tabs).find((tab) => tab.surface === "chat");
    return anywhere?.surface === "chat" ? (anywhere.conversationId ?? "") : "";
  }, [focusedPane, layoutTree]);

  // Reopen where you were. A reload used to land on an empty chat with the conversation you
  // were mid-way through two clicks away, looking gone.
  useEffect(() => {
    if (route.conversationId) return;
    let stored = "";
    try {
      stored = localStorage.getItem(LAST_CONVERSATION) || "";
    } catch {
      /* private mode, or no storage — a fresh chat is a fine answer */
    }
    if (stored) openConversation(stored);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* A notification that lands you in the chat it is about. `/chat/<id>` is the one route that
     could not be expressed before: a question he is waiting on lives in exactly one
     conversation, and an alert that drops you into whichever chat you last had open has not
     finished its job. */
  useEffect(() => {
    if (route.conversationId) openConversation(route.conversationId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.conversationId]);

  const activity = useActivity();
  const inbox = useMessages();

  /* A route names what you are looking at; the layout decides where it goes.
   *
   * These five screens used to cover the app, so "open settings" and "put settings on screen"
   * were the same act. They are panes now, and a deep link — a notification, a refresh, a
   * back button — has to *dock* the thing it names rather than replace what is there. So the
   * URL still carries what it always carried and every one of those links still works; it
   * opens a tab instead of a screen.
   *
   * Focus-or-open rather than open: `openTab` finds an existing tab and brings it forward, so
   * a second notification about the board does not stack a second board. */
  useEffect(() => {
    if (route.settingsTab) openSurface({ surface: "settings" });
  }, [openSurface, route.settingsTab]);
  useEffect(() => {
    if (panelOpen) openSurface({ surface: "board" });
  }, [openSurface, panelOpen]);
  useEffect(() => {
    if (inboxOpen) openSurface({ surface: "inbox" });
  }, [openSurface, inboxOpen]);
  useEffect(() => {
    if (route.contextOpen) openSurface({ surface: "context" });
  }, [openSurface, route.contextOpen]);

  /* A notification that lands you in the chat it is about.

     A question he is waiting on lives in exactly one conversation, and the answer can only be
     given there — so "he needs you" that drops you into whichever chat you last had open is a
     notification that has not finished its job. `/chat/<id>` is the one route that could not be
     expressed before.

     Guarded on already being there, or every render would reopen it and throw away the thread
     you are reading. */
  useEffect(() => {
    const wanted = route.conversationId;
    if (!wanted || wanted === conversationId) return;
    void openConversation(wanted);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.conversationId]);

  /* The three thresholds that used to live here are gone.
   *
   * `CHAT_FLOOR`, `WORK_YIELDS_BELOW` and `HISTORY_YIELDS_BELOW` encoded one intent — do not
   * squeeze prose to three words a line — as a hand-tuned order in which two named panels gave
   * way to a third, plus a `squeezed` flag held apart from `workOpen` so that narrowing the
   * window did not overwrite your choice, plus an overlay mode for the conversation list, plus
   * a pointer-driven divider that remembered its width in `localStorage`. All of it answered a
   * question the layout now answers structurally: every surface declares a `minWidth`, the
   * panel library enforces it, and there is no order of yielding to maintain because there is
   * no fixed set of three panels to order.
   *
   * See `layout/surfaces.ts`, where the same measured numbers survive as one field each. */

  // Clicking a desktop notification lands here. The shell dispatches this rather than
  // reloading the window, because a notification arriving mid-reply should not cost you the
  // reply — and `__kithRouter` is how it knows an in-page route is possible at all.
  useEffect(() => {
    (window as unknown as { __kithRouter?: boolean }).__kithRouter = true;
    const onNavigate = (event: Event) => {
      const path = (event as CustomEvent<string>).detail;
      if (typeof path === "string" && path.startsWith("/")) navigate(path);
    };
    window.addEventListener("kith:navigate", onNavigate);
    return () => {
      (window as unknown as { __kithRouter?: boolean }).__kithRouter = false;
      window.removeEventListener("kith:navigate", onNavigate);
    };
  }, [navigate]);

  /* Whether he is working, and where — which this could not honestly say until now.
   *
   * It was `const working = false`, with a comment explaining that the green wash and the session
   * bar were switched off rather than lying, waiting on "the reliability work, which gives them one
   * honest meaning: a session is busy if and only if a turn is live in it". That meaning exists now:
   * `live_turns.live()` is the set of conversations with a turn actually running, `turn` events
   * invalidate it the moment one starts or finishes, and the stream those events arrive on is
   * resumable — so a missed one costs a beat rather than a wrong screen.
   *
   * Both halves are wanted and they are different. `working` is about the conversation in front of
   * you, and drives the wash and the orb. `elsewhere` is the one this app was missing entirely: a
   * turn carrying on in a chat you are not looking at, which until now announced itself only as a
   * 6px dot inside a panel you had to have open to see. */
  const { data: live = [] } = useQuery({
    queryKey: keys.liveTurns(),
    queryFn: fetchLiveTurns,
  });
  const working = conversationId !== "" && live.includes(conversationId);
  const elsewhere = live.filter((id) => id !== conversationId);
  // The room glows green while he is working, and is otherwise his own amber.
  const wash = working ? "var(--roam)" : "var(--kith)";

  /* Which element a tab shows.
   *
   * The layout module is handed this rather than importing the surfaces itself, so it never
   * pulls the Thread or the board into its own chunk, and so every prop these components need
   * stays here with the state that produces it.
   *
   * One `ErrorBoundary` per surface, kept from the fixed layout: a pane that throws takes only
   * itself down, and the chat surviving a broken roadmap graph is the difference between "one
   * thing is wrong" and "Kith is down". */
  const renderSurface = useCallback(
    (ref: TabRef) => {
      switch (ref.surface) {
        case "conversations":
          return (
            <ErrorBoundary where="Conversations" compact>
              <HistoryPanel
                activeId={conversationId}
                onOpen={(id) => void openConversation(id)}
                onNew={(project) => newConversation(project ?? null)}
                onClose={() => closeTab("conversations")}
              />
            </ErrorBoundary>
          );

        case "chat":
          /* Keyed on the conversation, which is what makes the tabs real.
           *
           * A pane's conversation never changes, so React tearing one down and building
           * another is exactly right when the key changes — and there is no key change in the
           * ordinary life of a tab, because a draft chat learning its id renames its *tab*
           * rather than being replaced. */
          return (
            <ChatPane
              key={ref.conversationId || "draft"}
              conversationId={ref.conversationId ?? ""}
              initialProject={draftProject.current}
              active={(ref.conversationId ?? "") === conversationId}
            />
          );

        case "work":
          return (
            <ErrorBoundary where="Work" compact>
              <WorkPanel
                activity={activity}
                conversationId={conversationId}
                onClose={() => closeTab("work")}
              />
            </ErrorBoundary>
          );

        case "board":
          return (
            <ErrorBoundary where="The Control Panel">
              <Suspense fallback={<ScreenLoading />}>
                <ControlPanel
                  tab={route.tab}
                  openTask={route.taskId}
                  onSelectTab={(t) => navigate(pathForTab(t))}
                  onOpenTask={(id) => navigate(pathForTask(id))}
                  onClose={() => closeTab("board")}
                />
              </Suspense>
            </ErrorBoundary>
          );

        case "settings":
          return (
            <ErrorBoundary where="Settings">
              <Suspense fallback={<ScreenLoading />}>
                <SettingsPage
                  tab={route.settingsTab ?? "model"}
                  config={config}
                  onSelectTab={(t) => navigate(pathForSettings(t))}
                  onSaveConfig={onSaveConfig}
                  onConnectionSaved={onConnectionSaved}
                  onClose={() => closeTab("settings")}
                />
              </Suspense>
            </ErrorBoundary>
          );

        case "inbox":
          return (
            <Suspense fallback={<ScreenLoading />}>
              <InboxPanel inbox={inbox} onClose={() => closeTab("inbox")} />
            </Suspense>
          );

        case "context":
          return (
            <ErrorBoundary where="The context breakdown">
              <Suspense fallback={<ScreenLoading />}>
                <ContextDetailScreen
                  conversationId={conversationId}
                  onClose={() => closeTab("context")}
                />
              </Suspense>
            </ErrorBoundary>
          );
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      activity,
      closeTab,
      config,
      conversationId,
      inbox,
      newConversation,
      openConversation,
      route.settingsTab,
      route.tab,
      route.taskId,
    ],
  );

  /** The project the focused chat is bound to.
   *
   * The file viewer needs it: the paths it is handed are mostly relative ones out of his prose
   * and his tool results, and a relative path written during a project turn is relative *to
   * that project*. Without it the viewer asks the global workspace root and reports a file
   * missing that was never missing.
   *
   * Read from the cache the focused pane's own fetch already filled, rather than tracked
   * alongside it — one fact, one owner. `useQuery` on the same key rather than a bare
   * `getQueryData`, so this re-renders when that fetch lands instead of showing null until
   * something else happens to move. */
  const { data: focusedDetail } = useQuery({
    queryKey: keys.conversation(conversationId),
    queryFn: () => fetchConversation(conversationId),
    enabled: !!conversationId,
  });
  const projectId = focusedDetail?.projectId ?? null;

  /** A chat tab is named by its conversation; everything else by the surface registry. */
  const titleForTab = useCallback(
    (ref: TabRef) => {
      if (ref.surface !== "chat" || !ref.conversationId) return undefined;
      /* Straight out of the query cache rather than a fetch of its own. Opening the
       * conversation already put its detail there, so naming its tab is free — and a tab that
       * had to request a title would put one request per tab on every reload. */
      const held = cache.getQueryData<{ title?: string }>(keys.conversation(ref.conversationId));
      return held?.title;
    },
    [cache],
  );

  return (
    <TooltipProvider>
      {/* No runtime provider here any more. There is one per chat pane, inside `ChatPane`,
          which is what lets two conversations stream at once. */}
      <>
        <div className="relative flex h-dvh flex-col overflow-hidden text-foreground">
          {/* Ambient wash — leans green while he works, amber while he's here. */}
          <div
            className="kith-ambient"
            style={{ ["--wash" as string]: wash }}
          />
          <div className="relative z-10 flex min-h-0 flex-1 flex-col">
            <AppHeader
              working={working}
              status={null}
              model={config.model}
              effort={config.effort}
              // Offered unless the provider has said otherwise: an unknown model is the
              // normal case right after a switch, and the transport retries a reasoning 400.
              supportsEffort={
                !config.capabilities?.known || config.capabilities.reasoning
              }
              onEffort={(effort) => {
                onSaveConfig({ ...config, effort });
                void patchServerConfig({ effort });
              }}
              historyOpen={hasTab("conversations")}
              onOpenHistory={() => toggleSurface("conversations")}
              onNewConversation={() => newConversation()}
              elsewhere={elsewhere}
              onGoToWorking={(id) => {
                // One: go straight to it, which is the whole point of knowing where. Several: open
                // the list, because picking is the question and the panel is where it is answered.
                if (id) void openConversation(id);
                else openSurface({ surface: "conversations" });
              }}
              unread={inbox.unread}
              workOpen={hasTab("work")}
              onOpenInbox={() => {
                inbox.enableNotifications();
                openSurface({ surface: "inbox" });
                navigate(pathForMessages());
              }}
              onOpenWork={() => toggleSurface("work")}
              onOpenPanel={() => {
                openSurface({ surface: "board" });
                navigate(pathForTab(route.tab ?? "overview"));
              }}
              onOpenSettings={() => navigate(pathForSettings())}
            />
            <LayoutView render={renderSurface} titleFor={titleForTab} />
          </div>
        </div>
        {/* Drop a file anywhere in the window and it lands on the composer. Disabled — but
            still swallowing the drop — while something is covering the thread, since attaching
            to a composer nobody can see is a file that has vanished. */}
        {/* Enabled only while a chat is on screen. It used to be disabled whenever one of the
            five takeover screens covered the thread; there are no takeovers now, so the
            question is the one it was always really asking — is there a composer for this file
            to land on. A drop with the chat tab closed would attach to something nobody can
            see, which is a file that has vanished. */}
        <DropZone enabled={hasTab(tabKeyOf({ surface: "chat", conversationId }))} />
        {/* One viewer for the whole app — a path in a message, a deliverable, and the
            file browser all open this. Given this session's project, because the paths it is
            handed are mostly relative ones out of his prose and his tool results, and a
            relative path he wrote during a project turn is relative *to that project*. Without
            it the viewer asked the global workspace root and got "there's no
            .kith/work/task-76.md" for a file that was never missing. */}
        <WorkspaceFileViewer projectId={projectId} />
      </>
    </TooltipProvider>
  );
}

/** The moment between opening a screen and its chunk arriving.
 *
 * A full-bleed backdrop rather than a spinner in the corner: these screens cover the app, so
 * anything smaller reads as the click having missed. On a warm cache it is one frame. */
function ScreenLoading() {
  return (
    <div className="bg-background/80 fixed inset-0 z-40 backdrop-blur-[2px]" role="status">
      <span className="sr-only">Loading</span>
    </div>
  );
}

/** Keep (or clear) the conversation to reopen on the next load. */
function remember(conversationId: string): void {
  try {
    if (conversationId) localStorage.setItem(LAST_CONVERSATION, conversationId);
    else localStorage.removeItem(LAST_CONVERSATION);
  } catch {
    /* ignore */
  }
}
