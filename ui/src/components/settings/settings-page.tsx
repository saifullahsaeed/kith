import { useCallback, useEffect, useState } from "react";
import {
  Cpu,
  FileText,
  Loader2,
  MessageSquare,
  Puzzle,
  Settings2,
  SlidersHorizontal,
  Wrench,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { AdvancedTab } from "@/components/settings/advanced-tab";
import { ChatTab } from "@/components/settings/chat-tab";
import { ModelTab } from "@/components/settings/model-tab";
import { ToolsTab } from "@/components/settings/tools-tab";
import { fetchSetup, type ServerConfig, type SetupSnapshot } from "@/lib/backend";
import { PersonaTab } from "@/components/settings/persona-tab";
import { SkillsTab } from "@/components/settings/skills-tab";
import type { SettingsTab } from "@/lib/router";
import { cn } from "@/lib/utils";

/**
 * The tabs, and how wide each one wants to be.
 *
 * Width was the shell's decision — one `max-w-3xl` over all of them — which is right
 * only for the tabs that are forms and prose, where a long line is a line you lose your
 * place in. The model tab is a table: eight columns of scores and prices, squeezed into
 * 768px on a window three times that, wrapping every price onto two rows to make room
 * for whitespace on either side. So the tab says, and the shell obeys.
 */
const TABS: {
  id: SettingsTab;
  label: string;
  hint: string;
  icon: typeof Cpu;
  /** Omitted means the reading measure. */
  wide?: boolean;
}[] = [
  { id: "model", label: "Model", hint: "where he thinks", icon: Cpu, wide: true },
  { id: "persona", label: "Persona", hint: "who he is, in his own files", icon: FileText },
  { id: "skills", label: "Skills", hint: "what he knows how to do", icon: Puzzle },
  { id: "tools", label: "Tools", hint: "what he can reach", icon: Wrench },
  { id: "chat", label: "Conversation", hint: "reply length and reasoning", icon: MessageSquare },
  {
    id: "advanced",
    label: "Advanced",
    hint: "his pace, limits and addresses",
    icon: SlidersHorizontal,
  },
];

/**
 * Settings as a page, not a dialog.
 *
 * It was a dialog with every field in one column and a single Save, which was fine
 * when it was four inputs. It now has to hold a provider choice, a live credential
 * check, a picker over hundreds of models, and a search decision — none of which fit
 * in a modal, and none of which should share a Save button with the others.
 *
 * Model and Tools are separate tabs because they are separate decisions with separate
 * consequences: one costs money per token, the other costs money per search or nothing
 * at all. Each tab saves only itself.
 *
 * The snapshot is re-read after any save, because the tabs are not independent even
 * though their saves are — which search providers exist depends on which model provider
 * is connected, and that would otherwise go stale in front of someone.
 */
export function SettingsPage({
  tab,
  config,
  onSelectTab,
  onSaveConfig,
  onConnectionSaved,
  onClose,
}: {
  tab: SettingsTab;
  config: ServerConfig;
  onSelectTab: (tab: SettingsTab) => void;
  onSaveConfig: (config: ServerConfig) => void;
  onConnectionSaved: () => void;
  onClose: () => void;
}) {
  const [snapshot, setSnapshot] = useState<SetupSnapshot | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchSetup()
      .then(setSnapshot)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(load, [load]);

  // Escape closes, like every other overlay in the app.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-background text-foreground">
      {/* Ambient wash so the page feels like the same warm room as the rest of the app. */}
      <div className="kith-ambient opacity-70" />

      {/* This covers the app header, so it owns the top of the window and reserves
          the window-control space itself. */}
      <header className="window-drag-region window-controls-gap relative z-10 flex items-center gap-3 border-b border-border/60 bg-background/70 px-4 py-2.5 backdrop-blur-xl">
        <span className="bg-kith-soft text-kith ring-kith/20 relative flex size-8 shrink-0 items-center justify-center rounded-xl ring-1">
          <Settings2 className="size-4" />
        </span>
        <div className="min-w-0 leading-tight">
          <div className="text-sm font-semibold tracking-tight">Settings</div>
          <div className="text-muted-foreground hidden text-[11px] sm:block">
            where he thinks, what he can reach, and who he is
          </div>
        </div>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="icon"
          className="size-8 hover:text-destructive"
          onClick={onClose}
          aria-label="Close"
        >
          <X className="size-4" />
        </Button>
      </header>

      <div className="relative z-10 flex min-h-0 flex-1">
        <nav className="w-56 shrink-0 overflow-y-auto border-r border-border/60 bg-sidebar/40 px-3 py-4 backdrop-blur-sm">
          <div className="space-y-0.5">
            {TABS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => onSelectTab(entry.id)}
                className={`flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors ${
                  tab === entry.id
                    ? "bg-kith-soft text-foreground"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                }`}
              >
                <entry.icon
                  className={`mt-0.5 size-4 shrink-0 ${tab === entry.id ? "text-kith" : ""}`}
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{entry.label}</span>
                  <span className="text-muted-foreground/80 block text-[11px] leading-snug">
                    {entry.hint}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </nav>

        <div className="min-w-0 flex-1 overflow-y-auto px-6 py-6">
          <div
            className={cn(
              "mx-auto",
              TABS.find((entry) => entry.id === tab)?.wide ? "max-w-none" : "max-w-3xl",
            )}
          >
            {error ? (
              <p className="text-destructive text-sm">{error}</p>
            ) : !snapshot ? (
              <p className="text-muted-foreground flex items-center gap-2 text-sm">
                <Loader2 className="size-4 animate-spin" /> Reading his setup…
              </p>
            ) : tab === "model" ? (
              <ModelTab
                connection={snapshot.connection}
                providers={snapshot.providers}
                onSaved={() => {
                  load();
                  onConnectionSaved();
                }}
              />
            ) : tab === "skills" ? (
              <SkillsTab />
            ) : tab === "tools" ? (
              <ToolsTab
                search={snapshot.search.current}
                options={snapshot.search.options}
                checks={snapshot.checks}
                onSaved={() => {
                  load();
                  onConnectionSaved();
                }}
              />
            ) : tab === "persona" ? (
              <PersonaTab />
            ) : tab === "chat" ? (
              <ChatTab config={config} connection={snapshot.connection} onSave={onSaveConfig} />
            ) : (
              <AdvancedTab />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
