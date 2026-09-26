import { X } from "lucide-react";
import { BoardOverlay } from "@/components/design/board-overlay";
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { TooltipProvider } from "@/components/ui/tooltip";
import { AppHeader, NewTabButton } from "@/components/shell/app-header";
import {
  chatState,
  fetchAllWorkflows,
  permissionHolder,
  presenceFor,
  runningWorkflows,
  type ChatFacts,
} from "@/components/shell/chat-states";
import { WorkspaceFileViewer } from "@/components/files/workspace-file-viewer";
import { PluginSurface } from "@/components/shell/plugin-surface";
import { PluginWebView } from "@/components/shell/plugin-web-view";
import { Sidebar, useSidebar } from "@/components/shell/sidebar";
import { withCompanions } from "@/components/shell/companions";
import { useHostEffects } from "@/components/shell/use-host-effects";

import { AgentMapHost } from "@/components/chat/agent-map";
import { ChatPane } from "@/components/chat/chat-pane";
import { WorkPanel } from "@/components/chat/work-panel";
import { HistoryPanel } from "@/components/chat/history-panel";
import { LayoutView } from "@/components/shell/layout/layout-view";
import { useLayout } from "@/components/shell/layout/store";
import { surfaceFor } from "@/components/shell/layout/surfaces";
import { useCanAttach } from "@/lib/active-composer";
import { useAgentMap } from "@/lib/agent-map";
import { useDrafts, useDraftedTabs } from "@/lib/drafts";
import {
  BOUND,
  hasTab as treeHasTab,
  panes as panesOf,
  tabKey,
  type TabRef,
} from "@/components/shell/layout/tree";
import { DropZone } from "@/components/shell/drop-zone";
import { ErrorBoundary } from "@/components/shell/error-boundary";
import { useActivity } from "@/hooks/use-activity";
import { useMessages } from "@/hooks/use-messages";
import { usePermissions } from "@/hooks/use-permissions";
import { pluginSurface, setPluginSurfaces } from "@/lib/plugin-index";
import { keys } from "@/lib/query-keys";
import {
  parseLocation,
  pathForHome,
  pathForMessages,
  pathForSettings,
  pathForTab,
  pathForTask,
} from "@/lib/router";
import {
  fetchConversation,
  fetchConversations,
  fetchLiveTurns,
  fetchPluginSurfaces,
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

/** The workflow board for every conversation at once, for the tabs. Under `workflows` so a blanket
 *  invalidation of that prefix reaches it, and not `keys.workflows("")` — that is the Work panel's
 *  key for a chat with no conversation yet, which must stay empty rather than share this list. */
const EVERY_WORKFLOW = [...keys.workflows(""), "every conversation"] as const;

/** How many conversations to know the titles of. Generous, because the cost is one query the
 *  sidebar is running anyway, and a tab whose name is missing is a tab you cannot identify. */
const TITLE_LIMIT = 200;



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
  /* The rail's own state, deliberately not in the layout store: it is furniture, not part of
   * the arrangement, and it must survive the tree's version bumps rather than reset with them. */
  const sidebar = useSidebar();

  const closeLayoutTab = useLayout((state) => state.close);
  const openSurface = useLayout((state) => state.open);
  const layoutTree = useLayout((state) => state.tree);
  const companions = useLayout((state) => state.companions);
  const attach = useLayout((state) => state.attach);
  const detach = useLayout((state) => state.detach);
  /** Whether a surface is on screen anywhere, for the header's pressed states. Read from the
   *  tree rather than from a flag beside it, so the button cannot disagree with the layout. */
  const hasTab = useCallback((key: string) => treeHasTab(layoutTree, key), [layoutTree]);
  const focusedPane = useLayout((state) => state.focused);
  /** Whether a composer is mounted and claiming drops — see `lib/active-composer`. */
  const canAttach = useCanAttach();

  /* Closing a surface the URL still names has to clear the URL too.
   *
   * Four of these are reachable by route — Settings, the board, the inbox, the context
   * breakdown — and an effect above reopens whatever the route names. So closing one by its X
   * shut the tab and left `/settings/model` in the address bar: the effect had already run, so
   * nothing reopened it, but the header button was now a no-op because navigating to the route
   * it was already on fires nothing. It looked like the button had broken.
   *
   * Before this the takeover screens were rendered *off* the route, so closing them navigated
   * home by construction and the question could not arise. */
  const closeTab = useCallback(
    (key: string) => {
      closeLayoutTab(key);
      // `settings` and `board` are gone from here: they are not tabs any more, so closing one is
      // a navigation and nothing else — see the block near the foot of this file.
      if (key === "context" && route.contextOpen) navigate(pathForHome());
    },
    [closeLayoutTab, navigate, route.contextOpen],
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
  /* The last chat that was actually in front of you, remembered.
   *
   * The fallback used to be "the first chat anywhere in the tree", and that is not a harmless
   * default: `PaneView` focuses a pane on the mousedown of *any* click inside it, so one click
   * in the Work pane re-elected a different conversation — and the Fold button in that very
   * panel then folded it. A fold is appended to the transcript and there is no unfold anywhere
   * in the app or the server, so a stray click permanently changed what the model replays for
   * a conversation nobody had looked at.
   *
   * Sticky instead: a click outside a chat leaves the answer where it was. It only changes when
   * a chat is genuinely focused, or when the one being pointed at is no longer open. */
  const lastChat = useRef("");
  const conversationId = useMemo(() => {
    const all = panesOf(layoutTree);
    const pane = all.find((one) => one.id === focusedPane);
    const here = pane?.tabs[pane.active];
    if (here?.surface === "chat") {
      lastChat.current = here.conversationId ?? "";
      return lastChat.current;
    }
    const open = all.flatMap((one) => one.tabs).filter((tab) => tab.surface === "chat");
    if (open.some((tab) => (tab.conversationId ?? "") === lastChat.current)) {
      return lastChat.current;
    }
    // The one it was pointing at has been closed. Anything still open beats nothing, and the
    // *visible* one beats a background tab.
    const visible = all
      .map((one) => one.tabs[one.active])
      .find((tab) => tab?.surface === "chat");
    lastChat.current = (visible ?? open[0])?.conversationId ?? "";
    return lastChat.current;
  }, [focusedPane, layoutTree]);

  /** The header's buttons are toggles: pressing one twice puts the surface away again.
   *
   * A surface that is *about* a conversation goes into that chat's own column rather than
   * beside it in the strip — that is what a companion is, and why Work no longer arrives as a
   * sibling tab of the conversation it describes. It needs a chat to belong to: with none in
   * front of you it falls back to a tab, which is the honest answer for "Work about what?".
   *
   * `board` and `settings` are about Kith rather than about a chat, so they stay tabs. */
  const toggleSurface = useCallback(
    (surface: "work" | "board" | "settings") => {
      if (BOUND.has(surface) && conversationId) {
        const hostKey = tabKey({ surface: "chat", conversationId });
        const held = companions[hostKey] ?? [];
        // The companion column is one thing with tabs now, so the button toggles the column:
        // anything open in it closes, and an empty column opens on Activity. Asking only about
        // Work left the Browser (opened by him mid-turn) showing while the button read "off".
        if (surface === "work" && held.length) {
          for (const one of held) detach(hostKey, tabKey(one));
          return;
        }
        const key = tabKey({ surface, conversationId });
        if (held.some((one) => tabKey(one) === key)) {
          detach(hostKey, key);
        } else {
          attach(hostKey, { surface, conversationId });
        }
        return;
      }

      const ref: TabRef = { surface };
      const key = tabKey(ref);
      if (treeHasTab(layoutTree, key)) closeTab(key);
      else openSurface(ref);
    },
    [attach, closeTab, companions, conversationId, detach, layoutTree, openSurface],
  );

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
    // `openTab` focuses an existing tab rather than duplicating it, so re-running is harmless
    // in effect — but each call still commits a tree and writes `localStorage`, and there were
    // two of these effects doing it. One, and only when the id actually changes.
    if (route.conversationId) openConversation(route.conversationId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.conversationId]);

  /* Effects a plugin asks Kith to perform — opening its own tab, so far. See `use-host-effects`
   * for why this is the renderer's job and the dead end it closes. */
  useHostEffects(conversationId, openSurface);

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
  /* Board and Settings are not here.
   *
   * They used to be opened as tabs by two effects like the one below, and they cover the window
   * — `fixed inset-0 z-30`, both — because their layouts do not fit a pane. So opening one added
   * a tab, the pane drew a strip and a pane-scoped loading veil while the lazy chunk arrived, and
   * *then* the thing took the screen: a tab flashing into existence and vanishing, every time.
   *
   * They are rendered from the route now, beside the alerts panel below, which has worked this
   * way since it stopped being a pane. `TAKEOVERS` in `layout/tree.ts` keeps them out of the
   * tree for good. */
  /* Both directions, not one. See `routed-surface.ts` for the whole story: this effect used to
   * be `if (route.contextOpen) openSurface(...)`, which opens on the *transition* of a boolean
   * derived from the pathname. `closeTab` clears the URL on the way out so the transition can
   * happen again — but `PaneMenu`'s "Close this pane" calls the layout store's `closePane`
   * directly and takes every tab in the pane with it, and `layout/` has nothing to tell the URL
   * with. So `/context` stayed in the address bar with no Context tab, the boolean stayed true,
   * and the expand button navigated to the route it was already on: no dependency change, no
   * effect, nothing on screen, for good. */



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
   * you, and drives the wash and the presence pill. The rest is a turn carrying on in a chat you
   * are not looking at, which once announced itself only as a 6px dot inside a panel you had to
   * have open to see — it is that chat's tab now, green and tagged WORKING (see `stateForTab`). */
  const { data: live = [] } = useQuery({
    queryKey: keys.liveTurns(),
    queryFn: fetchLiveTurns,
  });

  /* What plugin surfaces exist, kept current.
   *
   * The document already carries this inline, because the layout tree rehydrates at module
   * import and a fetch arrives too late to give a restored plugin tab its real width. This is
   * the other half: installing or switching off a plugin must not need a reload, and
   * `STALE_ON.plugin` invalidates this key. */
  const { data: plugins } = useQuery({
    queryKey: keys.pluginSurfaces(),
    queryFn: fetchPluginSurfaces,
    staleTime: 30_000,
  });
  useEffect(() => {
    if (plugins) setPluginSurfaces(plugins);
  }, [plugins]);
  const working = conversationId !== "" && live.includes(conversationId);

  /* Which chats are holding something you typed and did not send.
   *
   * The third per-chat signal, and it lives beside the other two so one module ranks them all.
   * Unlike them it never touches the server: a draft is local by nature, so nothing on a
   * conversation row from the API could ever carry it — which is why the strip is handed a
   * function and the conversations list reads the store itself.
   *
   * Shallow-compared inside `useDraftedTabs`, and that matters here: this component renders the
   * whole window, and a subscription that moved on every keystroke would re-render it per
   * character. */
  const drafted = useDraftedTabs();
  const markForTab = useCallback(
    (ref: TabRef) => (ref.uid && ref.uid in drafted ? ("draft" as const) : undefined),
    [drafted],
  );
  /* The header used to count these — "unsent text in 2 conversations" — beside a count of turns
   * running elsewhere. Both went when the tabs learned to say it themselves: the ring on a tab
   * and its WORKING tag are on the chat they are about, which a number in a corner was not. */

  /* Let go of drafts whose tab is gone.
   *
   * Keyed on the tab, a draft has to end when the tab does — otherwise a closed tab leaves a mark
   * on a conversation with nowhere to put the text back. Driven off the tree rather than hooked
   * onto each way a tab can close (the X, "close the others", a pane closing, a reset, a loaded
   * layout), because "does this tab still exist" has one answer and five ways to ask it. Running
   * on mount is deliberate too: it closes the window in which a draft could outlive its tab
   * across a reload.
   *
   * Here and not in the layout store's `commit` beside the `widths` prune, which is the obvious
   * place and the wrong one: widths are the layout's own measured facts, and `layout/` is the
   * module that refuses to know what a surface holds. */
  useEffect(() => {
    const tabs = new Set<string>();
    for (const pane of panesOf(layoutTree)) {
      for (const tab of pane.tabs) if (tab.surface === "chat" && tab.uid) tabs.add(tab.uid);
    }
    useDrafts.getState().keep(tabs);
  }, [layoutTree]);
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
  const surfaceBody = useCallback(
    (ref: TabRef, onClose: () => void) => {
      switch (ref.surface) {
        case "chat":
          /* No key here. `PaneView` keys the body on the tab's permanent `uid`, which is what
           * survives a draft chat being named — keying on the conversation unmounted the pane
           * mid-first-reply and threw away the runtime streaming into it.
           *
           * `initialProject` is only handed to a chat that has no conversation yet. It used to
           * go to every pane that mounted, read off a mutable ref cleared by one code path out
           * of several — so opening "New chat here" on project 7 and then clicking an existing
           * chat's tab re-bound *that* conversation to project 7 on the server. */
          return (
            <ChatPane
              conversationId={ref.conversationId ?? ""}
              initialProject={ref.conversationId ? null : draftProject.current}
              active={(ref.conversationId ?? "") === conversationId}
              /* Where this tab's unsent text is kept, so it survives the tab being switched
                 away from — which unmounts this pane. See `lib/drafts`. */
              uid={ref.uid}
            />
          );

        case "work":
          return (
            <ErrorBoundary where="Work" compact>
              {/* `ref.conversationId`, not the focused chat. This is the whole point of the
                  binding: the panel that can fold a transcript now acts on the conversation its
                  tab names, so a click elsewhere cannot re-aim it. The fallback covers a tab
                  stored before surfaces could be bound. */}
              <WorkPanel
                activity={activity}
                conversationId={ref.conversationId ?? conversationId}
              />
            </ErrorBoundary>
          );

        /* No `board` or `settings` case. They are not tabs — the route renders them over the
           whole window near the foot of this file, and `TAKEOVERS` keeps them out of the tree.
           A pane can never be asked for one, so answering here would be dead code that reads
           like a supported arrangement. */
        case "plugin":
          /* One arm, so the ErrorBoundary is structural rather than a per-case convention —
           * which matters, because two of the seven existing arms lack one despite the comment
           * above claiming otherwise. A plugin surface that throws costs its own pane.
           *
           * Two kinds inside it. A `document` surface is the sealed frame; a `web` surface is a
           * browser the Electron shell composites over the pane, which shares no code with the
           * frame at all — no ticket, no bridge, no store, because there is no plugin page in it
           * to talk to. Chosen here rather than inside `PluginSurface` so neither component
           * carries a branch for the other's whole mechanism. */
          return (
            <ErrorBoundary where={`${ref.plugin}/${ref.view}`} compact>
              {pluginSurface(ref.plugin, ref.view)?.kind === "web" ? (
                <PluginWebView
                  plugin={ref.plugin}
                  view={ref.view}
                  conversationId={ref.conversationId ?? conversationId}
                />
              ) : (
                <PluginSurface
                  plugin={ref.plugin}
                  view={ref.view}
                  instance={ref.instance}
                  conversationId={ref.conversationId ?? conversationId}
                />
              )}
            </ErrorBoundary>
          );

        case "context":
          return (
            <ErrorBoundary where="The context breakdown">
              <Suspense fallback={<ScreenLoading />}>
                <ContextDetailScreen
                  conversationId={ref.conversationId ?? conversationId}
                  onClose={onClose}
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

  /**
   * A tab, plus whatever it carries with it.
   *
   * A chat's companions are rendered *inside* its body — the same components a tab would get,
   * through the same switch, with `detach` where `closeTab` would be. So Work in a chat's
   * column and Work as a tab are the same panel; only where it sits differs, and neither
   * spelling needs the other to know about it.
   *
   * Only chats carry a column. A companion is a surface that is about a conversation, and a
   * Board with a Work panel bolted to its side would be a shape with nothing to mean.
   */
  const renderSurface = useCallback(
    (ref: TabRef) => {
      const host = surfaceBody(ref, () => closeTab(tabKey(ref)));
      const hostKey = tabKey(ref);
      /* No early return for a chat with nothing beside it, and that absence is load-bearing —
       * `withCompanions` says why at length. In short: returning the host itself here and a
       * `<Companions>` there changes the element *type* at this position the moment the first
       * panel opens, and React answers a changed type by unmounting everything below it. The chat
       * losing its runtime and refetching itself is what that looked like from a chair. */
      const held = ref.surface === "chat" ? (companions[hostKey] ?? []) : [];
      return withCompanions(
        ref,
        host,
        held.map((one) => {
          const key = tabKey(one);
          return {
            key,
            // The bare surface name: which conversation it is about is the tab it is sitting
            // in, so "Work — find me a domain…" here would be repeating the tab above it.
            title: surfaceFor(one).title,
            icon: surfaceFor(one).icon,
            body: surfaceBody(one, () => detach(hostKey, key)),
          };
        }),
        (key) => detach(hostKey, key),
      );
    },
    [closeTab, companions, detach, surfaceBody],
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
  /* Every conversation's title in one query — the same one the sidebar runs, so this is a
   * cache read rather than a request.
   *
   * It used to read each tab's title out of `keys.conversation(id)`, the *detail* a pane fetches
   * when it opens. That is empty on a reload and never filled for a background tab, so after a
   * reload three open chats read "New chat", "New chat" and one real name — two tabs claiming
   * to be new, neither of them new, and nothing to tell them apart. */
  const { data: known = [] } = useQuery({
    queryKey: keys.conversations(TITLE_LIMIT, ""),
    queryFn: async () => (await fetchConversations(TITLE_LIMIT)).conversations,
    staleTime: 30_000,
  });
  const titleForTab = useCallback(
    (ref: TabRef) => {
      // Any tab that names a conversation, not only a chat — `tabTitle` decides what to do
      // with it, and a bound Work tab wants the same string a chat tab would get.
      if (!ref.conversationId) return undefined;
      return known.find((one) => one.id === ref.conversationId)?.title;
    },
    [known],
  );

  /* What every chat is doing, for its tab and for the presence pill: working, waiting on you,
   * or running a workflow. Three sources, one ranking — see `chat-states.ts`.
   *
   * Workflows are read from the server's board for *every* conversation, and the live feed is
   * laid over it so a run that just started, or a phase that just began, shows at once. The
   * board is re-read when the feed's newest workflow line changes: its identity rather than a
   * count of such lines, because the feed is a ring and a count can stand still while it turns. */
  const cache = useQueryClient();
  const { pending } = usePermissions();
  const { data: board = [] } = useQuery({
    queryKey: EVERY_WORKFLOW,
    queryFn: fetchAllWorkflows,
    staleTime: 30_000,
  });
  const lastFlow = useMemo(() => {
    for (let at = activity.activity.length - 1; at >= 0; at -= 1) {
      const line = activity.activity[at];
      if (line?.workflow) return `${line.at}|${line.workflow.run}|${line.workflow.state}`;
    }
    return "";
  }, [activity.activity]);
  useEffect(() => {
    if (lastFlow) void cache.invalidateQueries({ queryKey: EVERY_WORKFLOW });
  }, [cache, lastFlow]);

  const facts = useMemo<ChatFacts>(() => {
    const waiting = new Set(known.filter((one) => one.waiting).map((one) => one.id));
    return {
      live: new Set(live),
      waiting,
      asking: permissionHolder(pending.length, live, waiting),
      workflows: runningWorkflows(board, activity.activity),
    };
  }, [activity.activity, board, known, live, pending.length]);
  const stateForTab = useCallback(
    (ref: TabRef) =>
      ref.surface === "chat" ? chatState(ref.conversationId ?? "", facts) : undefined,
    [facts],
  );

  /* When each live turn was first seen, for the pill's clock. The server says *that* a turn is
   * live and not since when, so this is the moment this window learned it — exact for a turn you
   * started here, and a lower bound for one already running when the window opened. */
  const [started, setStarted] = useState<Record<string, number>>({});
  useEffect(() => {
    setStarted((was) => {
      const now = Date.now();
      const next: Record<string, number> = {};
      for (const id of live) next[id] = was[id] ?? now;
      const same =
        Object.keys(next).length === Object.keys(was).length &&
        Object.keys(next).every((id) => was[id] === next[id]);
      return same ? was : next;
    });
  }, [live]);
  const presence = presenceFor(conversationId, facts, started[conversationId]);
  const showAgentMap = useAgentMap((s) => s.show);

  /** Whether the chat in front of you has its companion column open — any tab of it. */
  const companionOpen = conversationId
    ? (companions[tabKey({ surface: "chat", conversationId })] ?? []).length > 0
    : hasTab("work");

  /* Where the title bar's tab strip is drawn — a node the header renders and the layout portals
   * into. State rather than a ref, so the layout re-renders once the node exists. */
  const [tabSlot, setTabSlot] = useState<HTMLDivElement | null>(null);
  const tabBar = useMemo(
    () => ({ slot: tabSlot, after: <NewTabButton onClick={() => newConversation()} /> }),
    [newConversation, tabSlot],
  );

  return (
    <TooltipProvider>
      {/* No runtime provider here any more. There is one per chat pane, inside `ChatPane`,
          which is what lets two conversations stream at once. */}
      <>
        <div className="relative flex h-dvh flex-col overflow-hidden text-foreground">
          {/* Ambient wash — leans green while he works, amber while he's here. `fixed inset-0`
              because `.kith-ambient` no longer carries its own positioning, and this one really is
              window-scoped.
              `fixed` rather than `absolute` is load-bearing: `kith-drift` scales the element to
              1.05, and a scaled *absolute* box contributes its overflow to this shell's scroll
              width (13px, measured) where a fixed one contributes nothing. Both paint the room
              identically — one of them was just inflating a scroll width nothing can reach. */}
          <div
            className="kith-ambient fixed inset-0"
            style={{ ["--wash" as string]: wash }}
          />
          {/* The board, once, above every pane.
           *
           * It used to be mounted inside `ChatPane` and gated on that pane being the focused
           * one, which made "does the board appear" a question about tab bookkeeping. Switching
           * tabs *unmounts* a pane, so a board opened in one died when you looked at another;
           * and with two chats open the store is global, so the surviving pane would have drawn
           * the other conversation's board over itself. It shows half the time because half the
           * time its host is gone.
           *
           * It was never a property of a conversation. It is keyed by a task and belongs to the
           * window, so it lives here — above the panes, outside anything that unmounts when you
           * look somewhere else. */}
          <BoardOverlay />
          {/* The agent map, for the same reason: it is asked for from the title bar, the Work
              panel and the thread, and belongs to none of them. */}
          <AgentMapHost />
          <div className="relative z-10 flex min-h-0 flex-1 flex-col">
            <AppHeader
              sidebarOpen={sidebar.open}
              sidebarWidth={sidebar.width}
              onToggleSidebar={sidebar.toggle}
              onNewConversation={() => newConversation()}
              tabsSlot={setTabSlot}
              presence={presence}
              onPresence={
                /* The pill goes where it is talking about. A workflow opens its agent map — in
                   the chat running it, which is this one or, taken there first, another. A chat
                   that needs you is opened. A turn working here is a status, not a destination. */
                presence.kind === "workflow"
                  ? () => {
                      const home = presence.target || conversationId;
                      if (presence.target) openConversation(presence.target);
                      if (home) showAgentMap(home);
                    }
                  : "target" in presence && presence.target
                    ? () => openConversation(presence.target)
                    : undefined
              }
              unread={inbox.unread}
              onOpenInbox={() => {
                inbox.enableNotifications();
                // Toggle: the bell is the way in and the way back out, so pressing it twice
                // puts the alerts away the same as every other header control.
                navigate(inboxOpen ? pathForHome() : pathForMessages());
              }}
              companionOpen={companionOpen}
              onToggleCompanion={() => toggleSurface("work")}
              onOpenPanel={() => navigate(pathForTab(route.tab ?? "overview"))}
              onOpenSettings={() => navigate(pathForSettings())}
            />
            {/* The rail and the tree, side by side. The rail is furniture — outside the tree
                on purpose, so no drag, split or tab-close can take the list away. */}
            <div className="relative flex min-h-0 flex-1">
              <Sidebar
                open={sidebar.open}
                width={sidebar.width}
                onWidth={sidebar.setWidth}
              >
                <ErrorBoundary where="Conversations" compact>
                  <HistoryPanel
                    activeId={conversationId}
                    onOpen={(id) => void openConversation(id)}
                    onNew={(project) => newConversation(project ?? null)}
                  />
                </ErrorBoundary>
              </Sidebar>
              <LayoutView
                render={renderSurface}
                titleFor={titleForTab}
                markFor={markForTab}
                stateFor={stateForTab}
                bar={tabBar}
              />

              {/* Alerts, over the room rather than beside it.
                *
                * `Layer.Panel` has described this surface as one that "slides over the room but
                * leaves it visible" since the layering was written down; being a pane in the
                * tree was the thing that disagreed. A glance at what arrived while you were
                * away should not cost the chat a third of its width, and it should not be
                * something you can dock, split, or leave open behind another tab.
                *
                * The route is the only state: `/messages` is open, anything else is closed. No
                * tab to keep in step with it, which is an effect and a special case in
                * `closeTab` that both went away with the surface. */}
              {inboxOpen ? (
                <>
                  {/* Click-away. Transparent on purpose — dimming the room would contradict
                      "leaves it visible", and this only has to catch the click. */}
                  <button
                    type="button"
                    aria-label="Close alerts"
                    tabIndex={-1}
                    onClick={() => navigate(pathForHome())}
                    className="absolute inset-0 z-20 cursor-default"
                  />
                  <div className="absolute inset-y-0 right-0 z-30 flex w-[380px] max-w-full shadow-[-8px_0_32px_oklch(0_0_0/12%)]">
                    <ErrorBoundary where="Alerts" compact>
                      <InboxPanel inbox={inbox} onClose={() => navigate(pathForHome())} />
                    </ErrorBoundary>
                  </div>
                </>
              ) : null}
            </div>
          </div>
        </div>
        {/* Drop a file anywhere in the window and it lands on the composer. Disabled — but
            still swallowing the drop — while something is covering the thread, since attaching
            to a composer nobody can see is a file that has vanished. */}
        {/* Armed only when something can actually receive the file.
            It used to be disabled whenever one of the five takeover screens covered the
            thread; there are no takeovers now, so the question is the one it was always really
            asking. Asking the *layout* whether a chat tab exists was not it — a pane renders
            only its active tab, so a chat sitting behind a Work tab satisfies the tree and has
            no composer mounted, and the overlay promised "Drop to attach" over a file that
            then went nowhere. The composer answers for itself. */}
        {/* The two screens that cover the window, opened by the route and by nothing else.
            
            Outside `LayoutView` because that is what they are: `fixed inset-0 z-30` roots whose
            own layouts do not fit a pane (see `pane-surfaces.test.tsx` for the measurements that
            settled it). While they were registered as tabs the model said "pane" and the paint
            said "window", and you could watch the disagreement — a tab appearing for as long as
            the lazy chunk took, then the screen being taken.

            The route is the only state, exactly as it is for the alerts panel above: a path is
            open, anything else is closed. No tab to keep in step, and no effect to keep it. */}
        {panelOpen ? (
          <ErrorBoundary where="The Control Panel">
            <Suspense fallback={<ScreenLoading />}>
              <ControlPanel
                tab={route.tab}
                openTask={route.taskId}
                onSelectTab={(t) => navigate(pathForTab(t))}
                onOpenTask={(id) => navigate(pathForTask(id))}
                onNewConversation={(project) => {
                  // The panel is a route, the chat is a layout tab: open the draft, then leave.
                  newConversation(project);
                  navigate(pathForHome());
                }}
                onClose={() => navigate(pathForHome())}
              />
            </Suspense>
          </ErrorBoundary>
        ) : null}
        {/* The context breakdown, full screen — reached from the drop-up on the composer's ring.
            A takeover like Settings rather than a tab in the side column: at 424px its tables
            were cramped, and the ring already answers the quick question. */}
        {route.contextOpen ? (
          <ErrorBoundary where="The context breakdown">
            <div className="bg-background fixed inset-0 z-30 flex flex-col">
              <div className="window-drag-region window-controls-gap border-border flex h-11 shrink-0 items-center gap-2 border-b pe-3">
                <span className="text-sm font-semibold">Context</span>
                <span className="text-muted-foreground text-xs">what is in his window, and where it came from</span>
                <span className="flex-1" />
                <span className="text-muted-foreground/70 text-[11px]">Esc to close</span>
                <button
                  type="button"
                  aria-label="Close the context breakdown"
                  onClick={() => navigate(pathForHome())}
                  className="text-muted-foreground hover:text-foreground hover:bg-accent/60 flex size-8 items-center justify-center rounded-lg transition-colors"
                >
                  <X className="size-4" aria-hidden />
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-auto">
                <Suspense fallback={<ScreenLoading />}>
                  <ContextDetailScreen conversationId={conversationId} onClose={() => navigate(pathForHome())} />
                </Suspense>
              </div>
            </div>
          </ErrorBoundary>
        ) : null}
        {route.settingsTab ? (
          <ErrorBoundary where="Settings">
            <Suspense fallback={<ScreenLoading />}>
              <SettingsPage
                tab={route.settingsTab}
                config={config}
                onSelectTab={(t) => navigate(pathForSettings(t))}
                onSaveConfig={onSaveConfig}
                onConnectionSaved={onConnectionSaved}
                onClose={() => navigate(pathForHome())}
              />
            </Suspense>
          </ErrorBoundary>
        ) : null}
        <DropZone enabled={canAttach} />
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
 * A full-bleed backdrop rather than a spinner in the corner: anything smaller reads as the click
 * having missed. On a warm cache it is one frame.
 *
 * Full-bleed *inside its pane*, though — `absolute`, not `fixed`. This used to say "these screens
 * cover the app", which was true when they were route-level takeovers; they are tabs in panes now,
 * so the fallback for one lazy chunk dimmed the app header, every tab strip and all three panes.
 * Its containing block is the pane body — the `role="tabpanel"` div in `layout-view.tsx`, already
 * `relative` — which is exactly the box it should blank.
 *
 * Exported for `pane-surfaces.test.tsx`, along with the surface roots; see the invariant there. */
export function ScreenLoading() {
  return (
    <div className="bg-background/80 absolute inset-0 z-40 backdrop-blur-[2px]" role="status">
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
