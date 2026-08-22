"use client";

import { AttachmentUI, UserMessageAttachments } from "@/components/assistant-ui/attachment";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/follow-up-suggestions";
import { AskPrompt } from "@/components/assistant-ui/ask-prompt";
import { RichComposerInput } from "@/components/assistant-ui/composer-input/rich-input";
import { UserMarkdownText } from "@/components/assistant-ui/user-markdown";
import { useSlashCommands } from "@/components/assistant-ui/slash-commands";
import { PermissionPrompt } from "@/components/assistant-ui/permission-prompt";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { TurnStatus, TurnTokens, type TurnUsage } from "@/components/assistant-ui/turn-usage";
import { ContextMeter } from "@/components/assistant-ui/context-meter";
import {
  Reasoning,
  ReasoningContent,
  ReasoningRoot,
  ReasoningText,
  ReasoningTrigger,
} from "@/components/assistant-ui/reasoning";
import { ToolFallback } from "@/components/assistant-ui/tool-fallback";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/tool-group";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { PresenceOrb } from "@/components/shell/presence";
import { useCheckpoints } from "@/components/assistant-ui/checkpoints-context";
import { restoreCheckpoint } from "@/lib/backend/checkpoints";
import { steerTurn, stopTurn } from "@/lib/commands";
import { currentConversation, dropHeld, heldMessage, isHolding, subscribeHolding } from "@/lib/queued-send";
import { copyText } from "@/lib/files";
import { time, when } from "@/lib/dates";
import { ERRAND_PART, STEER_PART, USAGE_PART } from "@/lib/backend/adapter";
import type { ContextLedger } from "@/lib/backend/types";
import { summariseRun } from "@/lib/tool-language";
import { cn } from "@/lib/utils";
import {
  ActionBarMorePrimitive,
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  BranchPickerPrimitive,
  ComposerPrimitive,
  ErrorPrimitive,
  groupPartByType,
  MessagePrimitive,
  SelectionToolbarPrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type ToolCallMessagePartComponent,
  useAuiState,
  useComposerRuntime,
  unstable_useSlashCommandAdapter,
  unstable_useTriggerPopoverScopeContext,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Clock3,
  CopyIcon,
  MicIcon,
  Paperclip,
  MoreHorizontalIcon,
  PencilIcon,
  Reply,
  RefreshCwIcon,
  RotateCcw,
  SquareIcon,
  XIcon,
  BellRing,
} from "lucide-react";
import {
  createContext,
  useContext,
  useRef,
  useState,
  type ComponentType,
  type FC,
  type PropsWithChildren,
  type ReactNode,
  useEffect,
  useSyncExternalStore,
} from "react";

export type ThreadGroupPart = MessagePrimitive.GroupedParts.GroupPart;

/**
 * Optional component overrides for the thread. `AssistantMessage` and
 * `Welcome` replace whole sections; the remaining slots override how the
 * assistant message renders tool calls and part groups. Tool UIs registered
 * by name (toolkit `render`, `useAssistantDataUI`) take precedence over
 * `ToolFallback`.
 */
export type ThreadComponents = {
  AssistantMessage?: ComponentType | undefined;
  Welcome?: ComponentType | undefined;
  ToolFallback?: ToolCallMessagePartComponent | undefined;
  ToolGroup?: ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>> | undefined;
  ReasoningGroup?: ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>> | undefined;
};

export type ThreadProps = {
  components?: ThreadComponents | undefined;
  /** Which conversation is on screen, for the things that have to ask the server about *this*
   *  one — currently the question he is waiting on. */
  conversationId?: string | undefined;
};

const EMPTY_COMPONENTS: ThreadComponents = {};

const ThreadComponentsContext = createContext<ThreadComponents>(EMPTY_COMPONENTS);

// Startup exposes a loading placeholder thread; treat it as a new chat so
// the composer mounts centered. Loads after startup keep the docked layout.
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 && (!s.thread.isLoading || s.threads.isLoading);

export const Thread: FC<ThreadProps> = ({ components = EMPTY_COMPONENTS, conversationId = "" }) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot isEmpty={isEmpty} conversationId={conversationId} />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<{ isEmpty: boolean; conversationId: string }> = ({ isEmpty, conversationId }) => {
  const { Welcome = ThreadWelcome } = useContext(ThreadComponentsContext);

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-transparent @container flex h-full flex-col"
      style={{
        ["--thread-max-width" as string]: "44rem",
        // The card colour, which is lifted from the background in both themes — brighter than
        // the warm paper, lighter than the warm night. It used to be muted at 30% over the
        // background, which in the light room came out within a percent of the background
        // itself: the one control the whole screen exists for was the faintest thing on it.
        ["--composer-bg" as string]: "var(--color-card)",
        // Room to write in. The box holds one line of text in a space sized for several, which
        // is the point: it is where a long message gets composed, and an input that looks like a
        // search field invites a sentence. Padding and radius both grew with the height so the
        // proportions hold — a taller box inside the old 8px inset reads as a text area with a
        // border, not as a surface.
        ["--composer-radius" as string]: "1.75rem",
        ["--composer-padding" as string]: "12px",
      }}
    >
      {/* Global on purpose — it listens on `document`, not on anything inside this subtree, so
          it only has to be mounted once regardless of where the selection happens. */}
      <SelectionQuoteToolbar />
      <ThreadPrimitive.Viewport
        turnAnchor="top"
        // Off by default the moment `turnAnchor` is "top" — which silently also killed "if
        // content grows while I'm already at the bottom, follow it down." A resumed
        // conversation's real height keeps settling for a moment after the initial
        // scroll-to-bottom fires (`content-visibility: auto` placeholders measuring in,
        // tool-result cards expanding), and with this off nothing ever re-corrects, so it can
        // only ever land short of the true bottom on a long history — never past it. The
        // library's own guard (`!(isRunning && hasActiveTopAnchor())`) already excludes an
        // actively-streaming turn, so this doesn't touch the reason `turnAnchor="top"` exists.
        autoScroll
        data-slot="aui_thread-viewport"
        className={cn(
          "relative flex flex-1 flex-col overflow-x-auto overflow-y-scroll scroll-smooth",
          // Only once there is something to scroll: on a new chat the fade would eat the top
          // of the greeting for no reason.
          !isEmpty && "kith-fade-top",
        )}
      >
        {/* The measure moved off this wrapper and onto the composer alone.

            Both used to share one 44rem column, so anything wide a turn produced — a rendered
            diagram, a table, a long command — was drawn inside a reading measure and had to be
            legible at 700px or not at all. A flowchart of thirty nodes is not. The column was
            the right constraint for prose and the wrong one for pictures, and it was being
            applied to the container rather than to the prose.

            The composer keeps it, because an input that grows to 1900px is a worse input. */}
        <div className={cn("flex w-full flex-1 flex-col px-4 pt-4", isEmpty && "justify-center")}>
          <AuiIf condition={isNewChatView}>
            <Welcome />
          </AuiIf>

          <div data-slot="aui_message-group" className="mb-14 flex flex-col gap-y-6 empty:hidden">
            <ThreadPrimitive.Messages>{() => <ThreadMessage />}</ThreadPrimitive.Messages>
          </div>

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer flex flex-col overflow-visible pt-6 pb-4 md:pb-6",
              // A fade, not a fill.
              //
              // This was `bg-background` with a rounded top, which worked while the footer was
              // the composer's own width: a small panel behind the input, obviously deliberate.
              // Now the column is the full pane it became a white slab across the window —
              // white because the app's ambient wash is painted *under* the content layer
              // (`z-10` in workspace.tsx), so any opaque fill above it is the one rectangle on
              // screen with no warmth in it.
              //
              // Almost nothing, on purpose. Enough that a line of reply does not collide with
              // the input as it scrolls past — the messages run the full width now, so there is
              // text moving behind the composer on both sides of it — and not enough to be a
              // surface.
              //
              // The blur that used to do this work is gone. Frosting the strip did stop the
              // collision, but it did it by making a band of his reply unreadable on its way
              // past, which is a strange thing to do to the text someone is in the middle of
              // reading. The gradient alone keeps the last few pixels off the composer's edge
              // and leaves the words legible until they go under it.
              //
              // To remove it altogether, delete this line. Nothing else depends on it: the
              // composer carries its own background and border, so it stays legible over
              // whatever passes underneath.
              !isEmpty && "sticky bottom-0 mt-auto bg-gradient-to-t from-background/45 to-transparent",
            )}
          >
            <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-col gap-4">
              <ThreadFollowupSuggestions />
              <AskPrompt conversationId={conversationId} />
              <PermissionPrompt />
              <Composer conversationId={conversationId} />
              <AuiIf condition={(s) => isNewChatView(s) && s.composer.isEmpty}>
                <ThreadSuggestions />
              </AuiIf>
            </div>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

