import { useState } from "react";
import { Bell, Brain, LayoutDashboard, Moon, Settings2, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import { PresenceOrb } from "@/components/presence";
import { moodHue, type Mood } from "@/lib/backend/mood";

/** The presence bar: Kith as a living thing (orb + mood + what he's doing right
 * now), then his inbox, mind, panel, and settings. */
export function AppHeader({
  roaming,
  status,
  mood,
  model,
  unread,
  mindOpen,
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
  unread: number;
  mindOpen?: boolean;
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
          <span className={roaming ? "text-roam" : "text-muted-foreground/60"}>
            {mood?.label ? " · " : ""}
            {roaming ? "roaming" : "here"}
          </span>
          {doing ? <span className="text-muted-foreground/50"> · {doing}</span> : null}
        </span>
      </div>

      <div className="flex-1" />

      {/* Which model is answering. Nothing showed this, and that is how turns ran for
          days on a model nobody had selected — the settings page said one thing and a
          stale client override sent another. A name on screen makes that unmissable. */}
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

      <Button
        variant="ghost"
        size="sm"
        className="relative gap-1.5 text-muted-foreground hover:text-foreground"
        onClick={onOpenInbox}
      >
        <Bell className="size-4" />
        Alerts
        {unread > 0 ? (
          <span className="absolute -top-1 -right-1 flex min-w-4 items-center justify-center rounded-full bg-kith px-1 text-[10px] font-semibold text-primary-foreground shadow-[0_0_10px_var(--kith)]">
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </Button>
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
