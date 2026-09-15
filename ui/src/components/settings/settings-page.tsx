import { useCallback, useEffect, useState } from "react";
import {
  Cpu,
  FileText,
  Loader2,
  MessageSquare,
  PlugZap,
  Puzzle,
  Settings2,
  ShieldCheck,
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
import { PermissionsTab } from "@/components/settings/permissions-tab";
import { PersonaTab } from "@/components/settings/persona-tab";
import { PluginsTab } from "@/components/settings/plugins-tab";
import { SkillsTab } from "@/components/settings/skills-tab";
import type { SettingsTab } from "@/lib/router";
import { cn } from "@/lib/utils";
import { Layer, useLayer } from "@/hooks/use-layer";
import { UpdateFooter } from "@/components/shell/update-notice";

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
/**
 * The three questions a person arrives with.
 *
 * **Grouped by whose decision a thing is**, which is the axis that stopped working when plugins
 * arrived. Eight panes in a flat list is a list you read rather than scan, and the old order
 * put Conversation — how he talks — between Tools and Advanced, as if it were about what he can
 * reach.
 *
 * `him` is what he is: the model, the persona, how he replies. `use` is what he has to work
 * with, and it is the group a plugin lands in. `limits` is what you allow and what you have
 * tuned. Order within a group is deliberate: the thing you change most often is first.
 */
const GROUPS = [
  { key: "him", label: "Him" },
  { key: "use", label: "What he can use" },
  { key: "limits", label: "Limits" },
] as const;

type Group = (typeof GROUPS)[number]["key"];

const TABS: {
  id: SettingsTab;
  label: string;
  hint: string;
  icon: typeof Cpu;
  group: Group;
  /** Omitted means the reading measure. */
  layout?: "reading" | "wide" | "fill";
}[] = [
  { id: "model", label: "Model", hint: "where he thinks", icon: Cpu, group: "him", layout: "wide" },
  {
    id: "persona",
    label: "Persona",
    hint: "who he is, in his own files",
    icon: FileText,
    group: "him",
    layout: "fill",
  },
  {
    id: "chat",
    label: "Conversation",
    hint: "reply length and reasoning",
    icon: MessageSquare,
    group: "him",
  },
  { id: "skills", label: "Skills", hint: "what he knows how to do", icon: Puzzle, group: "use" },
  { id: "plugins", label: "Plugins", hint: "what other people built", icon: PlugZap, group: "use" },
  { id: "tools", label: "Tools", hint: "what he can reach", icon: Wrench, group: "use" },
  {
    id: "permissions",
    label: "Permissions",
    hint: "what he may do without asking",
    icon: ShieldCheck,
    group: "limits",
  },
  {
    id: "advanced",
    label: "Advanced",
    hint: "his pace, limits and addresses",
    icon: SlidersHorizontal,
    group: "limits",
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
    /* **Deliberately a takeover, not a pane.** This was converted to `h-full w-full` alongside the
       board and Context, and reverted the same day: at the pane widths its own `minWidth` permits
       (420), Settings does not fit. Its body was left 217px for content that needs 240px, and a
       nested `max-h-[32rem]` scroller was left 167px for 358px — the rows are built from a hard
       `w-32` and `shrink-0` buttons. Because `overflow-y-auto` computes `overflow-x` to `auto`,
       each shortfall appeared as a horizontal scrollbar rather than as a squeeze.
       Tuning the width does not fix it: the layout is proportional, so the pane sometimes gets
       *narrower* as the window grows, and a `minWidth` wide enough to fit (~640) exceeds the
       window beside the chat's 560. Fitting this inside a pane is responsive work on Settings'
       own layout — eight tabs, each with its own idea of width — not a constant. */
    <div className="bg-background text-foreground fixed inset-0 z-30 flex flex-col">
      {/* Ambient wash so the page feels like the same warm room as the rest of the app.
          `fixed inset-0` because `.kith-ambient` no longer positions itself, and this surface is
          window-scoped. */}
      <div className="kith-ambient fixed inset-0 opacity-70" />

      {/* This covers the app header, so it owns the top of the window and reserves
          the window-control space itself. */}
      <header className="window-drag-region window-controls-gap relative z-10 flex items-center gap-3 border-b border-border/60 bg-background/70 px-4 py-2.5 backdrop-blur-xl">
        <span className="bg-kith-soft text-kith ring-kith/20 relative flex size-8 shrink-0 items-center justify-center rounded-xl ring-1">
          <Settings2 className="size-4" />
        </span>
        <div className="min-w-0 leading-tight">
          <div className="text-sm font-semibold tracking-tight">Settings</div>
          <div className="text-muted-foreground hidden text-[11px] sm:block">
            him, what he can use, and what he may do
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
          {/* Grouped, and the groups are derived from the panes rather than listed twice.
              A second list would be a second thing to keep in step, and the failure would be a
              pane that quietly stops appearing in the sidebar while still being reachable by
              its URL. */}
          {GROUPS.map((group) => {
            const inside = TABS.filter((entry) => entry.group === group.key);
            if (!inside.length) return null;
            return (
              <div key={group.key} className="mb-4 last:mb-0">
                <h2 className="text-muted-foreground/60 px-2.5 pb-1 text-[10px] font-semibold tracking-wider uppercase">
                  {group.label}
                </h2>
                <div className="space-y-0.5">
                  {inside.map((entry) => (
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
              </div>
            );
          })}

          {/* Under the nav, so it is on every tab. What is running, and whether there is
              something newer — the version being visible whenever settings are open is the
              thing you want when writing a bug report, and an update notice needs somewhere
              that is not a seventh tab holding one paragraph. */}
          <UpdateFooter />
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
            ) : tab === "plugins" ? (
              <PluginsTab />
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
            ) : tab === "permissions" ? (
              <PermissionsTab />
            ) : (
              <AdvancedTab />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
