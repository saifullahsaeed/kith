import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import { Thread } from "@/components/assistant-ui/thread";
import { CheckpointsProvider } from "@/components/assistant-ui/checkpoints-context";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppHeader } from "@/components/shell/app-header";
import { ControlPanel } from "@/components/control-panel";
import { SettingsPage } from "@/components/settings/settings-page";
import { WorkspaceFileViewer } from "@/components/files/workspace-file-viewer";
import { InboxPanel } from "@/components/chat/inbox-panel";
import { WorkPanel } from "@/components/chat/work-panel";
import { HistoryPanel } from "@/components/chat/history-panel";
import { SessionBar } from "@/components/chat/session-bar";
import { DropZone } from "@/components/shell/drop-zone";
import { ErrorBoundary } from "@/components/shell/error-boundary";
import { useActivity } from "@/hooks/use-activity";
import { useMessages } from "@/hooks/use-messages";
import { useMood } from "@/hooks/use-mood";
import { AnyFileAttachmentAdapter } from "@/lib/attachments";
import { cn } from "@/lib/utils";
import { moodHue } from "@/lib/backend/mood";
import {
  parseLocation,
  pathForHome,
  pathForMessages,
  pathForSettings,
  pathForTab,
  pathForTask,
} from "@/lib/router";
import {
  createBackendAdapter,
  resumeTurn,
  fetchConversation,
  USAGE_PART,
  type ContextLedger,
  type StoredTurn,
  patchServerConfig,
  type ServerConfig,
} from "@/lib/backend";

const WORK_MIN = 320;
const WORK_MAX = 720;
const WORK_DEFAULT = 400;

/** The room the chat needs before anything else may have any.
 *
 * With Conversations and Work both pinned open at fixed widths, a 1024px window left the
 * thread 370px and prose wrapped to three words a line — a paragraph became a column. Neither
 * panel yielded, because neither knew the other existed. So the two thresholds below are the
 * order in which they give way: Work first, since it is the ancillary one, then Conversations,
 * which stops taking a column of its own and covers instead. */
const CHAT_FLOOR = 560;
const HISTORY_WIDTH = 256;
const WORK_YIELDS_BELOW = CHAT_FLOOR + HISTORY_WIDTH + WORK_MIN; // 1136
const HISTORY_YIELDS_BELOW = CHAT_FLOOR + HISTORY_WIDTH + 96; // 912

/** Where the open conversation is remembered across a reload.
 *
 * Without it a refresh dropped you on a blank chat with your conversation one click away in
 * a panel that was also closed — so the app forgot what you were doing every time it
 * reloaded, which is the one thing a window is supposed to be good at. */
const LAST_CONVERSATION = "kith-conversation";

/** Turns mounted when a conversation opens, and added by each "load earlier".
 *
 * Forty rather than a round hundred: it is comfortably more than fits on a screen, so the thread
 * still reads as continuous history rather than a stub, while staying far below the point where
 * mounting and markdown-parsing the tail is what you are waiting for. */