const ThreadMessage: FC = () => {
  const { AssistantMessage: AssistantMessageComponent = AssistantMessage } =
    useContext(ThreadComponentsContext);
  const role = useAuiState((s) => s.message.role);
  const isEditing = useAuiState((s) => s.message.composer.isEditing);

  if (isEditing) return <EditComposer />;
  if (role === "system") return <SystemMessage />;
  if (role === "user") return <UserMessage />;
  return <AssistantMessageComponent />;
};

/**
 * A turn the harness started, said in the harness's own voice.
 *
 * A reminder firing and a background task finishing both open a turn with a sentence nobody typed.
 * That sentence was recorded as a `user` message, so reopening a conversation showed it as
 * something the person had said — in their bubble, on their side, with an edit pencil offering to
 * change words they never wrote. It reads as the app putting things in your mouth, which is
 * roughly what it was doing.
 *
 * So it is drawn as what it is: centred, quiet, no avatar, no actions, nothing to edit. Narrower
 * than a message and lighter than one, because it is stage direction rather than dialogue — the
 * reason the next thing happened, not a thing anybody said.
 */
const SystemMessage: FC = () => (
  <MessagePrimitive.Root className="aui-system-message mx-auto w-full max-w-(--thread-max-width) px-4 py-2">
    <div className="border-border/50 bg-muted/25 text-muted-foreground/80 rounded-lg border border-dashed px-3 py-2 text-[12.5px] leading-relaxed">
      <span className="text-muted-foreground/50 mb-1 flex items-center gap-1.5 text-[10px] font-medium tracking-[0.08em] uppercase">
        <BellRing className="size-3" aria-hidden />
        Woken
      </span>
      <MessagePrimitive.Parts />
    </div>
  </MessagePrimitive.Root>
);

/**
 * Select some text in a message, and a "Reply" button appears next to it — click it and that
 * excerpt rides along as a quote on the next message you send.
 *
 * Entirely `@assistant-ui/react`'s own `SelectionToolbarPrimitive` / the composer's
 * `Quote`/`QuoteText`/`QuoteDismiss` (see `Composer` below for the other half) — it already
 * does the part that matters (detecting the selection, positioning a portal at it, confining
 * it to one message, wiring the click through to `composer.setQuote`) correctly and for free;
 * this only supplies how the button looks.
 */
const SelectionQuoteToolbar: FC = () => (
  <SelectionToolbarPrimitive.Root>
    <SelectionToolbarPrimitive.Quote asChild>
      <button
        type="button"
        className="border-border/60 bg-popover text-foreground fade-in-0 zoom-in-95 animate-in inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium shadow-lg backdrop-blur-sm duration-100 ease-[cubic-bezier(0.32,0.72,0,1)] hover:bg-accent active:scale-95"
      >
        <Reply className="size-3.5" />
        Reply
      </button>
    </SelectionToolbarPrimitive.Quote>
  </SelectionToolbarPrimitive.Root>
);

const ThreadScrollToBottom: FC = () => {
  return (
    <ThreadPrimitive.ScrollToBottom asChild>
      {/* Centred, floating clear of the composer.

          Two wrong answers first, both worth keeping written down. End-aligned put it *inside*
          the reading column — measured, its right edge and the text column's right edge were the
          same pixel, so it landed on the end of a line and squarely on a wide table. That had
          been the fix for an even earlier version, and it only ever worked while messages sat in
          a 44rem column with empty margins; they run the full width now, so the right margin is
          text. Straddling the composer's top-right corner solved the overlap and traded it for a
          button sitting on the input's own border, which is worse to look at every time you type.

          So: above the composer, centred, with a real gap under it. It floats over the tail of
          the reply when a line reaches that high — that is the accepted cost, and it is the
          arrangement every other assistant has settled on, because the fade is thickest here and
          the eye is already heading for the composer.

          It's always mounted — `disabled` is how the primitive says "you're already at the
          bottom" — so appearing/disappearing is a transition on that attribute, not a mount.
          `disabled:invisible` cut straight to gone with nothing in between; scale+fade over the
          same easing the rest of the app's disclosures use reads as the button arriving rather
          than blinking on, and a lift on hover so it reads as pressable before you press it.

          No `backdrop-blur`: it sits on the composer's own fill, so there is nothing behind it
          worth frosting — and frosting is what was making a band of the reply unreadable
          elsewhere. */}
      <TooltipIconButton
        tooltip="Scroll to bottom"
        variant="outline"
        className={cn(
          "aui-thread-scroll-to-bottom dark:border-border dark:bg-background dark:hover:bg-accent bg-background",
          "absolute -top-11 left-1/2 z-20 -translate-x-1/2 rounded-full p-2 shadow-md",
          "scale-100 opacity-100 transition-[opacity,transform] duration-200 ease-[cubic-bezier(0.32,0.72,0,1)]",
          "hover:scale-110 active:scale-90",
          "disabled:pointer-events-none disabled:scale-50 disabled:opacity-0",
        )}
      >
        <ArrowDownIcon className="size-3.5" />
      </TooltipIconButton>
    </ThreadPrimitive.ScrollToBottom>
  );
};

const ThreadWelcome: FC = () => {
  const hour = new Date().getHours();
  const partOfDay =
    hour < 5
      ? "night"
      : hour < 12
        ? "morning"
        : hour < 17
          ? "afternoon"
          : hour < 21
            ? "evening"
            : "night";

  return (
    <div className="aui-thread-welcome-root mb-8 flex flex-col items-center px-4 text-center [animation:kith-rise_0.5s_ease-out_both]">
      <div className="mb-5 [animation:kith-rise_0.5s_ease-out_both]">
        <PresenceOrb size={22} />
      </div>
      <h1 className="aui-thread-welcome-message-inner text-3xl font-semibold tracking-tight">
        Good {partOfDay}.
      </h1>
      <p className="mt-2 max-w-sm text-sm text-muted-foreground">
        Kith's here — say what's on your mind, or ask what he's been up to.
      </p>
    </div>
  );
};

const ThreadSuggestions: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestions flex w-full flex-wrap items-center justify-center gap-2 px-4">
      <ThreadPrimitive.Suggestions>{() => <ThreadSuggestionItem />}</ThreadPrimitive.Suggestions>
    </div>
  );
};

