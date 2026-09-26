import type { ReactNode, Ref } from "react";
import {
  Bell,
  LayoutDashboard,
  PanelLeft,
  PanelRight,
  PenLine,
  Plus,
  SlidersHorizontal,
} from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import type { Presence } from "@/components/shell/chat-states";
import { PresencePill } from "@/components/shell/presence";
import { UpdatePill } from "@/components/shell/update-notice";
import { cn } from "@/lib/utils";

/** How wide the bar's left segment is while the conversation list is hidden: the traffic lights,
 *  the toggle that brings the list back, and the pencil, with room to breathe. */
const SEGMENT_CLOSED = 180;

/**
 * The window's one bar: navigation and presence, and nothing about the next message.
 *
 * It used to be two bars — this one, then a strip of tabs under it — and it carried the
 * per-message settings too: how hard he thinks, what he may touch, which model. Those moved into
 * the composer, because they are about the message you are writing and that is where it is
 * written. The counters for "working elsewhere" and "unsent text" went because the tabs and the
 * conversation list now carry both on the chat they are about, and the theme switch moved into
 * Settings, where a preference you set once belongs.
 *
 * Left to right: a segment exactly as wide as the conversation list under it (the traffic lights,
 * the list's toggle, a new chat), then the open chats as tabs — handed in as a slot, because the
 * layout draws them and only it knows when the window is a single pane — then Kith, the bell, the
 * companion column, the control panel and Settings.
 *
 * Doubles as the title bar in the desktop build: `window-drag-region` makes the empty parts drag
 * the window, and `window-controls-gap` keeps the traffic lights clear. Both do nothing in a
 * browser tab. See index.css → "Desktop window chrome" and "The title bar".
 */
export function AppHeader({
  sidebarOpen,
  sidebarWidth,
  onToggleSidebar,
  onNewConversation,
  tabsSlot,
  presence,
  onPresence,
  unread,
  onOpenInbox,
  companionOpen,
  onToggleCompanion,
  onOpenPanel,
  onOpenSettings,
}: {
  sidebarOpen: boolean;
  /** The conversation list's width, so the segment above it lines up with its edge. */
  sidebarWidth: number;
  onToggleSidebar: () => void;
  onNewConversation: () => void;
  /** Where the layout puts the tab strip when the window is one pane. */
  tabsSlot?: Ref<HTMLDivElement>;
  presence: Presence;
  /** Where the presence pill takes you, if anywhere. */
  onPresence?: () => void;
  unread: number;
  onOpenInbox: () => void;
  /** Whether the chat in front of you has its companion column open. */
  companionOpen: boolean;
  onToggleCompanion: () => void;
  onOpenPanel: () => void;
  onOpenSettings: () => void;
}) {
  return (
    <header
      className={cn(
        "window-drag-region bg-titlebar relative z-10 flex h-[45px] shrink-0 items-stretch",
        /* The rule under the bar, as an inset shadow rather than a border. A shadow is painted
         * under the bar's children, so the active tab — which fills the bar's full height in the
         * thread's own ground — covers it and reads as continuous with the page below. A border
         * would sit outside the tab strip's scroll box, which clips anything that tries to reach
         * it. */
        "shadow-[inset_0_-1px_0_var(--border)]",
      )}
    >
      <div
        className={cn(
          "window-controls-gap kith-titlebar-gap flex shrink-0 items-center gap-1.5 pr-2.5",
          sidebarOpen && "border-border border-r",
        )}
        style={{ width: sidebarOpen ? sidebarWidth : SEGMENT_CLOSED }}
      >
        <BarButton
          tooltip={sidebarOpen ? "Hide the conversation list" : "Show the conversation list"}
          className="size-[30px]"
          onClick={onToggleSidebar}
        >
          <PanelLeft className="size-4" />
        </BarButton>
        <div className="flex-1" />
        <BarButton tooltip="New chat" className="size-[30px]" onClick={onNewConversation}>
          <PenLine className="size-4" />
        </BarButton>
      </div>

      {/* The tabs, when the layout puts them here — see `LayoutView`'s `bar`. With several panes
          each draws its own strip and this is empty title bar: somewhere to drag the window. */}
      <div ref={tabsSlot} className="flex min-w-0 flex-1 items-stretch" />

      <div className="flex shrink-0 items-center gap-1.5 px-3">
        <PresencePill presence={presence} onClick={onPresence} />
        {/* A release you do not have. Absent unless there is one — nothing else in a running Kith
            mentions that versions exist. */}
        <UpdatePill onOpen={onOpenSettings} />
        <BarButton
          tooltip={unread > 0 ? `Alerts, ${unread} unread` : "Alerts"}
          className="relative"
          onClick={onOpenInbox}
        >
          <Bell className="size-4" />
          {unread > 0 ? (
            <span
              aria-hidden
              className="bg-kith text-primary-foreground absolute top-[3px] right-[2px] flex h-[15px] min-w-[15px] items-center justify-center rounded-full px-1 text-[10px] leading-none font-bold"
            >
              {unread > 9 ? "9+" : unread}
            </span>
          ) : null}
        </BarButton>
        {/* The column beside the chat — Activity, context, the task. A toggle, and it says so:
            pressed while the column is open. */}
        <BarButton
          tooltip={companionOpen ? "Hide the companion" : "Show the companion"}
          pressed={companionOpen}
          onClick={onToggleCompanion}
        >
          <PanelRight className="size-4" />
        </BarButton>
        <BarButton tooltip="Control panel" onClick={onOpenPanel}>
          <LayoutDashboard className="size-4" />
        </BarButton>
        <BarButton tooltip="Settings" onClick={onOpenSettings}>
          <SlidersHorizontal className="size-4" />
        </BarButton>
      </div>
    </header>
  );
}

/** "Open another chat", at the end of the title bar's tabs — where a browser puts it, and where
 *  the eye already is after reading along them. Handed to the layout as the strip's `after`, so
 *  it follows the last tab rather than the far edge of the bar. */
export function NewTabButton({ onClick }: { onClick: () => void }) {
  return (
    <TooltipIconButton
      tooltip="Open another chat"
      side="bottom"
      variant="ghost"
      size="icon"
      className="text-muted-foreground/80 hover:text-foreground h-full w-8 shrink-0 rounded-none hover:bg-transparent dark:hover:bg-transparent"
      onClick={onClick}
    >
      <Plus className="size-3.5" />
    </TooltipIconButton>
  );
}

/** One of the bar's square buttons: 32px by default, an 8px corner, the muted ink the bar's
 *  icons share, and a pressed state that lifts it onto the accent ground. */
function BarButton({
  tooltip,
  pressed,
  className,
  onClick,
  children,
}: {
  tooltip: string;
  pressed?: boolean;
  className?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <TooltipIconButton
      tooltip={tooltip}
      side="bottom"
      variant="ghost"
      size="icon"
      aria-pressed={pressed}
      className={cn(
        "size-8 shrink-0 rounded-[8px]",
        pressed
          ? "bg-accent text-foreground hover:bg-accent dark:hover:bg-accent"
          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
        className,
      )}
      onClick={onClick}
    >
      {children}
    </TooltipIconButton>
  );
}
