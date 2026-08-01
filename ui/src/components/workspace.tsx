import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import { Thread } from "@/components/assistant-ui/thread";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppHeader } from "@/components/app-header";
import { ControlPanel } from "@/components/control-panel";
import { SettingsPage } from "@/components/settings/settings-page";
import { WorkspaceFileViewer } from "@/components/workspace-file-viewer";
import { InboxPanel } from "@/components/inbox-panel";
import { MindPanel } from "@/components/mind-panel";
import { HistoryPanel } from "@/components/history-panel";
import { SessionBar } from "@/components/session-bar";
import { DropZone } from "@/components/drop-zone";
import { ErrorBoundary } from "@/components/error-boundary";
import { useAutonomy } from "@/hooks/use-autonomy";
import { useMessages } from "@/hooks/use-messages";
import { useMood } from "@/hooks/use-mood";
import { AnyFileAttachmentAdapter } from "@/lib/attachments";
import { cn } from "@/lib/utils";
import { moodHue } from "@/lib/backend/mood";
import { parseLocation, pathForHome, pathForSettings, pathForTab, pathForTask } from "@/lib/router";
import {
  createBackendAdapter,
  fetchConversation,
  USAGE_PART,
  type StoredTurn,
  patchServerConfig,
  type ServerConfig,
} from "@/lib/backend";

const MIND_MIN = 320;
const MIND_MAX = 720;
const MIND_DEFAULT = 400;

/** The room the chat needs before anything else may have any.
 *
 * With Conversations and Mind both pinned open at fixed widths, a 1024px window left the
 * thread 370px and prose wrapped to three words a line — a paragraph became a column. Neither
 * panel yielded, because neither knew the other existed. So the two thresholds below are the
 * order in which they give way: Mind first, since it is the ancillary one, then Conversations,
 * which stops taking a column of its own and covers instead. */
const CHAT_FLOOR = 560;
const HISTORY_WIDTH = 256;
const MIND_YIELDS_BELOW = CHAT_FLOOR + HISTORY_WIDTH + MIND_MIN; // 1136
const HISTORY_YIELDS_BELOW = CHAT_FLOOR + HISTORY_WIDTH + 96; // 912

/** Where the open conversation is remembered across a reload.
 *
 * Without it a refresh dropped you on a blank chat with your conversation one click away in
 * a panel that was also closed — so the app forgot what you were doing every time it
 * reloaded, which is the one thing a window is supposed to be good at. */
const LAST_CONVERSATION = "kith-conversation";

