import { useCallback, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";

import { Thread } from "@/components/assistant-ui/thread";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppHeader } from "@/components/app-header";
import { ControlPanel } from "@/components/control-panel";
import { InboxPanel } from "@/components/inbox-panel";
import { MindPanel } from "@/components/mind-panel";
import { useAutonomy } from "@/hooks/use-autonomy";
import { useMessages } from "@/hooks/use-messages";
import { useMood } from "@/hooks/use-mood";
import { moodHue } from "@/lib/backend/mood";
import { parseLocation, pathForHome, pathForTab, pathForTask } from "@/lib/router";
import { createBackendAdapter, type ServerConfig } from "@/lib/backend";

const MIND_MIN = 320;
const MIND_MAX = 720;
const MIND_DEFAULT = 400;

/** The ready-state app: chat runtime, header, and the autonomy ("Mind") panel.
 * Split out so its hooks only run once the backend is reachable. */
export function Workspace({
  config,
  serverDefaults,
  onSaveConfig,
}: {
  config: ServerConfig;
  serverDefaults: ServerConfig;
  onSaveConfig: (config: ServerConfig) => void;
}) {
  // The adapter reads the freshest config through a ref, so the runtime is never
  // recreated (which would drop an in-flight stream).
  const configRef = useRef(config);
  configRef.current = config;
  const adapter = useMemo(() => createBackendAdapter(() => configRef.current), []);
  const runtime = useLocalRuntime(adapter);

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
      <AssistantRuntimeProvider runtime={runtime}>
        <div className="relative flex h-dvh flex-col overflow-hidden text-foreground">
          {/* Ambient wash — leans green while he roams, amber while he's here. */}
          <div className="kith-ambient" style={{ ["--wash" as string]: wash }} />
          <div className="relative z-10 flex min-h-0 flex-1 flex-col">
            <AppHeader
              config={config}
              serverDefaults={serverDefaults}
              onSaveConfig={onSaveConfig}
              roaming={roaming}
              status={autonomy.status?.current ?? null}
              mood={mood}
              unread={inbox.unread}
              mindOpen={mindOpen}
              onOpenInbox={() => {
                inbox.enableNotifications();
                setInboxOpen(true);
              }}
              onOpenMind={() => setMindOpen((o) => !o)}
              onOpenPanel={() => navigate(pathForTab("overview"))}
            />
            <div className="flex min-h-0 flex-1">
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
              {mindOpen ? <MindPanel autonomy={autonomy} width={mindWidth} onClose={() => setMindOpen(false)} /> : null}
            </div>
          </div>
        </div>
        {inboxOpen ? <InboxPanel inbox={inbox} onClose={() => setInboxOpen(false)} /> : null}
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
