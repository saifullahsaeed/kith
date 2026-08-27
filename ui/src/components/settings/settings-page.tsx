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
import { Layer, useLayer } from "@/hooks/use-layer";

/**
 * The tabs, and how much of the pane each one wants.
 *
 * Width was the shell's decision — one `max-w-3xl` over all of them — which is right
 * only for the tabs that are forms and prose, where a long line is a line you lose your
 * place in. The model tab is a table: eight columns of scores and prices, squeezed into
 * 768px on a window three times that, wrapping every price onto two rows to make room
 * for whitespace on either side. So the tab says, and the shell obeys.
 *
 * That was half the thought. The shell was also deciding, for everyone, that a tab is a
 * *document*: it pads, it centres, and it owns the scrollbar. Right for a form — wrong for
 * the persona tab, which is a two-pane editor. Given a 1500x1330 pane it drew itself 770
 * wide and 640 tall and left the rest of the window as blank background, because a document
 * is as tall as its content and its content was a textarea with a height hard-coded in rem.
 * Widening it would not have helped; there was no width to widen, only an axis the flag
 * could not talk about.
 *
 * So `wide` became `layout`, over both axes:
 *
 *   reading — a measure, the shell scrolls. Forms and prose. The default.
 *   wide    — no measure, the shell scrolls. Tables.
 *   fill    — the whole pane, no padding, no measure; the tab is `h-full` and scrolls its
 *             own regions. Editors, which need to know how tall they are.
 */
const TABS: {
  id: SettingsTab;
  label: string;
  hint: string;
  icon: typeof Cpu;
  /** Omitted means the reading measure. */
  layout?: "reading" | "wide" | "fill";
}[] = [
  { id: "model", label: "Model", hint: "where he thinks", icon: Cpu, layout: "wide" },
  {
    id: "persona",
    label: "Persona",
    hint: "who he is, in his own files",
    icon: FileText,
    layout: "fill",
  },
  { id: "skills", label: "Skills", hint: "what he knows how to do", icon: Puzzle },
  { id: "tools", label: "Tools", hint: "what he can reach", icon: Wrench },
  { id: "chat", label: "Conversation", hint: "reply length and reasoning", icon: MessageSquare },
  {
    id: "advanced",
    label: "Advanced",
    hint: "his pace, limits and addresses",
    icon: SlidersHorizontal,
    // A form, so `reading` looks like the right call and is not. A row here is a label and its
    // help on the left and a number on the right, and the measure was being applied to the
    // *row* — so on a wide window the pane drew itself 768px and put the rest of the screen
    // in the margins, with thirty rows of two-line help stacking into a page you scroll for
    // a long time. The measure belongs to the prose, which keeps it (`max-w-prose` on the two
    // paragraphs in `Row`); the row wants the width, so the field it is about is beside it
    // rather than a wrap away.
    layout: "wide",
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
  const layer = useLayer(Layer.Overlay);
  const [snapshot, setSnapshot] = useState<SetupSnapshot | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchSetup()
      .then(setSnapshot)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(load, [load]);

  /* Escape closes, like every other overlay in the app — but only when Escape has nothing
   * nearer to close.
   *
   * This was bound to `window` with no test at all, so it also fired *through* anything opened
   * on top of it. Open Persona's "Name the fragment" prompt, change your mind, press Escape:
   * Radix dismissed the dialog and this dismissed the whole of Settings behind it, so a
   * cancelled rename dropped you back in the chat. See `useLayer` for who owns a key. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !layer.frontmost()) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const layout = TABS.find((entry) => entry.id === tab)?.layout ?? "reading";
  const filling = layout === "fill" && !error && snapshot !== null;

  return (
    <div className="bg-background text-foreground fixed inset-0 z-30 flex flex-col">
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

        <div
          className={cn("min-w-0 flex-1", filling ? "flex min-h-0" : "overflow-y-auto px-6 py-6")}
        >
          {/* A filling tab gets the pane bare and is trusted with it. Everything else keeps the
              wrapper it had — including the two messages below, which is why `filling` is false
              until there is a snapshot: a one-line "Reading his setup…" pinned to the very corner
              of an unpadded pane is not a layout anyone chose.

              `flex-col` and not the row it was. A row lays its child out along the main axis, so
              the tab became a flex item at `flex: 0 1 auto` and sized to its own content — about
              900px of header text — leaving 600px of the pane empty and looking for all the world
              like the measure was still being applied. A column stretches its children across the
              cross axis, which is the width, which is what "fill" was supposed to mean. */}
          <div
            className={cn(
              filling ? "flex min-h-0 flex-1 flex-col" : "mx-auto",
              filling ? "" : layout === "wide" ? "max-w-none" : "max-w-3xl",
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
              <PersonaTab connection={snapshot.connection} />
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