const ThreadSuggestionItem: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestion-display fade-in slide-in-from-bottom-2 animate-in fill-mode-both duration-200">
      <SuggestionPrimitive.Trigger send asChild>
        <Button
          variant="ghost"
          className="aui-thread-welcome-suggestion text-foreground hover:bg-muted border-border/60 h-auto gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-normal whitespace-nowrap transition-colors"
        >
          <SuggestionPrimitive.Title className="aui-thread-welcome-suggestion-text-1" />
          <SuggestionPrimitive.Description className="aui-thread-welcome-suggestion-text-2 empty:hidden" />
        </Button>
      </SuggestionPrimitive.Trigger>
    </div>
  );
};

/** One row of the slash menu, which scrolls itself into view when the keys reach it.
 *
 *  Arrow keys moved the highlight and the list did not follow, so anything past the fourth
 *  command was selected invisibly — the keys "worked" and you could not see what they had
 *  landed on, which is worse than them not working.
 *
 *  A MutationObserver on the element's own `data-highlighted`, because the library sets that
 *  attribute directly and there is no callback to subscribe to. `block: "nearest"` so it moves
 *  the list by the minimum needed rather than jumping the selection to the middle.
 */
function SlashItem({
  item,
  children,
}: {
  item: Parameters<typeof ComposerPrimitive.Unstable_TriggerPopoverItem>[0]["item"];
  children: ReactNode;
}) {
  const row = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const el = row.current;
    if (!el) return;
    const follow = () => {
      if (el.hasAttribute("data-highlighted")) el.scrollIntoView({ block: "nearest" });
    };
    follow();
    const watch = new MutationObserver(follow);
    watch.observe(el, { attributes: true, attributeFilter: ["data-highlighted"] });
    return () => watch.disconnect();
  }, []);

  return (
    <ComposerPrimitive.Unstable_TriggerPopoverItem
      ref={row}
      item={item}
      className="data-[highlighted]:bg-accent flex w-full items-baseline gap-2.5 rounded-lg px-2.5 py-1.5 text-left"
    >
      {children}
    </ComposerPrimitive.Unstable_TriggerPopoverItem>
  );
}

/** Everything in the menu that is chrome rather than behaviour — mounted only while it is open.
 *
 *  `TriggerPopover` renders `open ? <div {...props}>{children}</div> : children`: closed, the
 *  children are still there, just stripped of the container that positions and paints them. It
 *  has to be that way — the `Action` child registers the behaviour, and a trigger with no
 *  registered behaviour never opens, so the behaviour child cannot be conditional on being open.
 *
 *  The items look after themselves (`TriggerPopoverItems` returns null when closed). Nothing else
 *  did, so the hint strip and the scroll box rendered in normal flow *inside* the composer with
 *  none of the popover's styling: "↑↓ move ↵ run esc dismiss" and a hairline sitting on top of an
 *  empty input, after every dismiss and after every command that ran.
 */
function SlashPanel({ children }: { children: ReactNode }) {
  const { open } = unstable_useTriggerPopoverScopeContext();
  return open ? <>{children}</> : null;
}

/** The highlighted command's description in full, in a band of its own under the list.
 *
 *  This was a `title` on the row — a native tooltip, which is the OS's to place and the OS's to
 *  paint. Grey, square, its own font, and on a menu anchored to the bottom of the window there is
 *  no room beneath the pointer, so it flipped upwards and covered the six commands above the one
 *  you were reading about. You could not see the list while learning what was on it.
 *
 *  Below the list it covers nothing, and it follows the *highlight* rather than the pointer, so
 *  arrowing through the commands reads them out too — the keyboard path had no description at all.
 *  Three lines: a skill description is written to tell a model when to reach for the thing, and
 *  the sentence that says so is always the first one.
 */
function SlashDetail() {
  const { items, highlightedIndex } = unstable_useTriggerPopoverScopeContext();
  const description = items[highlightedIndex]?.description;
  if (!description) return null;
  return (
    <p className="border-border/50 text-muted-foreground/80 line-clamp-3 shrink-0 border-t px-2.5 py-1.5 text-[11px] leading-relaxed">
      {description}
    </p>
  );
}

/** `conversationId` is not decoration here: `/fold` and `/stop` are addressed to a conversation,
 *  and without one they short-circuit into a note. `useSlashCommands()` was called bare, so the
 *  id was always `""` — every `/fold` answered "this conversation has not started" over a window
 *  that was 58% full, and every `/stop` returned an empty note, which looks exactly like a
 *  keystroke that did nothing. */