const WINDOW = 40;

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
  // Which conversation the chat is in. Held in a ref as well as state: the adapter reads
  // it fresh on every run, and a resumed conversation must not rebuild the runtime while a
  // stream is open.
  const [conversationId, setConversationId] = useState("");
  const conversationRef = useRef("");
  conversationRef.current = conversationId;
  // Messages to seed the thread with when resuming. Bumping `threadKey` remounts the
  // runtime, which is the only way to replace a local runtime's messages wholesale.
  const [resumed, setResumed] = useState<ThreadMessageLike[]>([]);
  /* The whole conversation as fetched, and how much of its tail is actually mounted.
   *
   * Held apart because the two are different questions. A 473-turn conversation was handed to
   * the runtime entire, and `content-visibility: auto` does not save you from that: it skips
   * layout and paint for off-screen messages, but React still mounts every one and
   * `MarkdownText` still parses every character — ~547 KB of prose before anything appears. So
   * the fix has to be to not mount them, not to style them cheaply.
   *
   * It also fixes where the scroll lands. Until a message is laid out the browser assumes the
   * 200px of `contain-intrinsic-size`, so 473 of them made the initial `scrollHeight` a
   * ~95,000px guess against a much larger real height — "scroll to bottom" went to the bottom of
   * that fiction, which is near the top. Mount forty and the height is honest.
   */
  const [timeline, setTimeline] = useState<StoredTurn[]>([]);
  const [shown, setShown] = useState(WINDOW);
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

  /**
   * Ask him — here, in the thread — to check work he handed over.
   *
   * A chat message rather than a task comment, and that is the whole design of the `review` column: a tick
   * verifying its own output is marking its own homework, since it wrote the brief, chose the
   * requirements and supplied the evidence. Chat has the conversation the work came out of, forty
   * rounds, and a person in it.
   *
   * Appending to the thread rather than posting on the task, because the point is that you see the
   * answer and can argue with it. A comment on a task is somewhere you have to go and look.
   */
  const reviewFinished = useCallback(
    (taskIds: number[]) => {
      if (!taskIds.length) return;
      const list = taskIds.map((id) => `#${id}`).join(", ");
      const text =
        `Check the work you finished on ${list} before it counts as done. For each one: read what ` +
        `you claimed on the task, then verify the single claim resting on the least evidence — ` +
        `don't re-do the work. Then close it or send it back to yourself saying what's missing.`;
      runtime.thread.append({ role: "user", content: [{ type: "text", text }] });
    },
    [runtime],
  );

  /**
   * Same shape as `reviewFinished`, one step earlier: ask him to walk through a plan waiting
   * for approval — here, in the thread, where you can actually push back on it rather than
   * a bare yes/no. Approving or asking for changes is his call to act on afterward
   * (`update_task(status='planned')` or revising the plan file), not something this button
   * does directly.
   */
  const approvePlan = useCallback(
    (taskIds: number[]) => {
      if (!taskIds.length) return;
      const list = taskIds.map((id) => `#${id}`).join(", ");
      const text =
        `I'd like to look at the plan for ${list} before you start. Walk me through it — what ` +
        `you found, what you're about to do, and anything you're unsure about — so I can approve ` +
        `it (move it to 'planned') or tell you what to change.`;
      runtime.thread.append({ role: "user", content: [{ type: "text", text }] });
    },
    [runtime],
  );

  /* Rejoin the turn already running in the conversation you just opened.

     The screen is a window onto a turn, not the thing running it. The turn lives on the server,
     on its own thread, and carries on whether or not anybody is watching — but until now the
     watching could only ever start at the beginning, so switching away mid-answer and coming
     back showed a message frozen where you left it, and the finished reply only appeared after
     a reload. Everything the turn said in between was reachable (`live_turns` kept it) and
     nothing asked for it.

     It matters more than a cosmetic catch-up now that he can block on a question and on a
     permission: a card you cannot see is a turn that looks hung, and the answer it is waiting
     for is one you have no way to give.

     Guarded on `isRunning`, because the thread is already streaming when *this* window started
     the turn — resuming then would put a second reader on the same events and render them
     twice. */
  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    void resumeTurn(conversationId).then((stream) => {
      if (cancelled || !stream) return;
      const state = runtime.thread.getState();
      if (state.isRunning) return;
      runtime.thread.resumeRun({
        parentId: state.messages.at(-1)?.id ?? null,
        stream: () => stream,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [conversationId, threadKey, runtime]);

  const openConversation = useCallback(async (id: string) => {
    const detail = await fetchConversation(id).catch(() => null);
    if (!detail) return;
    setConversationId(id);
    setProjectId(detail.projectId ?? null);
    setTimeline(detail.timeline);
    setShown(WINDOW);
    setResumed(toThreadMessages(detail.timeline.slice(-WINDOW)));
    setThreadKey((n) => n + 1);
    remember(id);
  }, []);

  /* Widen the window by another page and rebuild the thread.
   *
   * A remount (`threadKey`) rather than a prepend, because `useLocalRuntime` reads
   * `initialMessages` once — prepending is exactly what it cannot do, and working around that
   * properly means moving to `useExternalStoreRuntime`. Acceptable here only because this is an
   * explicit click on a conversation you are already looking at, not something that happens
   * while you scroll. No refetch: the whole timeline is already in hand.
   */
  const loadEarlier = useCallback(() => {
    const next = Math.min(shown + WINDOW, timeline.length);
    if (next === shown) return;
    setShown(next);
    setResumed(toThreadMessages(timeline.slice(-next)));
    setThreadKey((n) => n + 1);
  }, [shown, timeline]);

  const newConversation = useCallback(() => {
    setConversationId("");
    setProjectId(null);
    setTimeline([]);
    setShown(WINDOW);
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

  const activity = useActivity();
  const inbox = useMessages();
  const { mood } = useMood();
  // Home is two windows — Chat and Work — side by side. Work can be collapsed to
  // give Chat the whole room, and the split is draggable (and remembered).
  const [workOpen, setWorkOpen] = useState(true);
  const [workWidth, setWorkWidth] = useState(() => {
    try {
      const v = Number(localStorage.getItem("kith-work-width"));
      return v >= WORK_MIN && v <= WORK_MAX ? v : WORK_DEFAULT;
    } catch {
      return WORK_DEFAULT;
    }
  });
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

  /* Which panels the window can currently afford.
   *
   * `squeezed` is kept apart from `workOpen` on purpose: one is the window's opinion and the
   * other is yours. Collapsing Work by writing to `workOpen` would overwrite your choice, so
   * widening the window again would leave it shut and look like the app had forgotten. Held
   * this way, narrowing hides it and widening brings back exactly what you had.
   *
   * Opening Work by hand while narrow wins — you asked for it — until the window crosses the
   * threshold again, which is the point at which the question is genuinely being re-asked. */
  const [viewport, setViewport] = useState(() => window.innerWidth);
  const [squeezed, setSqueezed] = useState(() => window.innerWidth < WORK_YIELDS_BELOW);
  useEffect(() => {
    let wasNarrow = window.innerWidth < WORK_YIELDS_BELOW;
    const measure = () => {
      setViewport(window.innerWidth);
      const narrow = window.innerWidth < WORK_YIELDS_BELOW;
      // Only on the crossing, so a hand-opened Work is not slammed shut by every resize event
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
  const workVisible = workOpen && !squeezed;
  const toggleWork = useCallback(() => {
    if (squeezed) {
      setSqueezed(false);
      setWorkOpen(true);
    } else setWorkOpen((open) => !open);
  }, [squeezed]);
  // Clamped to what is actually there rather than to what you dragged it to once on a wider
  // window. A remembered 720 on a 1100px window is a chat column of nothing.
  const workRoom = Math.max(
    WORK_MIN,
    Math.min(workWidth, viewport - CHAT_FLOOR - (historyOpen && !covering ? HISTORY_WIDTH : 0)),
  );

  const startResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const onMove = (ev: PointerEvent) => {
      const w = Math.max(WORK_MIN, Math.min(WORK_MAX, window.innerWidth - ev.clientX));
      setWorkWidth(w);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      setWorkWidth((w) => {
        try {
          localStorage.setItem("kith-work-width", String(w));
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

  // Nothing works on its own, so nothing is mid-work between turns. The green wash and the
  // session bar's "working" state both wait on the reliability work, which gives them one
  // honest meaning: a session is busy if and only if a turn is live in it. Until then they
  // are off rather than lying — see docs/superpowers/specs/2026-08-08-remove-the-self-
  // directed-loop-design.md.
  const working = false;
  // He adopts a project by working on one, so what the session is bound to can change
  // part-way through a turn. One re-read per completed step or turn, which is the cheapest
  // signal that anything could have changed at all.
  const finished = activity.activity.filter((item) => item.kind === "done").length;
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
              status={null}
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
              workOpen={workVisible}
              onOpenInbox={() => {
                inbox.enableNotifications();
                navigate(pathForMessages());
              }}
              onOpenWork={toggleWork}
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
                  onProject={setProjectId}
                />
                {/* The thread gets its own box with a definite height rather than sitting
                    straight in the column. Without one, the thread root's `h-full` resolved
                    against the whole column — session bar included — so the moment a
                    conversation existed the composer's bottom edge sat 37px past the window
                    and the send button was clipped clean off it. An empty chat looked fine
                    because the session bar draws nothing, which is what made it read as
                    random rather than as a layout bug.

                    A flex column, not a block, and that is the same bug a second time. `Thread`
                    is `h-full`, so in block layout it takes the whole box *and* the "Load
                    earlier" button above it takes its own height on top — the thread's bottom
                    ends up past the box by exactly the height of that button. It only shows on
                    a conversation long enough to be windowed, because that is the only time the
                    button is rendered, so it reads as "big chats are broken" rather than as a
                    layout bug. The bottom-most thing in the thread falls off first, which is
                    the context meter under the composer.

                    Anything added beside `Thread` in here has to go in the flow, not on top of
                    it. */}
                <div className="relative flex min-h-0 flex-1 flex-col">
                  <ErrorBoundary where="The conversation">
                    <CheckpointsProvider
                      conversationId={conversationId}
                      // How many turns were dropped off the front by the window. Checkpoints are
                      // tagged with their absolute turn index and matched against the *rendered*
                      // message index, so without this the offer silently walks backwards through
                      // the conversation as you window — "restore to here" on the first visible
                      // message would target whatever happened 433 turns earlier. A destructive
                      // action aiming at the wrong commit is the worst thing windowing could have
                      // broken, so the offset travels with the checkpoints rather than being
                      // recomputed anywhere that compares them.
                      turnOffset={Math.max(0, timeline.length - shown)}
                    >
                      {timeline.length > shown ? (
                        <div className="flex justify-center pt-3">
                          <button
                            type="button"
                            onClick={loadEarlier}
                            className="border-border/60 bg-card/60 text-muted-foreground hover:text-foreground rounded-full border px-3 py-1 text-[11px]"
                          >
                            Load {Math.min(WINDOW, timeline.length - shown)} earlier ·{" "}
                            {timeline.length - shown} above
                          </button>
                        </div>
                      ) : null}
                      <div className="relative min-h-0 flex-1">
                        <Thread conversationId={conversationId} />
                      </div>
                    </CheckpointsProvider>
                  </ErrorBoundary>
                </div>
              </div>
              {/* draggable divider */}
              {workVisible ? (
                <div
                  onPointerDown={startResize}
                  className="group relative z-10 w-1.5 shrink-0 cursor-col-resize"
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="Resize the work panel"
                >
                  <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border/60 transition-colors group-hover:bg-kith/60 group-active:bg-kith" />
                </div>
              ) : null}
              {/* Work window */}
              {workVisible ? (
                <div style={{ width: workRoom }} className="shrink-0">
                  <ErrorBoundary where="Work" compact>
                    <WorkPanel
                      activity={activity}
                      conversationId={conversationId}
                      width={workRoom}
                      onClose={() => setWorkOpen(false)}
                      onReview={reviewFinished}
                      onApprove={approvePlan}
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
            file browser all open this. Given this session's project, because the paths it is
            handed are mostly relative ones out of his prose and his tool results, and a
            relative path he wrote during a project turn is relative *to that project*. Without
            it the viewer asked the global workspace root and got "there's no
            .kith/work/task-76.md" for a file that was never missing. */}
        <WorkspaceFileViewer projectId={projectId} />
        {inboxOpen ? <InboxPanel inbox={inbox} onClose={() => navigate(pathForHome())} /> : null}
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
  // A running count of tool calls seen so far in this conversation, not the backend's own
  // id. `${turnIndex}-${part.id}` was the earlier fix and is still right for the ordinary
  // case, but it assumes the backend's per-turn ids are actually unique within whatever
  // `timeline()` groups as one turn — true for a turn that is one `stream_agent` call, and
  // false for older conversations where a reminder continued the same conversation_id
  // with no new user message in between: two separate turns, each restarting its own ids at
  // c1, land in the transcript with nothing to tell `timeline()` to split them, so "c9" can
  // appear twice *inside* one rendered turn. No amount of scoping by turn index fixes a
  // collision that happens within a single turn index — only something that can never repeat
  // does, so this counts instead of reading anything the backend assigned.
  let callSeq = 0;
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
    // One per turn at most, recorded when it ended — see the `context` branch in
    // `services/conversations`. Collected the same way the counts are, so it lands in the same
    // footer instead of somewhere in the middle of the reply.
    let context: ContextLedger | undefined;
    let baseline: ContextLedger | undefined;
    let folded = false;
    let retried = 0;
    for (const part of turn.parts) {
      if (part.kind === "text") content.push({ type: "text", text: part.text });
      else if (part.kind === "reasoning") content.push({ type: "reasoning", text: part.text });
      else if (part.kind === "tool") {
        content.push({
          type: "tool-call",
          // The backend resets its own tool-call ids to c1 at the start of every turn, so the
          // raw id repeats across nearly every message in a long conversation — 209 times for
          // "c9" alone in one real conversation, which is what actually crashed the thread on
          // reopening it: two different messages both offering a tool call keyed "c9" collided
          // in assistant-ui's own resource cache. A counter rather than the backend's id or
          // even `${turnIndex}-${part.id}`: those still collide on a turn that is really two
          // continuations glued together with no user message between them (older
          // conversations, from before this stopped happening unaskedsation on their own) —
          // both restart their own ids at c1, landing two "c9"s inside what `timeline()`
          // reads as one turn. This never repeats, by construction, regardless of what the
          // backend assigned or how the transcript is shaped.
          toolCallId: `call-${callSeq++}`,
          toolName: part.name,
          args: part.arguments,
          argsText: JSON.stringify(part.arguments),
          result: part.result,
        });
      } else if (part.kind === "context") {
        context = part.context;
        // `{}` — not absent — on a turn recorded before this field existed; `window` is
        // always present on a real reading, never on that placeholder. `turn-usage.tsx`
        // falls back to `context` when this is undefined.
        baseline = part.baseline?.window ? (part.baseline as ContextLedger) : undefined;
        folded = part.folded;
        retried = part.retried ?? 0;
      } else {
        rounds.push({ uncached: part.uncached, cached: part.cached, out: part.out });
      }
    }
    // Last, so the figure lands at the foot of the turn and nothing is split around it.
    // Token counts ride back as the same data part the live stream uses, so the footer reads
    // the same on a resumed turn as it did on a fresh one.
    if (rounds.length || context) {
      content.push({
        type: "data",
        name: USAGE_PART,
        data: { rounds, context, baseline, folded, retried },
      });
    }
    if (content.length) out.push({ role: turn.role, content });
  }
  // One cast, at the boundary: the shapes above are the library's own, and its content
  // union narrows by role in a way that defeats inference through a map.
  return out as ThreadMessageLike[];
}
