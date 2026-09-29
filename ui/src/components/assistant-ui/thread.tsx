"use client";

import { DesignsInTurn } from "@/components/design/designs-in-turn";
import { AttachmentUI, UserMessageAttachments } from "@/components/assistant-ui/attachment";
import { PendingSteer } from "@/components/assistant-ui/pending-steer";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/follow-up-suggestions";
import { AskPrompt } from "@/components/assistant-ui/ask-prompt";
import { RichComposerInput } from "@/components/assistant-ui/composer-input/rich-input";
import { UserMarkdownText } from "@/components/assistant-ui/user-markdown";
import { useSlashCommands } from "@/components/assistant-ui/slash-commands";
import { PermissionPrompt } from "@/components/assistant-ui/permission-prompt";
import { TurnFooterStats, latestUsage } from "@/components/assistant-ui/turn-usage";
import {
  FlatTurn,
  TurnSections,
  usePartComponents,
  useTurnUsage,
} from "@/components/assistant-ui/run-strip";
import {
  AccessChip,
  ContextRing,
  ModelChip,
  ThinkingChip,
  WorkflowModeChip,
} from "@/components/assistant-ui/composer-bar";
import { ToolFallback } from "@/components/assistant-ui/tool-fallback";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { PresenceOrb } from "@/components/shell/presence";
import { useCheckpoints } from "@/components/assistant-ui/checkpoints-context";
import { restoreCheckpoint } from "@/lib/backend/checkpoints";
import { steerTurn, stopTurn } from "@/lib/commands";
import { useConversationId } from "@/lib/conversation";
import { dropHeld, heldMessage, isHolding, subscribeHolding } from "@/lib/queued-send";
import { copyText } from "@/lib/files";
import { when } from "@/lib/dates";
import { STEER_PART } from "@/lib/backend/adapter";
import type { ContextLedger } from "@/lib/backend/types";
import { KIND_CODE, kindOf, kindsFromCode, planTurn } from "@/lib/turn-plan";
import { expandCommand } from "@/lib/slash";
import { splitWorkflowPrefix } from "@/lib/workflow-prefix";
import { cn } from "@/lib/utils";
import {
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  BranchPickerPrimitive,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePrimitive,
  SelectionToolbarPrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type ToolCallMessagePartComponent,
  useAuiState,
  useComposerRuntime,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Clock3,
  CopyIcon,
  CornerDownRight,
  MicIcon,
  PencilIcon,
  Plus,
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
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type FC,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useSyncExternalStore,
} from "react";

/* Re-exported: the Work panel reads the same figure, from here, and has always imported it from
 * this module. It lives beside the usage type now. */
export { latestUsage };

/**
 * Optional component overrides for the thread. `AssistantMessage` and `Welcome` replace whole
 * sections; `ToolFallback` replaces how a tool call renders when no tool UI is registered for it
 * by name (toolkit `render`, `useAssistantDataUI` take precedence).
 *
 * The `ToolGroup` / `ReasoningGroup` slots are gone with the grouping they overrode: a turn is
 * laid out as a run strip of beats now (see `run-strip.tsx`), and nothing set either slot.
 */