const Composer: FC<{ conversationId: string }> = ({ conversationId }) => {
  const { commands, note, reading } = useSlashCommands(conversationId);
  const slash = unstable_useSlashCommandAdapter({ commands, removeOnExecute: true });
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      {/* Anchored to the composer, not floated over the conversation. */}
      <ThreadScrollToBottom />
      {/* No `AttachmentDropzone` around this. It made this box the only place in the window
          that would take a file, which is the smallest and least obvious target on screen —
          and keeping it alongside the window-wide handler in `DropZone` would mean a file let
          go on the composer landed twice. One listener, one path, the whole window. */}
      <div
        data-slot="aui_composer-shell"
        // A full-strength border and a real focus ring, rather than border/60 and a shadow.
        // On warm paper a 60% border over a card that barely differs from the background was
        // a suggestion of an input; you had to know it was there.
        className="border-border dark:border-muted-foreground/25 dark:focus-within:border-muted-foreground/40 focus-within:border-ring/70 focus-within:ring-ring/25 relative flex w-full flex-col gap-2 rounded-(--composer-radius) border bg-(--composer-bg) p-(--composer-padding) shadow-[0_4px_16px_-8px_rgba(0,0,0,0.10),0_1px_2px_rgba(0,0,0,0.05)] transition-[border-color,box-shadow] focus-within:ring-[3px] focus-within:shadow-[0_8px_28px_-10px_rgba(0,0,0,0.14),0_1px_2px_rgba(0,0,0,0.06)] dark:shadow-none"
      >
        <QueuedNotice />
        <ComposerQuoteStrip />
        <ComposerAttachmentStrip />
        {/* Four parts, and it needs all four — spreading the adapter hook's return onto the
            popover, which is what its own example implies, throws
            `useTriggerPopoverRootContext must be used within TriggerPopoverRoot` and takes the
            composer down with it. A Root to hold the trigger state, the popover keyed on the
            character, exactly one behaviour child (`Action` runs a command; `Directive` is for
            mentions, which insert text), and an explicit item list — it renders nothing at all
            without that last one. */}
        {/* The Root wraps the input as well as the popover, and that is the bit I got wrong
            twice. The trigger watches what is being typed; the input has to be inside the same
            Root for it to see anything at all. Wrapped around only the popover, the menu mounts
            and never opens — no error, no missing part, nothing to notice. */}
        <ComposerPrimitive.Unstable_TriggerPopoverRoot>
            <ComposerPrimitive.Unstable_TriggerPopover
              char="/"
              adapter={slash.adapter}
              // Inside the box, not on top of it.
              //
              // Twice now this has been a card of its own: first floating two rems above the
              // input, then flush against it wearing a copy of the composer's fill, border and
              // radius. Copying the box is not being the box. Two borders still met at the seam,
              // the composer's rounded top corners still cut their notches out of it, the focus
              // ring still drew a line straight through the middle, and the whole thing still sat
              // in its own layer — so it read, correctly, as a second object bolted on top.
              //
              // No fill, no border, no radius, no shadow, no `absolute`: an ordinary flex child
              // of the composer shell, above the input, inheriting the one background and the one
              // border that were already there. A single hairline divides it from the input. The
              // box grows upward because there is more inside it, which is the whole idea — and
              // the composer sits in a `sticky bottom-0` footer, so upward is where it grows.
              className="border-border/50 flex max-h-72 shrink-0 flex-col overflow-hidden border-b"
            >
            <ComposerPrimitive.Unstable_TriggerPopover.Action {...slash.action} />
            <SlashPanel>
              {/* No padding of its own any more — the shell already pads, and a second inset
                  inside the first is what made this look like a panel sitting in a box. The rows
                  carry `px-2.5`, the same as the input, so a command name starts exactly where
                  the placeholder does. */}
              <div className="min-h-0 flex-1 overflow-y-auto py-0.5">
                <ComposerPrimitive.Unstable_TriggerPopoverItems>
                  {(items) =>
                    items.map((item) => (
                      <SlashItem key={item.id} item={item}>
                        <span className="text-foreground shrink-0 font-mono text-xs">
                          /{item.label ?? item.id}
                        </span>
                        {item.description ? (
                          // Clamped to one line. A skill's description is written for him — it
                          // tells a model when to reach for the thing, at paragraph length — and
                          // `/code-refactor` rendered eleven lines of it, which pushed every
                          // other command off the screen. The rest is in `SlashDetail`, below.
                          <span className="text-muted-foreground/80 min-w-0 flex-1 truncate text-[11px]">
                            {item.description}
                          </span>
                        ) : null}
                      </SlashItem>
                    ))
                  }
                </ComposerPrimitive.Unstable_TriggerPopoverItems>
              </div>
              <SlashDetail />
              {/* The keys, said out loud. They already worked — the library binds them — but a
                  menu that does not mention them is a menu people click. */}
              <p className="border-border/50 text-muted-foreground/50 flex shrink-0 gap-3 border-t px-2.5 py-1.5 font-mono text-[10px]">
                <span>↑↓ move</span>
                <span>↵ run</span>
                <span>esc dismiss</span>
              </p>
            </SlashPanel>
            </ComposerPrimitive.Unstable_TriggerPopover>
        {/* Not `ComposerPrimitive.Input` any more. The value in the store is still a markdown
            string — nothing downstream knows the difference — but a textarea cannot show a table
            as a table, and a message with structure in it was being written blind. The paste
            handling that used to live here (a screenshot on the clipboard, which Cmd-V otherwise
            drops silently) moved into `paste.ts` along with the rest of the rules. */}
        <RichComposerInput placeholder="say something to Kith…" autoFocus />
        </ComposerPrimitive.Unstable_TriggerPopoverRoot>
        <ComposerAction conversationId={conversationId} />
      </div>
      {/* A command has no reply to appear in, so it says what it did here. Without this,
          `/fold` was indistinguishable from a keystroke that did nothing. */}
      {note ? <p className="text-muted-foreground/70 px-2 pt-1 text-[11px]">{note}</p> : null}
      {/* The standing meter moved to the Work panel, itemised — see `chat/context-section`. What
          stays here is the one thing the panel cannot say: the reading straight after a `/fold`.
          A fold takes no reading of its own, so the thread's latest usage still describes the
          conversation as it was *before* the fold until the next turn reports in — which is why
          `/fold` returns one, and why showing it is the difference between the command looking
          like it worked and looking like it did nothing. Transient by construction: `reading` is
          set by the fold and nothing else ever sets it. */}
      {reading ? <ComposerMeter afterFold={reading} /> : null}
    </ComposerPrimitive.Root>
  );
};

/**
 * How full the window is right now — not attached to any one reply, because a meter that
 * only exists on the last message you happened to send disappears the moment you stop
 * sending them, which is exactly when "how much room do I have left" is worth checking.
 * One meter, here, always current; not one per message any more (see turn-usage.tsx).
 *
 * Reads it off the latest assistant message's own usage data rather than a separate
 * fetch — the number already exists, live, the instant a turn reports it in.
 *
 * Always `context` — the most recent reading, whatever round it came from — never
 * `baseline`. Preferring `baseline` at rest was the previous fix for a *different* bounce:
 * before tool calls were replayed across turns at all, a new turn's first reading was
 * always tiny, so showing this turn's own peak made it look like it dropped the instant
 * the next message was sent. Now that a turn's tool history is replayed in full
 * (`conversations.full_messages`), that reason is largely gone — and holding `baseline`
 * bought a worse version of the same complaint: a turn that did real work climbs live,
 * then the second it finishes, the number reverts to what it was *before that work
 * happened*, which reads as having lost everything it just did. `context` is the number
 * that turn actually ended on; it stays put until the next one has something newer to say.
 */
const ComposerMeter: FC<{ afterFold?: ContextLedger }> = ({ afterFold }) => {
  const messages = useAuiState((s) => s.thread.messages);
  const usage = latestUsage(messages);
  // `afterFold` wins while it exists, and it exists only between a `/fold` finishing and the
  // next turn starting. Every reading here is taken by a turn on its way past; a fold takes
  // none, so the last turn's figure went on describing a conversation that had just been
  // rewritten — the meter sat at 58% of a window the fold had emptied, which reads as the
  // command having done nothing at all. See `_reading_after_fold` for how the number is got.
  const reading = afterFold ?? usage?.context ?? usage?.baseline;
  if (!reading) return null;
  return (
    <div className="mt-1.5 flex justify-center">
      <ContextMeter context={reading} folded={afterFold ? true : usage?.folded} />
    </div>
  );
};

/** The most recent assistant turn's usage data part, walking back from the end — there is
 * at most one per turn (see `USAGE_PART`), and an in-progress turn's is exactly as current
 * as the round that just landed.
 *
 * Exported because the Work panel reads the same figure. One reader rather than two: this walks
 * the thread from the end looking for one part shape, and a second copy of that in the panel is a
 * second thing to get wrong when the shape changes. */
export function latestUsage(messages: readonly unknown[]): TurnUsage | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i] as { role?: string; content?: unknown[] };
    if (message.role !== "assistant") continue;
    for (const part of message.content ?? []) {
      const candidate = part as { type?: string; name?: string; data?: TurnUsage };
      if (candidate.type === "data" && candidate.name === USAGE_PART && candidate.data) {
        return candidate.data;
      }
    }
  }
  return undefined;
}

/**
 * How to send, while there is nothing to send.
 *
 * Enter-sends and shift-Enter-newlines is a convention, not a law, and the composer is a
 * multi-line box with a send button — which is exactly the shape that makes someone type a
 * paragraph, press Enter for the next line, and post half a thought. Only while the box is
 * empty: once you are typing you already know.
 *
 * On the action row, immediately left of the paperclip, rather than centred on a line of its
 * own below the shell. That line was empty space the rest of the time, so the composer changed
 * height the moment you typed a character — and the hint is about the two keys sitting beside
 * the send button, so it belongs in the same cluster as them.
 */
/**
 * A ⌘⏎ message, held until the turn ends.
 *
 * Nothing said this before. The wait happens inside the adapter, *before* the fetch, so
 * assistant-ui had already put the message in the thread and was showing the turn as running
 * while nothing had been sent — for up to twenty minutes. A queued message and a message that
 * got no reply looked exactly alike, and only one of them is fine.
 */