/** The ready-state app: chat runtime, header, and the autonomy ("Mind") panel.
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
  // Which conversation the chat is in. Held in a ref as well as state: the adapter reads
  // it fresh on every run, and a resumed conversation must not rebuild the runtime while a
  // stream is open.
  const [conversationId, setConversationId] = useState("");
  const conversationRef = useRef("");
  conversationRef.current = conversationId;
  // Messages to seed the thread with when resuming. Bumping `threadKey` remounts the
  // runtime, which is the only way to replace a local runtime's messages wholesale.
  const [resumed, setResumed] = useState<ThreadMessageLike[]>([]);
  const [threadKey, setThreadKey] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  // What this session is working on. Held here rather than fetched inside the bar because
  // it changes from two directions — you set it, and so does he, by starting a project or
  // filing a task mid-turn.
  const [projectId, setProjectId] = useState<number | null>(null);

  // No config passed: the server reads its own settings, so there is nothing here that
  // can go stale. Memoised so the runtime is never recreated mid-stream.
  const adapter = useMemo(
    () =>
      createBackendAdapter({
        get: () => conversationRef.current,
        set: (id) => setConversationId((was) => was || id),
      }),
    [],
  );
  // Always offered, and it takes anything. It used to appear only for models reporting
  // vision, and then only accept `image/*` — so a spreadsheet could not be attached at all,
  // and on a model without vision the paperclip simply vanished. Both were the wrong call:
  // he has a whole computer, so a file he cannot *see* is still a file he can open, and
  // whether to inline a picture or hand him a path is a decision the server makes next to
  // the model config rather than one the composer makes by hiding a button.
  const attachments = useMemo(() => new AnyFileAttachmentAdapter(), []);
  const runtime = useLocalRuntime(adapter, {
    initialMessages: resumed,
    ...(attachments ? { adapters: { attachments } } : {}),
  });

  const openConversation = useCallback(async (id: string) => {
    const detail = await fetchConversation(id).catch(() => null);
    if (!detail) return;
    setConversationId(id);
    setProjectId(detail.projectId ?? null);
    setResumed(toThreadMessages(detail.timeline));
    setThreadKey((n) => n + 1);
    remember(id);
  }, []);

  const newConversation = useCallback(() => {
    setConversationId("");
    setProjectId(null);
    setResumed([]);
    setThreadKey((n) => n + 1);
    remember("");
  }, []);

  // Reopen where you were. A reload used to land on an empty chat with the history panel
  // closed, so the conversation you were mid-way through was two clicks away and looked
  // gone. Runs once: if the stored conversation has since been deleted, `openConversation`
  // finds nothing and this quietly stays a fresh chat.
  useEffect(() => {
    let stored = "";
    try {
      stored = localStorage.getItem(LAST_CONVERSATION) || "";
    } catch {
      /* private mode, or no storage — a fresh chat is a fine answer */
    }
    if (stored) void openConversation(stored);
  }, [openConversation]);

  // The session's own state, re-read whenever the conversation changes underneath us — a
  // chat started from an empty composer gets its id from the stream, not from us.
  const refreshSession = useCallback(() => {
    if (!conversationId) return;
    void fetchConversation(conversationId)
      .then((detail) => setProjectId(detail.projectId ?? null))
      .catch(() => {});
  }, [conversationId]);

  useEffect(() => {
    remember(conversationId);
    refreshSession();
  }, [conversationId, refreshSession]);

  const autonomy = useAutonomy();
  const inbox = useMessages();
  const { mood } = useMood();
  // Home is two windows — Chat and Mind — side by side. Mind can be collapsed to
  // give Chat the whole room, and the split is draggable (and remembered).
  const [mindOpen, setMindOpen] = useState(true);
  const [mindWidth, setMindWidth] = useState(() => {
    try {
      const v = Number(localStorage.getItem("kith-mind-width"));
      return v >= MIND_MIN && v <= MIND_MAX ? v : MIND_DEFAULT;
    } catch {
      return MIND_DEFAULT;
    }
  });
  // The Control Panel (and which tab/task is open) lives in the URL, so deep
  // links, refresh, and back/forward all work.
  const location = useLocation();
  const navigate = useNavigate();
  const route = parseLocation(location.pathname);
  const panelOpen = route.panelOpen;
  const [inboxOpen, setInboxOpen] = useState(false);

  /* Which panels the window can currently afford.
   *
   * `squeezed` is kept apart from `mindOpen` on purpose: one is the window's opinion and the
   * other is yours. Collapsing Mind by writing to `mindOpen` would overwrite your choice, so
   * widening the window again would leave it shut and look like the app had forgotten. Held
   * this way, narrowing hides it and widening brings back exactly what you had.
   *
   * Opening Mind by hand while narrow wins — you asked for it — until the window crosses the
   * threshold again, which is the point at which the question is genuinely being re-asked. */
  const [viewport, setViewport] = useState(() => window.innerWidth);
  const [squeezed, setSqueezed] = useState(() => window.innerWidth < MIND_YIELDS_BELOW);
  useEffect(() => {
    let wasNarrow = window.innerWidth < MIND_YIELDS_BELOW;
    const measure = () => {
      setViewport(window.innerWidth);
      const narrow = window.innerWidth < MIND_YIELDS_BELOW;
      // Only on the crossing, so a hand-opened Mind is not slammed shut by every resize event
      // of a drag that never leaves the narrow range.
      if (narrow !== wasNarrow) {
        wasNarrow = narrow;
        setSqueezed(narrow);
      }
    };
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const covering = viewport < HISTORY_YIELDS_BELOW;
  const mindVisible = mindOpen && !squeezed;
  const toggleMind = useCallback(() => {
    if (squeezed) {
      setSqueezed(false);
      setMindOpen(true);
    } else setMindOpen((open) => !open);
  }, [squeezed]);
  // Clamped to what is actually there rather than to what you dragged it to once on a wider
  // window. A remembered 720 on a 1100px window is a chat column of nothing.
  const mindRoom = Math.max(
    MIND_MIN,
    Math.min(mindWidth, viewport - CHAT_FLOOR - (historyOpen && !covering ? HISTORY_WIDTH : 0)),
  );

  const startResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const onMove = (ev: PointerEvent) => {
      const w = Math.max(MIND_MIN, Math.min(MIND_MAX, window.innerWidth - ev.clientX));
      setMindWidth(w);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      setMindWidth((w) => {
        try {
          localStorage.setItem("kith-mind-width", String(w));
        } catch {
          /* ignore */
        }
        return w;
      });
    };
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, []);

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

  // Any session mid-work. Was a single `running` flag called `roaming` all the way up the
  // tree; work belongs to sessions now, so the question is plural and the glow means
  // "something is happening" rather than "the roam switch is on".
  const working = (autonomy.status?.working ?? []).length > 0;
  // Whether *this* session is one of them. Derived rather than kept in state: the server is
  // the only thing that knows — he stops himself when the work runs out — and a local copy
  // would keep saying "working" after that, which is the exact lie the button exists to
  // prevent.
  const sessionWorking = (autonomy.status?.working ?? []).includes(conversationId);
  // He adopts a project by working on one, so what the session is bound to can change
  // part-way through a turn. One re-read per completed step or turn, which is the cheapest
  // signal that anything could have changed at all.
  const finished = autonomy.activity.filter((item) => item.kind === "done").length;
  useEffect(refreshSession, [finished, refreshSession]);
  // The room glows green while he is working, otherwise the colour of his mood.
  const wash = working ? "var(--roam)" : moodHue(mood?.label);

  return (
    <TooltipProvider>
      <AssistantRuntimeProvider key={threadKey} runtime={runtime}>
        <div className="relative flex h-dvh flex-col overflow-hidden text-foreground">
          {/* Ambient wash — leans green while he works, amber while he's here. */}
          <div className="kith-ambient" style={{ ["--wash" as string]: wash }} />
          <div className="relative z-10 flex min-h-0 flex-1 flex-col">
            <AppHeader
              working={working}
              status={autonomy.status?.current ?? null}
              mood={mood}
              model={config.model}
              effort={config.effort}
              // Offered unless the provider has said otherwise: an unknown model is the
              // normal case right after a switch, and the transport retries a reasoning 400.
              supportsEffort={!config.capabilities?.known || config.capabilities.reasoning}
              onEffort={(effort) => {
                onSaveConfig({ ...config, effort });
                void patchServerConfig({ effort });
              }}
              historyOpen={historyOpen}
              onOpenHistory={() => setHistoryOpen((open) => !open)}
              onNewConversation={newConversation}
              unread={inbox.unread}
              mindOpen={mindVisible}
              onOpenInbox={() => {
                inbox.enableNotifications();
                setInboxOpen(true);
              }}
              onOpenMind={toggleMind}
              onOpenPanel={() => navigate(pathForTab("overview"))}
              onOpenSettings={() => navigate(pathForSettings())}
            />
            <div className="relative flex min-h-0 flex-1">
              {historyOpen ? (
                <>
                  {/* Covering rather than taking a column, once there is not enough window for
                      both. A fixed 256px out of 900 is a quarter of the screen spent on a list
                      you are done with the moment you have picked from it. */}
                  {covering ? (
                    <button
                      type="button"
                      aria-label="Close the conversation list"
                      onClick={() => setHistoryOpen(false)}
                      className="absolute inset-0 z-20 bg-background/50 backdrop-blur-[2px]"
                    />
                  ) : null}
                  <div
                    className={cn(
                      "border-border/60 w-64 shrink-0 border-e",
                      covering && "absolute inset-y-0 start-0 z-30 bg-background shadow-2xl",
                    )}
                  >
                    {/* One boundary per panel, so a panel that throws takes only itself down.
                        The chat surviving a broken roadmap graph is the difference between
                        "one thing is wrong" and "Kith is down". */}
                    <ErrorBoundary where="Conversations" compact>
                      <HistoryPanel
                        activeId={conversationId}
                        onOpen={(id) => {
                          void openConversation(id);
                          // Picking from a drawer closes the drawer. Leaving it over the
                          // conversation you just opened is the one thing it must not do.
                          if (covering) setHistoryOpen(false);
                        }}
                        onNew={() => {
                          newConversation();
                          if (covering) setHistoryOpen(false);
                        }}
                        onClose={() => setHistoryOpen(false)}
                      />
                    </ErrorBoundary>
                  </div>
                </>
              ) : null}
              {/* Chat window */}
              <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
                <SessionBar
                  conversationId={conversationId}
                  projectId={projectId}
                  working={sessionWorking}
                  onProject={setProjectId}
                  onKeepWorking={() => void autonomy.start(conversationId)}
                  onStop={() => void autonomy.stop(conversationId)}
                />
                {/* The thread gets its own box with a definite height rather than sitting
                    straight in the column. Without one, the thread root's `h-full` resolved
                    against the whole column — session bar included — so the moment a
                    conversation existed the composer's bottom edge sat 37px past the window
                    and the send button was clipped clean off it. An empty chat looked fine
                    because the session bar draws nothing, which is what made it read as
                    random rather than as a layout bug. */}
                <div className="relative min-h-0 flex-1">
                  <ErrorBoundary where="The conversation">
                    <Thread />
                  </ErrorBoundary>
                </div>
              </div>
              {/* draggable divider */}
              {mindVisible ? (
                <div
                  onPointerDown={startResize}
                  className="group relative z-10 w-1.5 shrink-0 cursor-col-resize"
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="Resize Mind window"
                >
                  <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border/60 transition-colors group-hover:bg-kith/60 group-active:bg-kith" />
                </div>
              ) : null}
              {/* Mind window */}
              {mindVisible ? (
                <div style={{ width: mindRoom }} className="shrink-0">
                  <ErrorBoundary where="Mind" compact>
                    <MindPanel
                      autonomy={autonomy}
                      conversationId={conversationId}
                      width={mindRoom}
                      onClose={() => setMindOpen(false)}
                    />
                  </ErrorBoundary>
                </div>
              ) : null}
            </div>
          </div>
        </div>
        {/* Drop a file anywhere in the window and it lands on the composer. Disabled — but
            still swallowing the drop — while something is covering the thread, since attaching
            to a composer nobody can see is a file that has vanished. */}
        <DropZone enabled={!route.settingsTab && !panelOpen && !inboxOpen} />
        {/* One viewer for the whole app — a path in a message, a deliverable, and the
            file browser all open this. */}
        <WorkspaceFileViewer />
        {inboxOpen ? <InboxPanel inbox={inbox} onClose={() => setInboxOpen(false)} /> : null}
        {route.settingsTab ? (
          <ErrorBoundary where="Settings">
            <SettingsPage
              tab={route.settingsTab}
              config={config}
              onSelectTab={(t) => navigate(pathForSettings(t))}
              onSaveConfig={onSaveConfig}
              onConnectionSaved={onConnectionSaved}
              onClose={() => navigate(pathForHome())}
            />
          </ErrorBoundary>
        ) : null}
        {panelOpen ? (
          <ErrorBoundary where="The Control Panel">
            <ControlPanel
              tab={route.tab}
              openTask={route.taskId}
              onSelectTab={(t) => navigate(pathForTab(t))}
              onOpenTask={(id) => navigate(pathForTask(id))}
              onClose={() => navigate(pathForHome())}
            />
          </ErrorBoundary>
        ) : null}
      </AssistantRuntimeProvider>
    </TooltipProvider>
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

/**
 * A stored conversation, rebuilt as the thread saw it.
 *
 * Not just the words: the reasoning blocks he opened, the prose between tool rounds, and
 * each call with the result it got. Reopening a conversation should show you the one you
 * had — a paragraph where six rounds of work used to be is a summary, and no amount of
 * cleverness reconstructs the shape once it is gone.
 */
function toThreadMessages(timeline: StoredTurn[]): ThreadMessageLike[] {
  const out: unknown[] = [];
  for (const turn of timeline) {
    const content: unknown[] = [];
    // Collected rather than pushed as they arrive. One of these is recorded per model request,
    // and pushing each as its own data part is what made a reopened conversation a column of
    // six-figure token counts — "328,422 tokens", "165,292 tokens", a dozen deep — threaded
    // through the reply. The live stream has always accumulated them into one; a resumed turn
    // has to read the same, or reopening a conversation does not show you the one you had.
    //
    // It also cost the tool grouping: a data part between two tool calls breaks the run, so
    // eight consecutive calls rendered as eight separate "1 tool call" rows instead of one
    // line saying what he touched.
    const rounds: { uncached: number; cached: number; out: number }[] = [];
    for (const part of turn.parts) {
      if (part.kind === "text") content.push({ type: "text", text: part.text });
      else if (part.kind === "reasoning") content.push({ type: "reasoning", text: part.text });
      else if (part.kind === "tool") {
        content.push({
          type: "tool-call",
          toolCallId: part.id,
          toolName: part.name,
          args: part.arguments,
          argsText: JSON.stringify(part.arguments),
          result: part.result,
        });
      } else {
        rounds.push({ uncached: part.uncached, cached: part.cached, out: part.out });
      }
    }
    // Last, so the figure lands at the foot of the turn and nothing is split around it.
    // Token counts ride back as the same data part the live stream uses, so the footer reads
    // the same on a resumed turn as it did on a fresh one.
    if (rounds.length) content.push({ type: "data", name: USAGE_PART, data: { rounds } });
    if (content.length) out.push({ role: turn.role, content });
  }
  // One cast, at the boundary: the shapes above are the library's own, and its content
  // union narrows by role in a way that defeats inference through a map.
  return out as ThreadMessageLike[];
}
