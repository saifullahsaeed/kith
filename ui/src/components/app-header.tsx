import { useState } from "react";
import { Bell, Brain, LayoutDashboard, Moon, PanelLeft, Plus, Settings2, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { HeaderControls } from "@/components/header-controls";
import { PresenceOrb } from "@/components/presence";
import { moodHue, type Mood } from "@/lib/backend/mood";
import { cn } from "@/lib/utils";

/** The presence bar: Kith as a living thing (orb + mood + what he's doing right
 * now), then his inbox, mind, panel, and settings. */
export function AppHeader({
  roaming,
  status,
  mood,
  model,
  effort = "",
  supportsEffort = false,
  onEffort,
  unread,
  mindOpen,
  historyOpen,
  onOpenHistory,
  onNewConversation,
  onOpenInbox,
  onOpenMind,
  onOpenPanel,
  onOpenSettings,
}: {
  roaming: boolean;
  status: string | null;
  mood: Mood | null;
  /** The model he is actually thinking with, straight from the server. */
  model?: string;
  effort?: string;
  /** Whether this model accepts a reasoning parameter at all. */
  supportsEffort?: boolean;
  onEffort?: (effort: string) => void;
  unread: number;
  mindOpen?: boolean;
  historyOpen?: boolean;
  onOpenHistory?: () => void;
  onNewConversation?: () => void;
  onOpenInbox: () => void;
  onOpenMind: () => void;
  onOpenPanel: () => void;
  onOpenSettings: () => void;
}) {
  const doing = cleanStatus(status);
  const hue = moodHue(mood?.label);

  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const toggleTheme = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("kith-theme", next ? "dark" : "light");
    } catch {
      /* ignore */
    }
    setDark(next);
  };

  return (
    // Doubles as the window's title bar in the desktop build: drag it to move the
    // window, and window-controls-gap keeps the traffic lights off the presence orb.
    // Both classes do nothing in a browser tab. See index.css → "Desktop window chrome".
    <header className="window-drag-region window-controls-gap relative z-10 flex items-center gap-3 border-b border-border/60 bg-background/60 px-4 py-2.5 backdrop-blur-md">
      {onOpenHistory ? (
        <TooltipIconButton
          tooltip={historyOpen ? "Hide conversations" : "Your conversations"}
          side="bottom"
          variant="ghost"
          size="icon"
          className={cn("size-7", historyOpen && "bg-accent/60 text-foreground")}
          onClick={onOpenHistory}
        >
          <PanelLeft className="size-4" />
        </TooltipIconButton>
      ) : null}
      {onNewConversation ? (
        <TooltipIconButton
          tooltip="New conversation"
          side="bottom"
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={onNewConversation}
        >
          <Plus className="size-4" />
        </TooltipIconButton>
      ) : null}

      <PresenceOrb roaming={roaming} idle={!roaming} size={11} color={hue} />
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="font-medium tracking-tight">Kith</span>
        <span className="truncate font-mono text-[11px] tracking-wide">
          {mood?.label ? (
            <span
              style={{ color: roaming ? undefined : hue }}
              className={roaming ? "text-muted-foreground/80" : ""}
            >
              {mood.label}
            </span>
          ) : null}
          {/* "roaming" was a mode you switched on for the whole machine, and it is gone —
              work belongs to a session now. The word had to go with it, or the header names
              a thing the app no longer has. */}
          <span className={roaming ? "text-roam" : "text-muted-foreground/60"}>
            {mood?.label ? " · " : ""}
            {roaming ? "working" : "here"}
          </span>
          {doing ? <span className="text-muted-foreground/50"> · {doing}</span> : null}
        </span>
      </div>

      <div className="flex-1" />

      {/* Which model is answering. Nothing showed this, and that is how turns ran for
          days on a model nobody had selected — the settings page said one thing and a
          stale client override sent another. A name on screen makes that unmissable. */}
      <HeaderControls
        effort={effort}
        supportsEffort={supportsEffort}
        onEffort={(next) => onEffort?.(next)}
      />

      {model ? (
        <button
          type="button"
          onClick={onOpenSettings}
          title={`${model} — click to change`}
          className="text-muted-foreground/60 hover:text-foreground hidden max-w-[14rem] truncate font-mono text-[11px] transition-colors sm:block"
        >
          {shortModel(model)}
        </button>
      ) : null}

      <span className="bg-border/60 mx-1 hidden h-4 w-px sm:block" aria-hidden />

      {/* An icon, because the word never told you anything the bell doesn't — and the count
          is the only part that ever mattered. The label was costing space in a bar where the
          things that need words (what he is doing, which model, which mode) were being
          truncated to make room for it. */}
      <TooltipIconButton
        tooltip={unread > 0 ? `${unread} unread` : "Alerts"}
        side="bottom"
        variant="ghost"
        size="icon"
        className="relative size-7"
        onClick={onOpenInbox}
      >
        <Bell className="size-4" />
        {unread > 0 ? (
          <span className="bg-kith text-primary-foreground absolute -top-0.5 -right-0.5 flex min-w-3.5 items-center justify-center rounded-full px-1 text-[9px] font-semibold shadow-[0_0_10px_var(--kith)]">
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </TooltipIconButton>
      <Button
        variant="ghost"
        size="sm"
        aria-pressed={mindOpen}
        className={
          mindOpen
            ? "gap-1.5 bg-accent text-foreground"
            : "gap-1.5 text-muted-foreground hover:text-foreground"
        }
        onClick={onOpenMind}
      >
        <Brain className="size-4" />
        Mind
        {roaming ? <span className="ml-0.5 size-1.5 animate-pulse rounded-full bg-roam" /> : null}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="gap-1.5 text-muted-foreground hover:text-foreground"
        onClick={onOpenPanel}
      >
        <LayoutDashboard className="size-4" />
        Panel
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        className="text-muted-foreground hover:text-foreground"
        onClick={toggleTheme}
        aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      >
        {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        className="text-muted-foreground hover:text-foreground"
        onClick={onOpenSettings}
        aria-label="Settings"
      >
        <Settings2 className="size-4" />
      </Button>
    </header>
  );
}

/** Turn the raw autonomy status ("working on: practice cello") into a short,
 * lowercase phrase for the presence line. */
function cleanStatus(status: string | null): string {
  if (!status) return "";
  const text = status.replace(/^working on:\s*/i, "").replace(/^reminder due:\s*/i, "↳ ");
  return text.length > 42 ? text.slice(0, 42) + "…" : text;
}

/** "openai/gpt-5.6-luna" → "gpt-5.6-luna". The vendor prefix is the least useful part
 *  of a name that has to fit in a title bar; the full id is on hover. */
function shortModel(id: string): string {
  const slash = id.lastIndexOf("/");
  return slash === -1 ? id : id.slice(slash + 1);
}