const QueuedNotice: FC = () => {
  const holding = useSyncExternalStore(subscribeHolding, isHolding, () => false);
  const text = useSyncExternalStore(subscribeHolding, heldMessage, () => "");
  if (!holding) return null;
  return (
    <div className="border-border/60 bg-muted/40 mb-1.5 flex items-start gap-2 rounded-lg border px-3 py-2">
      <Clock3 className="text-muted-foreground/70 mt-0.5 size-3 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="text-muted-foreground text-[11px]">
          Queued — goes as soon as this turn finishes.
        </div>
        {/* The words themselves, not just the fact of them. A notice that says something is
            waiting without showing what is a notice you have to take on trust. */}
        {text ? (
          <p className="text-foreground/70 mt-0.5 line-clamp-2 text-[12px]">{text}</p>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => dropHeld()}
        className="text-muted-foreground/50 hover:text-destructive shrink-0 text-[11px] transition-colors"
      >
        Cancel
      </button>
    </div>
  );
};

/**
 * What the keys do, said at the moment it is worth saying.
 *
 * **Shown while he is working even once you have typed**, which is the change that matters. The
 * whole hint used to be gated on an empty composer, so the two keys that differ mid-turn — ⏎
 * steers the running turn, ⌘⏎ waits for it to finish — were only ever legible when there was
 * nothing to send with them. The instant you typed the correction that makes the choice real,
 * the only thing that explained the choice disappeared. ⌘⏎ was undiscoverable by construction.
 *
 * Idle keeps saying ⏎ / ⇧⏎ and nothing else, because ⌘⏎ idle is not a third option: the flag is
 * set, `waitUntilIdle` finds nothing running and returns at once, and the message sends exactly
 * as ⏎ would. Listing "after this" with no *this* would be teaching a key that does nothing.
 */
const ComposerHint: FC = () => (
  <AuiIf
    condition={(s) =>
      s.thread.isRunning || (s.composer.isEmpty && s.composer.attachments.length === 0)
    }
  >
    <div className="text-muted-foreground/40 pointer-events-none me-1 flex items-center gap-3 text-[10px] select-none">
      <AuiIf condition={(s) => !s.thread.isRunning}>
        <span>
          <kbd className="font-sans">⏎</kbd> send
        </span>
        <span aria-hidden>·</span>
        <span>
          <kbd className="font-sans">⇧⏎</kbd> new line
        </span>
      </AuiIf>
      {/* Named by what each does to the turn in flight, not by "send" twice. Steering keeps
          everything the turn has found; queuing starts fresh once it is over. */}
      <AuiIf condition={(s) => s.thread.isRunning}>
        <span>
          <kbd className="font-sans">⏎</kbd> steer now
        </span>
        <span aria-hidden>·</span>
        <span>
          <kbd className="font-sans">⌘⏎</kbd> send after
        </span>
      </AuiIf>
    </div>
  </AuiIf>
);

/**
 * What is attached to the message you are writing.
 *
 * Rendered above the input rather than below it, so adding an image does not push the caret
 * you are typing in. Nothing shows when nothing is attached.
 *
 * It used to say the strip was "absent for a model that cannot take images, because the
 * workspace only installs the adapter when the provider says the model accepts one". That
 * stopped being true when the adapter became unconditional and started accepting any file:
 * a spreadsheet could not be attached at all, and on a model without vision the paperclip
 * simply vanished. Whether to inline a picture or hand him a path is decided on the server,
 * next to the model config — not here by hiding a button.
 */
/** The other half of `SelectionQuoteToolbar`: what a quote looks like once it's actually
 *  attached, so it's obvious what's about to be sent along with the message rather than a
 *  silent extra a person only discovers after sending. `ComposerPrimitive.Quote` already
 *  renders nothing when there's no quote set, so no extra `AuiIf` here. */
const ComposerQuoteStrip: FC = () => (
  <ComposerPrimitive.Quote className="border-border/50 bg-muted/40 flex items-start gap-2 rounded-lg border-s-2 border-s-kith/60 px-2.5 py-1.5 text-xs">
    <Reply className="text-muted-foreground/60 mt-0.5 size-3.5 shrink-0" />
    <ComposerPrimitive.QuoteText className="text-muted-foreground min-w-0 flex-1 line-clamp-2 leading-relaxed" />
    <ComposerPrimitive.QuoteDismiss
      aria-label="Remove quote"
      className="text-muted-foreground/60 hover:text-foreground shrink-0"
    >
      <XIcon className="size-3.5" />
    </ComposerPrimitive.QuoteDismiss>
  </ComposerPrimitive.Quote>
);

const ComposerAttachmentStrip: FC = () => (
  <AuiIf condition={(s) => s.composer.attachments.length > 0}>
    <div className="flex flex-wrap gap-1.5 px-1 pt-1">
      <ComposerPrimitive.Attachments>{() => <AttachmentUI />}</ComposerPrimitive.Attachments>
    </div>
  </AuiIf>
);

const AttachButton: FC = () => {
  const composer = useComposerRuntime();
  const input = useRef<HTMLInputElement>(null);
  const capable = useAuiState((s) => s.thread.capabilities.attachments);
  if (!capable) return null;
  return (
    <>
      {/* No `accept`, deliberately. This was hardcoded to `image/*`, and it is what actually
          greyed out every PDF and zip in the file dialog — widening the adapter changed
          nothing, because the picker's filter lives here and not there. Left off so the dialog
          offers everything and the adapter decides what it takes, which is the one place that
          decision belongs. */}
      <input
        ref={input}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => {
          for (const file of Array.from(event.target.files ?? []))
            void composer.addAttachment(file);
          event.target.value = "";
        }}
      />
      <TooltipIconButton
        tooltip="Attach a file"
        side="bottom"
        type="button"
        variant="ghost"
        size="icon"
        className="size-7 rounded-full"
        onClick={() => input.current?.click()}
      >
        <Paperclip className="size-4" />
      </TooltipIconButton>
    </>
  );
};

/**
 * Send, while he is working — which means steer, and must not go near the runtime.
 *
 * `ComposerPrimitive.Send` cannot be used here. Everything it sends goes through
 * `performRoundtrip`, whose first line is `abortController.abort()`: the run in flight is
 * cancelled, the adapter's abort handler posts `/stop`, and the turn dies. A button labelled
 * "send" that quietly stops the work it was meant to redirect is worse than no button, which is
 * what was here before.
 *
 * So this is the same path the Enter key takes — post the text to the running turn, clear the
 * box — and the two are deliberately identical, because a keystroke and a button that claim to
 * do the same thing should.
 */
const SteerButton: FC = () => {
  const composer = useComposerRuntime();
  const text = useAuiState((s) => s.composer.text);
  const canSend = useAuiState((s) => !s.composer.isEmpty);

  return (
    <TooltipIconButton
      tooltip="Send to the turn he is running — it keeps what it has found"
      side="bottom"
      type="button"
      variant="default"
      size="icon"
      disabled={!canSend}
      className="aui-composer-send size-7 rounded-full"
      aria-label="Steer this turn"
      onClick={() => {
        const where = currentConversation();
        if (!where || !text.trim()) return;
        void steerTurn(where, text);
        composer.setText("");
      }}
    >
      <ArrowUpIcon className="aui-composer-send-icon size-4.5" />
    </TooltipIconButton>
  );
};