export type ThreadComponents = {
  AssistantMessage?: ComponentType | undefined;
  Welcome?: ComponentType | undefined;
  ToolFallback?: ToolCallMessagePartComponent | undefined;
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
        /* One reading column, 720px, centred — the conversation's measure, for his words and
         * yours alike. The composer is a little wider (752px) so its edges frame the column
         * above it rather than lining up with it.
         *
         * This reverses a decision, knowingly. The measure had moved off the messages so that a
         * wide diagram or table was not drawn inside a reading column; the design puts the column
         * back, because a thread whose every line runs 1,400px wide is not read, it is scanned.
         * Anything wide still scrolls inside its own box, and diagrams open full screen. */
        ["--thread-max-width" as string]: "45rem",
        ["--composer-max-width" as string]: "47rem",
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
          // The thread never scrolls sideways. Anything that can be wide — code, tables,
          // diagrams, tool output — scrolls inside its own box; letting the viewport scroll too
          // meant one unbreakable line shifted every message left and cut off their starts.
          "relative flex flex-1 flex-col overflow-x-hidden overflow-y-scroll scroll-smooth",
          // Only once there is something to scroll: on a new chat the fade would eat the top
          // of the greeting for no reason.
          !isEmpty && "kith-fade-top",
        )}
      >
        <div className={cn("flex w-full flex-1 flex-col px-4 pt-[18px]", isEmpty && "justify-center")}>
          <AuiIf condition={isNewChatView}>
            <Welcome />
          </AuiIf>

          <div
            data-slot="aui_message-group"
            className="mx-auto mb-10 flex w-full max-w-(--thread-max-width) flex-col gap-y-5 empty:hidden"
          >
            <ThreadPrimitive.Messages>{() => <ThreadMessage />}</ThreadPrimitive.Messages>
          </div>

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer flex flex-col overflow-visible",
              /* Docked: a solid band with a hairline on top, and the thread ends where it begins.
               *
               * This was a fade, and before that a fill; both are written down in the history of
               * this line. The fill was dropped because the ambient wash is painted *under* the
               * content layer, so an opaque band was the one rectangle on screen with no warmth
               * in it. The fade was the compromise — and it meant his reply ran on underneath the
               * box you type in, the last lines half-visible behind it, which is exactly what the
               * design rules out: the composer is docked, it never floats over text. The band is
               * the page's own ground colour, so in the flat room the design draws it is
               * indistinguishable from the thread; only the hairline says where one ends.
               *
               * `-mx-4 px-4` so the band reaches the pane's edges while the composer inside it
               * keeps its measure. */
              isEmpty
                ? "pt-6 pb-4"
                : "bg-background border-border/70 sticky bottom-0 -mx-4 mt-auto border-t px-4 pt-3 pb-[18px]",
            )}
          >
            <div className="mx-auto flex w-full max-w-(--composer-max-width) flex-col gap-2">
              <ThreadFollowupSuggestions />
              <AskPrompt conversationId={conversationId} />
              {/* What you have said to the running turn that it has not read yet. Sits between
                  the thread and the composer because that is where it is in time: after
                  everything that has happened, before the next thing you type. */}
              <PendingSteer conversationId={conversationId} />
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
  <MessagePrimitive.Root className="aui-system-message mx-auto w-full max-w-[34rem] py-1">
    {/* `wrap-break-word` because a finished process's last output is quoted here verbatim, and a
        pytest node id is one 120-character word: with nothing to break on, it made this box
        wider than the thread and the whole conversation scrolled sideways. */}
    <div className="border-border text-muted-foreground rounded-xl border border-dashed px-3.5 py-2.5 text-[12.5px] leading-relaxed wrap-break-word">
      <span className="text-kith/80 mb-1 flex items-center gap-1.5 text-[10px] font-semibold tracking-[0.12em] uppercase">
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
          elsewhere.

          The composer is docked in a band now, so "above the composer" means above the band's
          hairline: -top-14 clears the band's own top padding. */}
      <TooltipIconButton
        tooltip="Scroll to bottom"
        variant="outline"
        className={cn(
          "aui-thread-scroll-to-bottom dark:border-border dark:bg-card dark:hover:bg-accent bg-card",
          "absolute -top-14 left-1/2 z-20 -translate-x-1/2 rounded-full p-2 shadow-md",
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

/** `conversationId` is not decoration here: `/fold` and `/stop` are addressed to a conversation,
 *  and without one they short-circuit into a note. `useSlashCommands()` was called bare, so the
 *  id was always `""` — every `/fold` answered "this conversation has not started" over a window
 *  that was 58% full, and every `/stop` returned an empty note, which looks exactly like a
 *  keystroke that did nothing. */
const Composer: FC<{ conversationId: string }> = ({ conversationId }) => {
  const { commands, note, reading } = useSlashCommands(conversationId);
  const composer = useComposerRuntime();
  const running = useAuiState((s) => s.thread.isRunning);
  const empty = useAuiState((s) => s.composer.isEmpty && s.composer.attachments.length === 0);
  // A message that opens with `/workflow` may fan out into agents, and the box says so.
  const workflow = useAuiState((s) => splitWorkflowPrefix(s.composer.text).workflow);
  /* Escape stops the turn — from the box you type in, and only from there.
   *
   * Taken on the way *down*, because the editor claims Escape for itself: ProseMirror marks it
   * handled before it would ever bubble to this shell, so a bubble-phase handler here never saw
   * the key at all. The one thing in the box with a better claim is an open slash menu, where
   * Escape means "dismiss" — so while it is open the key is left to it. The chips' menus claim
   * theirs on the window before anything reaches here. Scoped to the editor itself, so an Escape
   * that lands on a button — or anywhere else in the window — never ends a turn by accident. */
  const onKeyDownCapture = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Escape" || event.defaultPrevented || event.nativeEvent.isComposing || !running) return;
    if (!(event.target as HTMLElement).closest(".ProseMirror")) return;
    if (event.currentTarget.querySelector("[data-slash-panel]")) return;
    event.preventDefault();
    event.stopPropagation();
    composer.cancel();
    if (conversationId) void stopTurn(conversationId);
  };

  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col gap-2">
      {/* Anchored to the composer, not floated over the conversation. */}
      <ThreadScrollToBottom />
      <QueuedNotice />
      {/* No `AttachmentDropzone` around this. It made this box the only place in the window
          that would take a file, which is the smallest and least obvious target on screen —
          and keeping it alongside the window-wide handler in `DropZone` would mean a file let
          go on the composer landed twice. One listener, one path, the whole window. */}
      <div
        data-slot="aui_composer-shell"
        onKeyDownCapture={onKeyDownCapture}
        className={cn(
          // The card: 16px radius, the lifted card colour, a real border. Green-edged while he
          // works — typing now steers a live turn, and the edge says which kind of box this is.
          "bg-card relative flex w-full flex-col rounded-2xl border transition-[border-color,box-shadow]",
          running
            ? "border-roam/30 focus-within:border-roam/50"
            : workflow
              ? "border-teal-400/40 focus-within:border-teal-400/60"
              : "border-input focus-within:border-foreground/25",
        )}
      >
        <div className="flex flex-col gap-2 px-2.5 pt-2.5 empty:hidden">
          <ComposerQuoteStrip />
          <ComposerAttachmentStrip />
        </div>
          {/* Not `ComposerPrimitive.Input` any more. The value in the store is still a markdown
              string — nothing downstream knows the difference — but a textarea cannot show a table
              as a table, and a message with structure in it was being written blind. The paste
              handling that used to live here (a screenshot on the clipboard, which Cmd-V otherwise
              drops silently) moved into `paste.ts` along with the rest of the rules.

              The wrapper sets the design's measure on the editor and its placeholder together —
              16px in, 14px down, 14px type — so the placeholder sits exactly where the caret will. */}
          <div className="@container relative [&>div>span]:start-4 [&>div>span]:top-3.5 [&>div>span]:text-sm">
            <RichComposerInput
              commands={commands}
              placeholder={running ? "Steer him while he works…" : "Say something to Kith, or type / for commands…"}
              className="min-h-[3.25rem] px-4 pt-3.5 pb-0.5 text-sm leading-normal"
              autoFocus
            />
            {empty ? <ComposerHint running={running} /> : null}
          </div>
        <ComposerAction conversationId={conversationId} afterFold={reading} />
      </div>
      {workflow ? (
        <p className="text-muted-foreground flex items-center gap-2 px-1 text-xs">
          <WorkflowMark className="size-3 text-teal-700 dark:text-teal-400" />
          This message may fan out: he writes a script, agents run in the background
        </p>
      ) : null}
      {/* A command has no reply to appear in, so it says what it did here. Without this,
          `/fold` was indistinguishable from a keystroke that did nothing. */}
      {note ? <p className="text-muted-foreground/80 px-1 text-[11px]">{note}</p> : null}
    </ComposerPrimitive.Root>
  );
};

