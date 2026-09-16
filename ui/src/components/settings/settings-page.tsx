import { useCallback, useEffect, useState } from "react";
import {
  Cpu,
  FileText,
  FolderOpen,
  Gauge,
  Layers,
  Loader2,
  MessageSquare,
  PlugZap,
  Puzzle,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Wrench,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ChatTab } from "@/components/settings/chat-tab";
import { TuningPage } from "@/components/settings/tuning-page";
import { FilesPage } from "@/components/settings/files-page";
import { UpdatesPage } from "@/components/settings/updates-page";
import { McpPage } from "@/components/settings/mcp-page";
import { ModelTab } from "@/components/settings/model-tab";
import { ToolsTab } from "@/components/settings/tools-tab";
import {
  fetchSetup,
  fetchTuning,
  type ServerConfig,
  type SetupSnapshot,
  type TuningSnapshot,
} from "@/lib/backend";
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
  { key: "machine", label: "This machine" },
] as const;

type Group = (typeof GROUPS)[number]["key"];

/**
 * Which of the server's tunable groups each page shows.
 *
 * This table is what dissolved Advanced. The server sends seven groups and Advanced rendered
 * all of them behind a nested tab strip; four pages here render the same groups under names
 * that say what they are. Two of those nested tabs — `chat` and `context` — named a page that
 * already existed in the sidebar, which is the clearest sign the division was right and the
 * placement was not.
 *
 * A group the server adds that nobody has placed lands on `budget` rather than disappearing:
 * the failure of a map like this is a setting that exists, is saved, affects him, and cannot be
 * reached from anywhere in the app.
 */
const TUNING_PAGES: Partial<Record<SettingsTab, readonly string[]>> = {
  /* Mapped on what each group *contains*, not on what it is called. The server's `chat` group
   * is `max_rounds`, `history_keep_recent` and `max_answer_tokens` — pace and history, none of
   * it the reply-length and notification settings the Conversation page already owns through a
   * different endpoint. Putting it under Conversation because of its name would have been two
   * pages disagreeing about who owns "how long an answer is". */
  budget: ["limits", "chat", "connections"],
  context: ["context", "stuck"],
  files: ["machine"],
  mcp: ["mcp"],
};

/** Everything the table above does not claim, so nothing the server sends is unreachable. */
const PLACED = new Set(Object.values(TUNING_PAGES).flat());