const ComposerAction: FC<{ conversationId: string }> = ({ conversationId }) => {
  return (
    <div className="aui-composer-action-wrapper relative flex items-center justify-end">
      <div className="flex items-center gap-1.5">
        <ComposerHint />
        <AttachButton />
        <AuiIf condition={(s) => s.thread.capabilities.dictation}>
          <AuiIf condition={(s) => s.composer.dictation == null}>
            <ComposerPrimitive.Dictate asChild>
              <TooltipIconButton
                tooltip="Voice input"
                side="bottom"
                type="button"
                variant="ghost"
                size="icon"
                className="aui-composer-dictate size-7 rounded-full"
                aria-label="Start voice input"
              >
                <MicIcon className="aui-composer-dictate-icon size-4" />
              </TooltipIconButton>
            </ComposerPrimitive.Dictate>
          </AuiIf>
          <AuiIf condition={(s) => s.composer.dictation != null}>
            <ComposerPrimitive.StopDictation asChild>
              <TooltipIconButton
                tooltip="Stop dictation"
                side="bottom"
                type="button"
                variant="ghost"
                size="icon"
                className="aui-composer-stop-dictation text-destructive size-7 rounded-full"
                aria-label="Stop voice input"
              >
                <SquareIcon className="aui-composer-stop-dictation-icon size-3.5 animate-pulse fill-current" />
              </TooltipIconButton>
            </ComposerPrimitive.StopDictation>
          </AuiIf>
        </AuiIf>
        {/* One button, and what it does depends on whether you have typed anything.
         *
         * It was two while a turn ran — a square Stop beside an arrow Send — which is a choice
         * presented at the moment you are least interested in making one. Every other assistant
         * settles this the same way and it is the right way: an empty box while something is
         * running can only mean stop, and a box with words in it can only mean send them.
         *
         * The two intents are still both reachable, because they are still different: Stop ends
         * the run and loses what it found; sending steers, and keeps every tool result so far.
         * You get whichever one your input is already asking for. */}
        <AuiIf condition={(s) => s.thread.isRunning && s.composer.isEmpty}>
          {/* Stop says so, rather than being inferred from a closed connection.
              `Cancel` stops this window reading; the `/stop` it posts is what ends the turn on the
              server. Two actions because they are two different things — which is the distinction
              the adapter used to collapse, so that leaving a conversation killed the work in it.
              Order does not matter: neither depends on the other. */}
          <ComposerPrimitive.Cancel asChild>
            <TooltipIconButton
              tooltip="Stop — ends the turn and loses what it found"
              side="bottom"
              type="button"
              variant="ghost"
              size="icon"
              className="aui-composer-cancel size-7 rounded-full"
              aria-label="Stop generating"
              onClick={() => {
                if (conversationId) void stopTurn(conversationId);
              }}
            >
              <SquareIcon className="aui-composer-cancel-icon size-3.5 fill-current" />
            </TooltipIconButton>
          </ComposerPrimitive.Cancel>
        </AuiIf>
        <AuiIf condition={(s) => s.thread.isRunning && !s.composer.isEmpty}>
          <SteerButton />
        </AuiIf>
        <AuiIf condition={(s) => !s.thread.isRunning}>
          <ComposerPrimitive.Send asChild>
            <TooltipIconButton
              tooltip="Send message"
              side="bottom"
              type="button"
              variant="default"
              size="icon"
              className="aui-composer-send size-7 rounded-full"
              aria-label="Send message"
            >
              <ArrowUpIcon className="aui-composer-send-icon size-4.5" />
            </TooltipIconButton>
          </ComposerPrimitive.Send>
        </AuiIf>
      </div>
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root border-destructive bg-destructive/10 text-destructive dark:bg-destructive/5 mt-2 rounded-md border p-3 text-sm dark:text-red-200">
        <ErrorPrimitive.Message className="aui-message-error-message line-clamp-2" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

const AssistantMessage: FC = () => {
  const {
    ToolFallback: ToolFallbackComponent = ToolFallback,
    ToolGroup,
    ReasoningGroup,
  } = useContext(ThreadComponentsContext);

  const ACTION_BAR_PT = "pt-1.5";
  // Keep the action bar inside the contained root's paint box, then cancel its reserved space in flow.
  const ACTION_BAR_HEIGHT = `min-h-7.5 ${ACTION_BAR_PT}`;

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="fade-in slide-in-from-bottom-1 animate-in relative -mb-7.5 pb-7.5 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <div data-slot="aui_assistant-message-name" className="mb-1.5 flex items-center gap-2 px-2">
        <PresenceOrb size={7} />
        <span className="font-mono text-[11px] tracking-wide text-kith/90">Kith</span>
      </div>
      <div
        data-slot="aui_assistant-message-content"
        className="text-foreground px-2 leading-relaxed wrap-break-word"
      >
        <MessagePrimitive.GroupedParts
          groupBy={groupPartByType({
            reasoning: ["group-chainOfThought", "group-reasoning"],
            "tool-call": ["group-chainOfThought", "group-tool"],
            "standalone-tool-call": [],
          })}
        >
          {({ part, children }) => {
            switch (part.type) {
              case "group-chainOfThought":
                // A rail, so the reply reads as prose and the machinery reads as a log beside
                // it. Reasoning and tool rows used to sit at the same weight and indent as the
                // writing, which left three kinds of thing competing for the same column and
                // the least interesting of them — a collapsed disclosure — often winning.
                return (
                  <div
                    data-slot="aui_chain-of-thought"
                    // `border-foreground/10` rather than the token: `--border` is already only
                    // 12% alpha, so the usual `border-border/50` computed to six percent and
                    // the rail was there in the DOM and invisible on the screen. Neutral
                    // rather than amber — the rail is a margin, and amber is the colour of
                    // what he says.
                    className="border-foreground/20 my-2 space-y-0.5 border-s ps-3.5"
                  >
                    {children}
                  </div>
                );
              case "group-tool":
                if (ToolGroup) {
                  return <ToolGroup group={part}>{children}</ToolGroup>;
                }
                // A run of one is not a run. Wrapping a single call in "1 tool call ›" put a
                // row that says nothing in front of the row that says what he did.
                if (part.indices.length <= 1) return children;
                return <ToolRun group={part}>{children}</ToolRun>;
              case "group-reasoning": {
                if (ReasoningGroup) {
                  return <ReasoningGroup group={part}>{children}</ReasoningGroup>;
                }
                // Ghost, not the default bordered card, when it does get the full disclosure —
                // a collapsed "Reasoning" used to be the heaviest object in a turn for the part
                // of it a reader is least often after. `ReasoningRun` decides, by length,
                // whether this one is worth a disclosure at all.
                return <ReasoningRun group={part}>{children}</ReasoningRun>;
              }
              case "text":
                return <MarkdownText />;
              case "reasoning":
                return <Reasoning {...part} />;
              case "tool-call":
                return part.toolUI ?? <ToolFallbackComponent {...part} />;
              case "data":
                // The adapter turns the turn's stats into one of these. It is drawn in the
                // message footer rather than here — it is metadata about the turn, and a
                // number in the body reads as something he said. Every other data part is
                // somebody else's and keeps the library's renderer.
                if (part.name === USAGE_PART) return null;
                // What you said into the running turn, drawn where it actually landed. It sits
                // inside his message because that is the truth of it — it went into the prompt
                // mid-turn, between two of his rounds, not before the turn began.
                if (part.name === STEER_PART) {
                  const said = (part.data as { text?: string } | undefined)?.text ?? "";
                  return said ? <SteeredIn text={said} /> : null;
                }
                // An errand he sent away, arriving back. Same placement rule as a steer and
                // the opposite voice: that one is his person interrupting, this is a result he
                // asked for turning up late.
                if (part.name === ERRAND_PART) {
                  const found = (part.data as { text?: string } | undefined)?.text ?? "";
                  return found ? <ErrandBack text={found} /> : null;
                }
                return part.dataRendererUI;
              case "indicator":
                // The bare dot means "working". When the turn can say *what* it is working on
                // — folding to make room, retrying a dropped round — that line replaces it
                // rather than sitting under it: two pulsing dots one above the other is one
                // signal too many, and the specific one is strictly better than the generic.
                return <WorkingIndicator />;
              default:
                return null;
            }
          }}
        </MessagePrimitive.GroupedParts>
        <MessageError />
      </div>

      <div
        data-slot="aui_assistant-message-footer"
        className={cn("ms-2 flex items-center", ACTION_BAR_HEIGHT)}
      >
        <BranchPicker />
        <AssistantActionBar />
        <div className="flex-1" />
        <MessageTime />
        <TurnUsageFooter />
      </div>
    </MessagePrimitive.Root>
  );
};

/**
 * Something you said into a turn that was already running.
 *
 * Marked as yours and set apart, because it is the one thing inside his message that he did not
 * write. Placed at the round it actually entered the prompt rather than at the top — a steer
 * that arrived after four tool calls did not influence those four, and showing it above them
 * would claim it did.
 */
/** What an errand came back with, drawn at the round it landed.
 *
 *  Teal, matching the errand's row in the Work panel and its icon in the tool feed, so the
 *  three places one delegation shows up read as the same thing. Muted rather than prominent:
 *  it is raw material he is about to use, not his answer to you. */
const ErrandBack: FC<{ text: string }> = ({ text }) => (
  <div className="my-2 rounded-lg border-s-2 border-teal-400/30 bg-teal-400/[0.06] px-3 py-2">
    <div className="mb-0.5 text-[10px] font-medium tracking-wide text-teal-400/80 uppercase">
      an errand came back
    </div>
    <p className="text-foreground/75 text-sm whitespace-pre-wrap">{text}</p>
  </div>
);

const SteeredIn: FC<{ text: string }> = ({ text }) => (
  <div className="border-kith/30 bg-kith-soft/40 my-2 rounded-lg border-s-2 px-3 py-2">
    <div className="text-kith/80 mb-0.5 text-[10px] font-medium tracking-wide uppercase">
      you, mid-turn
    </div>
    <p className="text-foreground/85 text-sm whitespace-pre-wrap">{text}</p>
  </div>
);

/**
 * A run of tool calls, collapsed to one line that says what he touched.
 *
 * The names are read off the message's own parts rather than passed in, because the group part
 * carries only the indices it spans.
 */
const ToolRun: FC<PropsWithChildren<{ group: ThreadGroupPart }>> = ({ group, children }) => {
  // Joined into one string rather than returned as an array: the state selector compares by
  // identity, and a fresh array on every render is a render on every render.
  const joined = useAuiState((s) =>
    group.indices
      .map((index) => {
        const part = s.message.parts[index];
        return part && part.type === "tool-call" ? part.toolName : "";
      })
      .join(RUN_SEPARATOR),
  );
  const run = summariseRun(joined.split(RUN_SEPARATOR).filter(Boolean));

  return (
    <ToolGroupRoot variant="ghost">
      <ToolGroupTrigger
        count={group.indices.length}
        active={group.status.type === "running"}
        summary={run.text}
        icons={run.icons}
      />
      <ToolGroupContent>{children}</ToolGroupContent>
    </ToolGroupRoot>
  );
};

/** A unit separator: safe in a way a comma or a space is not, since tool names are joined and
 *  split back apart. */
const RUN_SEPARATOR = "\u001f";

/** Below this many characters, a reasoning block is a one-line narration ("Let me check the
 *  API.") rather than an actual chain of thought — and giving it the same disclosure as a real
 *  one (its own row, a chevron, a click to read three words) is the thing making a turn with a
 *  reasoning blurb before every tool call look busier than it is. Long enough to catch a real
 *  sentence or two before switching over. */
const REASONING_INLINE_MAX = 160;

/**
 * A run of reasoning, collapsed to quiet inline text when it's short enough to just read, or
 * the full disclosure when it's substantial enough to be worth collapsing.
 *
 * The split isn't about row count — it's that "Reasoning ›" and three words behind it is a
 * worse deal than just showing the three words. A genuine multi-sentence chain of thought still
 * gets the full trigger; the difference is whether there's enough there to want to hide by
 * default.
 */
const ReasoningRun: FC<PropsWithChildren<{ group: ThreadGroupPart }>> = ({ group, children }) => {
  const length = useAuiState((s) =>
    group.indices.reduce((total, index) => {
      const part = s.message.parts[index];
      return total + (part && part.type === "reasoning" ? part.text.length : 0);
    }, 0),
  );
  const running = group.status.type === "running";
  const brief = !running && length > 0 && length <= REASONING_INLINE_MAX;

  if (brief) {
    return (
      <div className="text-muted-foreground/60 my-1 text-xs italic leading-relaxed [&_p]:my-0">
        {children}
      </div>
    );
  }

  return (
    <ReasoningRoot streaming={running} variant="ghost" className="mb-0">
      <ReasoningTrigger active={running} />
      <ReasoningContent aria-busy={running}>
        <ReasoningText>{children}</ReasoningText>
      </ReasoningContent>
    </ReasoningRoot>
  );
};

/**
 * What the turn cost, on the footer row with the rest of the turn's metadata.
 *
 * It was a line in the message body, which put a number where a sentence goes — and on a
 * reopened conversation there was one of them per model round, so a reply that took a dozen
 * rounds arrived as a column of six-figure token counts with the writing somewhere inside it.
 * The rounds are folded into one part on the way in now (see `toThreadMessages`); this is where
 * the one figure lands.
 */
/** This turn's usage part, or undefined. Serialised because the selector compares by identity. */
const useTurnUsage = (): TurnUsage | undefined => {
  const encoded = useAuiState((s) => {
    const part = s.message.parts.find((one) => one.type === "data" && one.name === USAGE_PART) as
      | { data?: unknown }
      | undefined;
    return part?.data ? JSON.stringify(part.data) : "";
  });
  if (!encoded) return undefined;
  try {
    return JSON.parse(encoded) as TurnUsage;
  } catch {
    return undefined;
  }
};

/** The working indicator, upgraded to a reason whenever the turn has one.
 *
 *  Decided on the usage itself rather than on whether `<TurnStatus>` would render something:
 *  an element is truthy even when the component returns null, so `status ?? dot` would have
 *  silently removed the working indicator in every ordinary turn. */
const WorkingIndicator: FC = () => {
  const usage = useTurnUsage();
  const explained = Boolean(usage?.retrying || (usage?.folded && (usage.rounds ?? []).length === 0));
  if (explained && usage) return <TurnStatus usage={usage} />;
  return (
    <span
      data-slot="aui_assistant-message-indicator"
      className="animate-pulse font-sans"
      aria-label="Assistant is working"
    >
      {"\u25CF"}
    </span>
  );
};

const TurnUsageFooter: FC = () => {
  const usage = useTurnUsage();
  if (!usage) return null;
  return <TurnTokens usage={usage} />;
};

/**
 * When the turn happened, on the footer row beside what it cost.
 *
 * Clock time only — the day is established by the conversation you are in, and a full date on
 * every message is noise in a thread you have been in all afternoon. The title carries the whole
 * thing for the one you actually want to pin down.
 *
 * Same weight as the token count next to it, deliberately: both are metadata about the turn, and
 * a timestamp that competes with the reply for attention is a worse timestamp.
 */
const MessageTime: FC = () => {
  const at = useAuiState((s) => s.message.createdAt);
  if (!at) return null;
  const iso = at instanceof Date ? at.toISOString() : String(at);
  const clock = time(iso);
  if (!clock) return null;
  return (
    <span
      data-slot="kith_message-time"
      className="text-muted-foreground/45 me-2 font-mono text-[10px] tabular-nums select-none"
      title={when(iso)}
    >
      {clock}
    </span>
  );
};

const AssistantActionBar: FC = () => {
  const { checkpoints, reload, turnOffset } = useCheckpoints();
  const renderedIndex = useAuiState((s) => s.message.index);
  const confirm = useConfirm();

  // `s.message.index` counts the *rendered* messages, and the thread mounts only the tail of a
  // long conversation — so it is an absolute turn index only when nothing has been windowed
  // away. Checkpoints are tagged absolutely, so the offset has to be added back before the two
  // can be compared at all.
  const messageIndex = renderedIndex + turnOffset;

  // The newest checkpoint at or before this turn — restoring reaches back to right
  // before whatever Kith changed on the way to this message, skipping past any turn
  // that made no file changes at all.
  const candidate = checkpoints
    .filter((c) => c.turnIndex <= messageIndex)
    .sort((a, b) => b.turnIndex - a.turnIndex)[0];

  const onRestore = async () => {
    const ok = await confirm({
      title: "Restore files to this point?",
      description:
        `Reverts every file Kith has touched in ${candidate.repoRoot.split("/").pop()} back to ` +
        `how it was right before "${candidate.trigger}". A file you created by hand since — ` +
        "outside anything Kith did — is left alone, not deleted. A safety checkpoint of the " +
        "current state is taken first, so this itself can be undone.",
      confirmLabel: "Restore",
      destructive: true,
    });
    if (!ok) return;
    try {
      await restoreCheckpoint(candidate.id);
      reload();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "restore failed");
    }
  };

  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-assistant-action-bar-root text-muted-foreground animate-in fade-in col-start-3 row-start-2 -ms-1 flex gap-1 duration-200"
    >
      <CopyMessage />
      <ActionBarPrimitive.Reload asChild>
        <TooltipIconButton tooltip="Refresh">
          <RefreshCwIcon />
        </TooltipIconButton>
      </ActionBarPrimitive.Reload>
      {/* Only when there is something in it. The menu's other item was "Export as Markdown",
          and with that gone a turn with no checkpoint behind it had a `…` that opened an empty
          box — a control that does nothing is worse than no control. */}
      {candidate ? (
        <ActionBarMorePrimitive.Root>
          <ActionBarMorePrimitive.Trigger asChild>
            <TooltipIconButton tooltip="More" className="data-[state=open]:bg-accent">
              <MoreHorizontalIcon />
            </TooltipIconButton>
          </ActionBarMorePrimitive.Trigger>
          <ActionBarMorePrimitive.Content
            side="bottom"
            align="start"
            sideOffset={6}
            className="aui-action-bar-more-content bg-popover/95 text-popover-foreground data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:animate-out data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 z-50 min-w-[8rem] overflow-hidden rounded-xl border p-1.5 shadow-lg backdrop-blur-sm"
          >
            <ActionBarMorePrimitive.Item
              onSelect={onRestore}
              className="aui-action-bar-more-item hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none"
            >
              <RotateCcw className="size-4" />
              Restore files to here
            </ActionBarMorePrimitive.Item>
          </ActionBarMorePrimitive.Content>
        </ActionBarMorePrimitive.Root>
      ) : null}
    </ActionBarPrimitive.Root>
  );
};

