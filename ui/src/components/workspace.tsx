import { useCallback, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  AssistantRuntimeProvider,
  SimpleImageAttachmentAdapter,
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
import { useAutonomy } from "@/hooks/use-autonomy";
import { useMessages } from "@/hooks/use-messages";
import { useMood } from "@/hooks/use-mood";
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

/** The ready-state app: chat runtime, header, and the autonomy ("Mind") panel.
 * Split out so its hooks only run once the backend is reachable. */
export function Workspace({
  config,
  serverDefaults,
  onSaveConfig,
  onConnectionSaved,
}: {
  config: ServerConfig;
  serverDefaults: ServerConfig;
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
  // The attach button exists only when the provider says the model takes images. Offering
  // one that silently drops the image, or 400s the turn, is worse than not offering it —
  // and when it is absent he still has the whole computer, which is the better answer for
  // a spreadsheet anyway.
  const attachments = useMemo(
    () => (config.capabilities?.images ? new SimpleImageAttachmentAdapter() : undefined),
    [config.capabilities?.images],
  );
  const runtime = useLocalRuntime(adapter, {
    initialMessages: resumed,
    ...(attachments ? { adapters: { attachments } } : {}),
  });

  const openConversation = useCallback(async (id: string) => {
    const detail = await fetchConversation(id).catch(() => null);
    if (!detail) return;
    setConversationId(id);
    setResumed(toThreadMessages(detail.timeline));
    setThreadKey((n) => n + 1);
  }, []);

  const newConversation = useCallback(() => {
    setConversationId("");
    setResumed([]);
    setThreadKey((n) => n + 1);
  }, []);

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

  const roaming = autonomy.status?.running ?? false;
  // The room glows green while he roams, otherwise the colour of his mood.
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
            serverDefaults={serverDefaults}
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