/** The `/workflow` mark: a small radar, the same glyph the chip on a sent message wears. */
export const WorkflowMark: FC<{ className?: string }> = ({ className }) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.4}
    strokeLinecap="round"
    aria-hidden
    className={cn("shrink-0", className)}
  >
    <circle cx="12" cy="12" r="2" />
    <path d="M16.24 7.76a6 6 0 1 0 .01 8.48M19.07 4.93a10 10 0 1 0 .01 14.14" />
  </svg>
);

/**
 * A ⌘⏎ message, held until the turn ends.
 *
 * Nothing said this before. The wait happens inside the adapter, *before* the fetch, so
 * assistant-ui had already put the message in the thread and was showing the turn as running
 * while nothing had been sent — for up to twenty minutes. A queued message and a message that
 * got no reply looked exactly alike, and only one of them is fine.
 *
 * Its own card above the composer now rather than a strip inside it: it is a message you have
 * already sent, waiting, and the box below is for the next one.
 */
const QueuedNotice: FC = () => {
  const holding = useSyncExternalStore(subscribeHolding, isHolding, () => false);
  const text = useSyncExternalStore(subscribeHolding, heldMessage, () => "");
  if (!holding) return null;
  return (
    <div className="border-border bg-card flex items-start gap-2.5 rounded-xl border px-3 py-2.5">
      <Clock3 className="text-muted-foreground mt-0.5 size-3.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="text-muted-foreground text-[11px]">
          Goes when this turn ends · sent with ⌘⏎
        </div>
        {/* The words themselves, not just the fact of them. A notice that says something is
            waiting without showing what is a notice you have to take on trust. */}
        {text ? <p className="text-foreground mt-0.5 line-clamp-2 text-[13px]">{text}</p> : null}
      </div>
      <button
        type="button"
        onClick={() => dropHeld()}
        className="text-muted-foreground hover:text-destructive shrink-0 rounded-md px-1.5 py-0.5 text-xs transition-colors"
      >
        Cancel
      </button>
    </div>
  );
};