/**
 * Copy the reply, and say so only if it actually happened.
 *
 * Not `ActionBarPrimitive.Copy`, which writes through `navigator.clipboard` alone and flips to a
 * tick on having *tried*. That is the same lie `file-view.tsx` was fixed for: `writeText` throws
 * "Document is not focused" in exactly the situations an Electron webview gets into, and a
 * checkmark over an empty clipboard is worse than a button that visibly does nothing. `copyText`
 * is the shared path everything else here copies through — it falls back to `execCommand` on a
 * different permission route and returns whether either worked.
 *
 * The text comes from the message's own parts rather than the DOM, so reasoning blocks, tool
 * cards and the token footer stay out of it: what lands on the clipboard is what he said.
 */
const CopyMessage: FC = () => {
  const text = useAuiState((s) =>
    s.message.content
      .filter((part): part is { type: "text"; text: string } => part.type === "text")
      .map((part) => part.text)
      .join("\n\n")
      .trim(),
  );
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  const onCopy = async () => {
    if (!text || !(await copyText(text))) return;
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2_000);
  };

  return (
    <TooltipIconButton tooltip="Copy" onClick={() => void onCopy()} disabled={!text}>
      {copied ? (
        <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
      ) : (
        <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
      )}
    </TooltipIconButton>
  );
};