const TABS: {
  id: SettingsTab;
  label: string;
  hint: string;
  icon: typeof Cpu;
  group: Group;
  /** The line under the page's own title. Longer than `hint`, which has a sidebar row to fit. */
  blurb: string;
  layout?: "reading" | "wide" | "fill";
}[] = [
  {
    id: "model",
    label: "Model",
    hint: "where he thinks",
    blurb: "Change the model without touching the key; changing the provider clears it.",
    icon: Cpu,
    group: "him",
    layout: "wide",
  },
  {
    id: "persona",
    label: "Persona",
    hint: "who he is, in his own files",
    blurb: "The folder stays the source of truth — edit here or in an editor, whichever you prefer.",
    icon: FileText,
    group: "him",
    layout: "fill",
  },
  {
    id: "chat",
    label: "Conversation",
    hint: "how he replies",
    blurb: "How he replies, and when he may interrupt you. These outlive a model.",
    icon: MessageSquare,
    group: "him",
  },
  {
    id: "tools",
    label: "Tools",
    hint: "what he can reach",
    blurb: "Each one is checked when this page opens — a tool that is off is one he will not try.",
    icon: Wrench,
    group: "use",
  },
  {
    id: "skills",
    label: "Skills",
    hint: "what he knows how to do",
    blurb: "Instructions he reads when the work matches — they cost nothing until then.",
    icon: Puzzle,
    group: "use",
  },
  {
    id: "plugins",
    label: "Plugins",
    hint: "what other people built",
    blurb: "One folder with a kith.plugin.json in it. Their surfaces open beside a chat.",
    icon: PlugZap,
    group: "use",
  },
  {
    id: "mcp",
    label: "MCP servers",
    hint: "tools from somewhere else",
    blurb: "Servers he can call tools on. A server he uses is something he uses.",
    icon: Server,
    group: "use",
    layout: "fill",
  },
  {
    id: "permissions",
    label: "Permissions",
    hint: "what he may do without asking",
    blurb: "The same control as the one in the title bar — this is where it is explained.",
    icon: ShieldCheck,
    group: "limits",
  },
  {
    id: "budget",
    label: "Budget and pace",
    hint: "where a session stops",
    blurb: "Where a session stops on its own. These are the settings that cost money.",
    icon: Gauge,
    group: "limits",
    layout: "fill",
  },
  {
    id: "context",
    label: "Context",
    hint: "what he carries",
    blurb: "What he carries into each round, and when he puts some of it down.",
    icon: Layers,
    group: "limits",
    layout: "fill",
  },
  {
    id: "files",
    label: "Folders and files",
    hint: "where his things live",
    blurb: "Where his things live on this machine.",
    icon: FolderOpen,
    group: "machine",
    layout: "fill",
  },
  {
    id: "updates",
    label: "Updates",
    hint: "what is running",
    blurb: "What is running, and whether there is something newer.",
    icon: SlidersHorizontal,
    group: "machine",
    layout: "fill",
  },
];

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
  /* Fetched here rather than inside the pages that show it, for the sidebar: "how many settings
   * on this page are away from their default" cannot be answered by a page that is not
   * mounted, and it is the number that makes the badge worth having. */
  const [tuning, setTuning] = useState<TuningSnapshot | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchSetup()
      .then(setSnapshot)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
    // A failure here costs the badges and nothing else, so it does not reach `error`: a page
    // that will not open is a worse answer than a page with no counts on it.
    fetchTuning()
      .then(setTuning)
      .catch(() => setTuning(null));
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

  /** The server's groups for one page, in the order `TUNING_PAGES` lists them. Anything the
   *  table does not claim rides along with Budget rather than becoming unreachable. */
  const groupsFor = useCallback(
    (id: SettingsTab) => {
      if (!tuning) return [];
      const wanted = TUNING_PAGES[id];
      if (!wanted) return [];
      const named = wanted
        .map((key) => tuning.groups.find((group) => group.key === key))
        .filter((group): group is NonNullable<typeof group> => Boolean(group));
      const orphans = id === "budget" ? tuning.groups.filter((g) => !PLACED.has(g.key)) : [];
      return [...named, ...orphans];
    },
    [tuning],
  );

  const changedOn = useCallback(
    (id: SettingsTab) =>
      groupsFor(id)
        .flatMap((group) => group.settings)
        .filter((knob) => !knob.isDefault).length,
    [groupsFor],
  );

  const entry = TABS.find((one) => one.id === tab);
  const layout = entry?.layout ?? "reading";
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
        <nav className="border-border/60 bg-sidebar/40 flex w-[264px] shrink-0 flex-col border-r backdrop-blur-sm">
          {/* One search, over every page.
              Advanced had its own, and nothing else did — so "where is the setting for X" had an
              answer on one page out of eight and nowhere else. It filters the sidebar rather
              than opening a results screen: the pages are the answer, and a page that holds no
              match is the useful half of the reply. */}
          <div className="relative shrink-0 p-3">
            <Search className="text-muted-foreground/50 pointer-events-none absolute top-1/2 left-6 size-3.5 -translate-y-1/2" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search every setting"
              aria-label="Search settings"
              className="border-border/60 bg-background focus-visible:border-ring h-8 w-full rounded-[10px] border py-1 pr-3 pl-8 text-[12.5px] outline-none"
            />
          </div>
          <div className="min-w-0 flex-1 overflow-y-auto px-3 pb-3">
          {/* Grouped, and the groups are derived from the panes rather than listed twice.
              A second list would be a second thing to keep in step, and the failure would be a
              pane that quietly stops appearing in the sidebar while still being reachable by
              its URL. */}
          {GROUPS.map((group) => {
            const needle = query.trim().toLowerCase();
            const inside = TABS.filter(
              (one) =>
                one.group === group.key &&
                (!needle ||
                  one.label.toLowerCase().includes(needle) ||
                  one.hint.toLowerCase().includes(needle) ||
                  // The settings themselves, so searching "budget" or "timeout" finds the page
                  // holding it rather than only the pages named after it.
                  groupsFor(one.id).some((g) =>
                    g.settings.some(
                      (knob) =>
                        knob.label.toLowerCase().includes(needle) ||
                        knob.key.toLowerCase().includes(needle),
                    ),
                  )),
            );
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
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{entry.label}</span>
                        <span className="text-muted-foreground/80 block text-[11px] leading-snug">
                          {entry.hint}
                        </span>
                      </span>
                      {/* How many of this page's settings are away from their default. The
                          question you arrive with, answerable on one page before this. */}
                      {changedOn(entry.id) > 0 ? (
                        <span
                          title={`${changedOn(entry.id)} changed from default`}
                          className="bg-kith-soft text-kith mt-0.5 shrink-0 rounded-full px-1.5 font-mono text-[10px]"
                        >
                          {changedOn(entry.id)}
                        </span>
                      ) : null}
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
          </div>
          <UpdateFooter />
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          {/* Every page opens the same way: its name, and one line on what it is for. The pages
              each wrote their own heading before, in their own size, or none at all. */}
          {entry && !error && snapshot ? (
            <div className="shrink-0 px-8 pt-6 pb-3">
              <h1 className="text-[19px] font-semibold tracking-tight">{entry.label}</h1>
              <p className="text-muted-foreground mt-1 max-w-[62ch] text-[12.5px]">
                {entry.blurb}
              </p>
            </div>
          ) : null}

          <div
            className={cn(
              "min-w-0 flex-1",
              filling ? "flex min-h-0 flex-col" : "overflow-y-auto px-8 pb-7",
            )}
          >
            {/* One left edge, one measure.
             *
             * `reading` used to centre itself (`mx-auto max-w-3xl`), which put its content two
             * hundred and seventy pixels right of where the tuning pages start theirs. Clicking
             * down the sidebar moved the whole page sideways under you — eight pages, three
             * different left edges, and nothing in the design asking for any of it. Left-aligned
             * at one width, so only the content changes when you change page.
             *
             * And no cap: a section spans the content area, which is what the design does and
             * what makes a row's control sit at the page's right edge rather than at an
             * arbitrary one partway across it. */}
            <div className={cn(filling ? "flex min-h-0 flex-1 flex-col" : "")}>
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
              ) : tab === "updates" ? (
                <UpdatesPage />
              ) : tab === "files" ? (
                <FilesPage
                  paths={tuning?.paths ?? []}
                  groups={groupsFor("files")}
                  onSaved={load}
                />
              ) : tab === "mcp" ? (
                <McpPage groups={groupsFor("mcp")} onSaved={load} />
              ) : TUNING_PAGES[tab] ? (
                <TuningPage groups={groupsFor(tab)} onSaved={load} />
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