/**
 * What the keys do, said at the moment it is worth saying — in the empty box's top corner.
 *
 * Idle says ⏎ / ⇧⏎ and nothing else, because ⌘⏎ idle is not a third option: the flag is set,
 * `holdUntilIdle` finds nothing running and returns at once, and the message sends exactly as ⏎
 * would. Listing "after this" with no *this* would be teaching a key that does nothing.
 *
 * While he works it names what each key does to the turn in flight. Once you have typed, the
 * Steer now / After this turn control takes over the job and wears the same keys — the whole
 * hint used to vanish at exactly that moment, which made ⌘⏎ undiscoverable by construction.
 */
const ComposerHint: FC<{ running: boolean }> = ({ running }) => (
  // Long enough, beside the placeholder, to run into it in a narrow composer — where it steps
  // aside. The keys still work; Stop is on its button.
  <div className="text-muted-foreground/60 pointer-events-none absolute end-4 top-4 flex items-center gap-1.5 text-[11px] select-none @max-[36rem]:hidden">
    {running ? (
      <>
        <span>⏎ steers him</span>
        <span aria-hidden>·</span>
        <span>⌘⏎ waits for the turn</span>
        <span aria-hidden>·</span>
        <span>esc stops</span>
      </>
    ) : (
      <>
        <span>⏎ send</span>
        <span aria-hidden>·</span>
        <span>⇧⏎ new line</span>
      </>
    )}
  </div>
);

/** The other half of `SelectionQuoteToolbar`: what a quote looks like once it's actually
 *  attached, so it's obvious what's about to be sent along with the message rather than a
 *  silent extra a person only discovers after sending. `ComposerPrimitive.Quote` already
 *  renders nothing when there's no quote set, so no extra `AuiIf` here. */
const ComposerQuoteStrip: FC = () => (
  <ComposerPrimitive.Quote className="border-s-kith/50 bg-kith-soft/30 flex items-start gap-2 rounded-lg border-s-2 px-3 py-2 text-xs">
    <Reply className="text-kith/80 mt-0.5 size-3.5 shrink-0" />
    <span className="min-w-0 flex-1 leading-relaxed">
      <span className="text-kith me-1">Replying to</span>
      <ComposerPrimitive.QuoteText className="text-muted-foreground line-clamp-2 inline" />
    </span>
    <ComposerPrimitive.QuoteDismiss
      aria-label="Remove quote"
      className="text-muted-foreground/70 hover:text-foreground shrink-0"
    >
      <XIcon className="size-3.5" />
    </ComposerPrimitive.QuoteDismiss>
  </ComposerPrimitive.Quote>
);

/**
 * What is attached to the message you are writing.
 *
 * Rendered above the input rather than below it, so adding an image does not push the caret
 * you are typing in. Nothing shows when nothing is attached.
 *
 * Whether to inline a picture or hand him a path is decided on the server, next to the model
 * config — not here by hiding a button.
 */