const UserMessage: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="fade-in slide-in-from-bottom-1 animate-in grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 px-2 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto] [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        <div className="aui-user-message-content peer bg-kith-soft text-foreground rounded-2xl rounded-br-md border border-[color-mix(in_oklab,var(--kith)_22%,transparent)] px-4 py-2 wrap-break-word empty:hidden">
          {/* Rendered, not printed. The composer can make lists and tables now, and a bubble
              showing their source is a message that looks nothing like the one you composed. */}
          <MessagePrimitive.Parts components={{ Text: UserMarkdownText }} />
        </div>
        <div className="aui-user-action-bar-wrapper absolute start-0 top-1/2 -translate-x-full -translate-y-1/2 pe-2 peer-empty:hidden rtl:translate-x-full">
          <UserActionBar />
        </div>
      </div>

      <BranchPicker
        data-slot="aui_user-branch-picker"
        className="col-span-full col-start-1 row-start-3 -me-1 justify-end"
      />
    </MessagePrimitive.Root>
  );
};

const UserActionBar: FC = () => {
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-user-action-bar-root flex flex-col items-end"
    >
      <ActionBarPrimitive.Edit asChild>
        <TooltipIconButton tooltip="Edit" className="aui-user-action-edit">
          <PencilIcon />
        </TooltipIconButton>
      </ActionBarPrimitive.Edit>
    </ActionBarPrimitive.Root>
  );
};

const EditComposer: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_edit-composer-wrapper"
      className="flex flex-col px-2 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <ComposerPrimitive.Root className="aui-edit-composer-root border-border/60 dark:border-muted-foreground/15 ms-auto flex w-full max-w-[85%] flex-col rounded-(--composer-radius) border bg-(--composer-bg) shadow-[0_4px_16px_-8px_rgba(0,0,0,0.08),0_1px_2px_rgba(0,0,0,0.04)] dark:shadow-none">
        <ComposerPrimitive.Input
          className="aui-edit-composer-input text-foreground min-h-14 w-full resize-none bg-transparent px-4 pt-3 pb-1 text-base outline-none"
          autoFocus
        />
        <div className="aui-edit-composer-footer mx-2.5 mb-2.5 flex items-center gap-1.5 self-end">
          <ComposerPrimitive.Cancel asChild>
            <Button variant="ghost" size="sm" className="h-8 rounded-full px-3.5">
              Cancel
            </Button>
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send asChild>
            <Button size="sm" className="h-8 rounded-full px-3.5">
              Update
            </Button>
          </ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
    </MessagePrimitive.Root>
  );
};

const BranchPicker: FC<BranchPickerPrimitive.Root.Props> = ({ className, ...rest }) => {
  return (
    <BranchPickerPrimitive.Root
      hideWhenSingleBranch
      className={cn(
        "aui-branch-picker-root text-muted-foreground -ms-2 me-2 inline-flex items-center text-xs",
        className,
      )}
      {...rest}
    >
      <BranchPickerPrimitive.Previous asChild>
        <TooltipIconButton tooltip="Previous">
          <ChevronLeftIcon />
        </TooltipIconButton>
      </BranchPickerPrimitive.Previous>
      <span className="aui-branch-picker-state font-medium">
        <BranchPickerPrimitive.Number /> / <BranchPickerPrimitive.Count />
      </span>
      <BranchPickerPrimitive.Next asChild>
        <TooltipIconButton tooltip="Next">
          <ChevronRightIcon />
        </TooltipIconButton>
      </BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
  );
};
