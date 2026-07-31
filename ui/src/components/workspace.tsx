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
import { useAutonomy } from "@/hooks/use-autonomy";
import { useMessages } from "@/hooks/use-messages";
import { useMood } from "@/hooks/use-mood";
import { AnyFileAttachmentAdapter } from "@/lib/attachments";
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

  // Any session mid-work. Was a single `running` flag; work belongs to sessions now, so
  // the question is plural and the glow means "something is happening", not "roaming is on".
  const roaming = (autonomy.status?.working ?? []).length > 0;
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
  const wash = roaming ? "var(--roam)" : moodHue(mood?.label);

  return (
    <TooltipProvider>
      <AssistantRuntimeProvider key={threadKey} runtime={runtime}>
        <div className="relative flex h-dvh flex-col overflow-hidden text-foreground">
          {/* Ambient wash — leans green while he roams, amber while he's here. */}
          <div className="kith-ambient" style={{ ["--wash" as string]: wash }} />
          <div className="relative z-10 flex min-h-0 flex-1 flex-col">
            <AppHeader
              roaming={roaming}
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
              mindOpen={mindOpen}
              onOpenInbox={() => {
                inbox.enableNotifications();
                setInboxOpen(true);
              }}
              onOpenMind={() => setMindOpen((o) => !o)}
              onOpenPanel={() => navigate(pathForTab("overview"))}
              onOpenSettings={() => navigate(pathForSettings())}
            />
            <div className="flex min-h-0 flex-1">
              {historyOpen ? (
                <div className="w-64 shrink-0 border-e border-border/60">
                  <HistoryPanel
                    activeId={conversationId}
                    onOpen={(id) => void openConversation(id)}
                    onNew={newConversation}
                    onClose={() => setHistoryOpen(false)}
                  />
                </div>
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
                <Thread />
              </div>
              {/* draggable divider */}
              {mindOpen ? (
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
              {mindOpen ? (
                <MindPanel
                  autonomy={autonomy}
                  conversationId={conversationId}
                  width={mindWidth}
                  onClose={() => setMindOpen(false)}
                />
              ) : null}
            </div>
          </div>
        </div>
        {/* One viewer for the whole app — a path in a message, a deliverable, and the
            file browser all open this. */}
        <WorkspaceFileViewer />
        {inboxOpen ? <InboxPanel inbox={inbox} onClose={() => setInboxOpen(false)} /> : null}
        {route.settingsTab ? (
          <SettingsPage
            tab={route.settingsTab}
            config={config}
            onSelectTab={(t) => navigate(pathForSettings(t))}
            onSaveConfig={onSaveConfig}
            onConnectionSaved={onConnectionSaved}
            onClose={() => navigate(pathForHome())}
          />
        ) : null}
        {panelOpen ? (
          <ControlPanel
            tab={route.tab}
            openTask={route.taskId}
            onSelectTab={(t) => navigate(pathForTab(t))}
            onOpenTask={(id) => navigate(pathForTask(id))}
            onClose={() => navigate(pathForHome())}
          />
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
        // Token counts ride back as the same data part the live stream uses, so the footer
        // reads the same on a resumed turn as it did on a fresh one.
        content.push({
          type: "data",
          name: USAGE_PART,
          data: { rounds: [{ uncached: part.uncached, cached: part.cached, out: part.out }] },
        });
      }
    }
    if (content.length) out.push({ role: turn.role, content });
  }
  // One cast, at the boundary: the shapes above are the library's own, and its content
  // union narrows by role in a way that defeats inference through a map.
  return out as ThreadMessageLike[];
}