const ComposerAttachmentStrip: FC = () => (
  <AuiIf condition={(s) => s.composer.attachments.length > 0}>
    <div className="flex flex-wrap gap-1.5 px-0.5">
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
        tooltip="Attach files"
        side="top"
        type="button"
        variant="ghost"
        size="icon"
        aria-label="Attach files"
        className="text-muted-foreground hover:text-foreground size-[30px] rounded-lg [&_svg]:size-[15px]"
        onClick={() => input.current?.click()}
      >
        <Plus />
      </TooltipIconButton>
    </>
  );
};

/**
 * Send, while he is working — which means steer, or wait for the turn, and must not go near the
 * runtime either way.
 *
 * `ComposerPrimitive.Send` cannot be used here. Everything it sends goes through
 * `performRoundtrip`, whose first line is `abortController.abort()`: the run in flight is
 * cancelled, the adapter's abort handler posts `/stop`, and the turn dies. A button labelled
 * "send" that quietly stops the work it was meant to redirect is worse than no button, which is
 * what was here before.
 *
 * So this is the path ⏎ takes: the text goes to the running turn. Waiting for the turn to end
 * instead is ⌘⏎ and only ⌘⏎ — there was a "Steer now | After this turn" toggle beside the attach
 * button for the mouse, and it was a second control for what the keys already say, crowding the
 * model's name off the row whenever you typed during a turn.
 */
const SteerButton: FC = () => {
  const composer = useComposerRuntime();
  const text = useAuiState((s) => s.composer.text);
  const canSend = useAuiState((s) => !s.composer.isEmpty);
  const here = useConversationId();

  return (
    <button
      type="button"
      disabled={!canSend}
      title="Send to the turn he is running — it keeps what it has found. ⌘⏎ sends it after this turn instead."
      className="aui-composer-send bg-kith text-primary-foreground hover:bg-kith/90 flex h-8 shrink-0 items-center gap-1.5 rounded-[10px] px-3 text-xs font-semibold transition-colors disabled:opacity-50"
      aria-label="Steer this turn"
      onClick={() => {
        // The pane this button is in, not the process's last-writer-wins guess — see `rich-input`.
        if (!here || !text.trim()) return;
        const said = text;
        composer.setText("");
        // Spelled out as a send would be — a steer never passes the adapter. See `lib/slash`.
        void steerTurn(here, expandCommand(said));
      }}
    >
      <CornerDownRight className="size-3.5" aria-hidden />
      Steer
    </button>
  );
};

const ComposerAction: FC<{
  conversationId: string;
  /** The reading a `/fold` just left, which the ring shows until the next turn measures. */
  afterFold?: ContextLedger;
}> = ({ conversationId, afterFold }) => {
  const running = useAuiState((s) => s.thread.isRunning);
  const hasText = useAuiState((s) => !s.composer.isEmpty);

  return (
    <div className="aui-composer-action-wrapper @container relative flex min-w-0 items-center gap-1 px-2 pt-1.5 pb-2">
      <AttachButton />
      {/* Nothing between the attach button and the settings: commands are `/`, which the
          placeholder says, and steer-or-wait is ⏎ or ⌘⏎, which the hint says. */}
      <ThinkingChip />
      <AccessChip />
      <ModelChip />
      {/* Workflow *mode*, a setting like the three beside it — not a status. That a workflow is
          running is the title bar's pill, and a finished run's map is on its card in the thread. */}
      <WorkflowModeChip />
      <div className="min-w-2 flex-1" />
      <AuiIf condition={(s) => s.thread.capabilities.dictation}>
        <AuiIf condition={(s) => s.composer.dictation == null}>
          <ComposerPrimitive.Dictate asChild>
            <TooltipIconButton
              tooltip="Voice input"
              side="top"
              type="button"
              variant="ghost"
              size="icon"
              className="aui-composer-dictate size-7 rounded-lg"
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
              side="top"
              type="button"
              variant="ghost"
              size="icon"
              className="aui-composer-stop-dictation text-destructive size-7 rounded-lg"
              aria-label="Stop voice input"
            >
              <SquareIcon className="aui-composer-stop-dictation-icon size-3.5 animate-pulse fill-current" />
            </TooltipIconButton>
          </ComposerPrimitive.StopDictation>
        </AuiIf>
      </AuiIf>
      <ContextRing afterFold={afterFold} />
      {/* One button, and what it does depends on whether you have typed anything.
       *
       * It was two while a turn ran — a square Stop beside an arrow Send — which is a choice
       * presented at the moment you are least interested in making one. Every other assistant
       * settles this the same way and it is the right way: an empty box while something is
       * running can only mean stop, and a box with words in it can only mean send them.
       *
       * The two intents are still both reachable, because they are still different: Stop ends
       * the run and loses what it found; sending steers, and keeps every tool result so far.
       * You get whichever one your input is already asking for — and Escape stops from the box. */}
      {running && !hasText ? (
        <>
          {/* Stop says so, rather than being inferred from a closed connection.
              `Cancel` stops this window reading; the `/stop` it posts is what ends the turn on the
              server. Two actions because they are two different things — which is the distinction
              the adapter used to collapse, so that leaving a conversation killed the work in it.
              Order does not matter: neither depends on the other. */}
          <ComposerPrimitive.Cancel asChild>
            <button
              type="button"
              title="Stop — ends the turn and loses what it found"
              aria-label="Stop generating"
              className="aui-composer-cancel border-foreground/15 bg-accent text-foreground hover:bg-accent/70 flex h-8 shrink-0 items-center gap-1.5 rounded-[10px] border px-3 text-xs font-semibold transition-colors"
              onClick={() => {
                if (conversationId) void stopTurn(conversationId);
              }}
            >
              <SquareIcon className="aui-composer-cancel-icon size-2.5 fill-current" />
              Stop
            </button>
          </ComposerPrimitive.Cancel>
        </>
      ) : null}
      {running && hasText ? <SteerButton /> : null}
      {!running ? (
        <ComposerPrimitive.Send asChild>
          <button
            type="button"
            title="Send message"
            aria-label="Send message"
            className="aui-composer-send bg-kith text-primary-foreground hover:bg-kith/90 disabled:bg-accent disabled:text-muted-foreground/75 flex size-8 shrink-0 items-center justify-center rounded-[10px] transition-colors"
          >
            <ArrowUpIcon className="aui-composer-send-icon size-[15px]" strokeWidth={2.4} />
          </button>
        </ComposerPrimitive.Send>
      ) : null}
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root border-destructive/40 bg-destructive/10 text-destructive rounded-xl border px-3.5 py-2.5 text-[13px]">
        <ErrorPrimitive.Message className="aui-message-error-message line-clamp-2" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

/**
 * One of his turns: its runs of work and what he said between them, the answer, what the turn
 * made, and the footer.
 *
 * The layout is decided by `planTurn` from the *shape* of the message — which kinds of part, in
 * which order — so it is recomputed when a part arrives and not on every streamed token. The
 * shape travels through the selector as a string, one character per part, which compares by
 * value where an array would compare by identity and re-render the turn on every chunk. The one
 * exception is a text part that grows into an output (`isOutput`) — its code flips once, from
 * `t` to `T`, and the turn is laid out again then.
 */
const AssistantMessage: FC = () => {
  const { ToolFallback: ToolFallbackComponent = ToolFallback } = useContext(ThreadComponentsContext);
  const code = useAuiState((s) =>
    s.message.parts
      .map((part) => KIND_CODE[kindOf(part as { type: string; name?: string; text?: string }, STEER_PART)])
      .join(""),
  );
  const kinds = useMemo(() => kindsFromCode(code), [code]);
  const finished = useAuiState((s) => s.message.status?.type !== "running");
  const plan = useMemo(() => planTurn(kinds, finished), [kinds, finished]);
  const components = usePartComponents(ToolFallbackComponent);

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="fade-in slide-in-from-bottom-1 animate-in relative duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      {/* Not `aui_assistant-message-content` any more: find-in-thread reads that slot as *prose*,
          and the strip's header, counts and calls are not. The slot moved onto what he wrote —
          each beat's sentence and the answer — so ⌘F finds his words and never a command line. */}
      <article data-slot="kith_turn" className="text-foreground flex flex-col gap-3.5">
        {plan.strip ? (
          <TurnSections plan={plan} components={components} />
        ) : (
          <FlatTurn plan={plan} kinds={kinds} components={components} />
        )}
        {/* What the turn *made*, under the turn, open. A design is not a step worth auditing —
            it is the output, and it was invisible inside the tool chain's closed disclosure. */}
        <DesignsInTurn />
        <MessageError />
        <TurnFooter />
      </article>
    </MessagePrimitive.Root>
  );
};

/**
 * The footer under a finished answer: copy, restore files to here, retry — and what the turn
 * cost, in one quiet line. The clock time it used to show is on that line's tooltip: the design
 * gives the footer to the turn's price and its model, and the day is established by the
 * conversation you are in.
 */
const TurnFooter: FC = () => {
  const usage = useTurnUsage();
  /* Quiet until the turn is over. The strip's header is the live line while he works; a price
   * ticking up under a half-finished reply is a second clock for the same thing. */
  const running = useAuiState((s) => s.message.status?.type === "running");
  const at = useAuiState((s) => s.message.createdAt);
  const iso = at ? (at instanceof Date ? at.toISOString() : String(at)) : "";
  const stamp = iso ? when(iso) : "";
  return (
    <div
      data-slot="aui_assistant-message-footer"
      className="text-muted-foreground/75 -mt-1 flex min-h-[26px] min-w-0 items-center gap-0.5 text-[11px]"
    >
      <BranchPicker />
      <AssistantActionBar />
      {usage && !running ? <TurnFooterStats usage={usage} title={stamp || undefined} /> : null}
    </div>
  );
};

const footerButton =
  "text-muted-foreground/75 hover:text-foreground size-[26px] rounded-[7px] p-0 [&_svg]:size-[13px]";

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
    if (!candidate) return;
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
      className="aui-assistant-action-bar-root animate-in fade-in -ms-1.5 flex items-center gap-0.5 duration-200"
    >
      <CopyMessage />
      {/* Only when there is something to go back to. It sat in a "…" menu of its own, one click
          deeper than it needed to be; the confirmation it opens is the same, and is the part that
          makes a destructive action safe to put in reach. */}
      {candidate ? (
        <TooltipIconButton tooltip="Restore files to here" className={footerButton} onClick={() => void onRestore()}>
          <RotateCcw />
        </TooltipIconButton>
      ) : null}
      <ActionBarPrimitive.Reload asChild>
        <TooltipIconButton tooltip="Retry this turn" className={footerButton}>
          <RefreshCwIcon />
        </TooltipIconButton>
      </ActionBarPrimitive.Reload>
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
    <TooltipIconButton tooltip="Copy" onClick={() => void onCopy()} disabled={!text} className={footerButton}>
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
      className="fade-in slide-in-from-bottom-1 animate-in grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto] [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        {/* The design's bubble: the raised surface, 16px corners with the one nearest you cut to
            4px, 14px type. It was a pale amber pill, which spent Kith's colour on your words. */}
        <div className="aui-user-message-content peer bg-accent text-foreground max-w-[520px] rounded-[16px_16px_4px_16px] px-3.5 py-2.5 text-[14px] leading-[1.5] wrap-break-word empty:hidden">
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
        <TooltipIconButton tooltip="Edit" className="aui-user-action-edit text-muted-foreground/75">
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
      className="flex flex-col [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <ComposerPrimitive.Root className="aui-edit-composer-root border-input bg-card ms-auto flex w-full max-w-[85%] flex-col rounded-2xl border">
        <ComposerPrimitive.Input
          className="aui-edit-composer-input text-foreground min-h-14 w-full resize-none bg-transparent px-4 pt-3 pb-1 text-[14px] outline-none"
          autoFocus
        />
        <div className="aui-edit-composer-footer mx-2.5 mb-2.5 flex items-center gap-1.5 self-end">
          <ComposerPrimitive.Cancel asChild>
            <Button variant="ghost" size="sm" className="h-8 rounded-[10px] px-3.5">
              Cancel
            </Button>
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send asChild>
            <Button size="sm" className="bg-kith text-primary-foreground hover:bg-kith/90 h-8 rounded-[10px] px-3.5">
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
        "aui-branch-picker-root text-muted-foreground/75 -ms-2 me-1 inline-flex items-center text-[11px]",
        className,
      )}
      {...rest}
    >
      <BranchPickerPrimitive.Previous asChild>
        <TooltipIconButton tooltip="Previous">
          <ChevronLeftIcon />
        </TooltipIconButton>
      </BranchPickerPrimitive.Previous>
      <span className="aui-branch-picker-state tabular-nums">
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
