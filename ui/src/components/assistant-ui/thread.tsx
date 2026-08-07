"use client";

import { AttachmentUI, UserMessageAttachments } from "@/components/assistant-ui/attachment";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/follow-up-suggestions";
import { PermissionPrompt } from "@/components/assistant-ui/permission-prompt";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { TurnTokens, type TurnUsage } from "@/components/assistant-ui/turn-usage";
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
import { USAGE_PART } from "@/lib/backend/adapter";
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
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CopyIcon,
  DownloadIcon,
  MicIcon,
  Paperclip,
  MoreHorizontalIcon,
  PencilIcon,
  Reply,
  RefreshCwIcon,
  RotateCcw,
  SquareIcon,
  XIcon,
} from "lucide-react";
import {
  createContext,
  useContext,
  useRef,
  type ComponentType,
  type FC,
  type PropsWithChildren,
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
};

const EMPTY_COMPONENTS: ThreadComponents = {};

const ThreadComponentsContext = createContext<ThreadComponents>(EMPTY_COMPONENTS);

// Startup exposes a loading placeholder thread; treat it as a new chat so
// the composer mounts centered. Loads after startup keep the docked layout.
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 && (!s.thread.isLoading || s.threads.isLoading);

export const Thread: FC<ThreadProps> = ({ components = EMPTY_COMPONENTS }) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot isEmpty={isEmpty} />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<{ isEmpty: boolean }> = ({ isEmpty }) => {
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
        ["--composer-radius" as string]: "1.5rem",
        ["--composer-padding" as string]: "8px",
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
        <div
          className={cn(
            "mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-4 pt-4",
            isEmpty && "justify-center",
          )}
        >
          <AuiIf condition={isNewChatView}>
            <Welcome />
          </AuiIf>

          <div data-slot="aui_message-group" className="mb-14 flex flex-col gap-y-6 empty:hidden">
            <ThreadPrimitive.Messages>{() => <ThreadMessage />}</ThreadPrimitive.Messages>
          </div>

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer flex flex-col gap-4 overflow-visible pb-4 md:pb-6",
              !isEmpty && "sticky bottom-0 mt-auto rounded-t-(--composer-radius) bg-background",
            )}
          >
            <ThreadScrollToBottom />
            <ThreadFollowupSuggestions />
            <PermissionPrompt />
            <Composer />
            <AuiIf condition={(s) => isNewChatView(s) && s.composer.isEmpty}>
              <ThreadSuggestions />
            </AuiIf>
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
  if (role === "user") return <UserMessage />;
  return <AssistantMessageComponent />;
};

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
      {/* End-aligned rather than centred, and smaller. Centred over a 44rem column it landed
          squarely on the last line of the reply — so the one control for "take me back down"
          was drawn through the sentence you were reading.

          It's always mounted — `disabled` is how the primitive says "you're already at the
          bottom" — so appearing/disappearing is a transition on that attribute, not a mount.
          `disabled:invisible` cut straight to gone with nothing in between; scale+fade over the
          same easing the rest of the app's discloures use reads as the button arriving rather
          than blinking on. A couple more pixels of clearance from the composer, and a lift on
          hover so it reads as pressable before you press it. */}
      <TooltipIconButton
        tooltip="Scroll to bottom"
        variant="outline"
        className={cn(
          "aui-thread-scroll-to-bottom dark:border-border dark:bg-background/80 dark:hover:bg-accent",
          "absolute -top-14 end-2 z-10 self-end rounded-full p-2.5 shadow-md backdrop-blur-sm",
          "scale-100 opacity-100 transition-[opacity,transform] duration-200 ease-[cubic-bezier(0.32,0.72,0,1)]",
          "hover:scale-110 active:scale-90",
          "disabled:pointer-events-none disabled:scale-50 disabled:opacity-0",
        )}
      >
        <ArrowDownIcon className="size-4" />
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

const Composer: FC = () => {
  const composer = useComposerRuntime();
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      {/* No `AttachmentDropzone` around this. It made this box the only place in the window
          that would take a file, which is the smallest and least obvious target on screen —
          and keeping it alongside the window-wide handler in `DropZone` would mean a file let
          go on the composer landed twice. One listener, one path, the whole window. */}
      <div
        data-slot="aui_composer-shell"
        // A full-strength border and a real focus ring, rather than border/60 and a shadow.
        // On warm paper a 60% border over a card that barely differs from the background was
        // a suggestion of an input; you had to know it was there.
        className="border-border dark:border-muted-foreground/25 dark:focus-within:border-muted-foreground/40 focus-within:border-ring/70 focus-within:ring-ring/25 flex w-full flex-col gap-2 rounded-(--composer-radius) border bg-(--composer-bg) p-(--composer-padding) shadow-[0_4px_16px_-8px_rgba(0,0,0,0.10),0_1px_2px_rgba(0,0,0,0.05)] transition-[border-color,box-shadow] focus-within:ring-[3px] focus-within:shadow-[0_8px_28px_-10px_rgba(0,0,0,0.14),0_1px_2px_rgba(0,0,0,0.06)] dark:shadow-none"
      >
        <ComposerQuoteStrip />
        <ComposerAttachmentStrip />
        <ComposerPrimitive.Input
          placeholder="say something to Kith…"
          className="aui-composer-input caret-primary placeholder:text-muted-foreground/80 max-h-32 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base outline-none"
          rows={1}
          autoFocus
          enterKeyHint="send"
          aria-label="Message input"
          // A screenshot on the clipboard is the other thing people try after dragging, and
          // Cmd-V into a textarea otherwise does nothing at all for an image — no error, no
          // attachment, which reads as the app ignoring you.
          onPaste={(event) => {
            const files = Array.from(event.clipboardData?.files ?? []);
            if (!files.length) return;
            event.preventDefault();
            for (const file of files) void composer.addAttachment(file);
          }}
        />
        <ComposerAction />
      </div>
      <ComposerMeter />
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
const ComposerMeter: FC = () => {
  const messages = useAuiState((s) => s.thread.messages);
  const usage = latestUsage(messages);
  const reading = usage?.context ?? usage?.baseline;
  if (!reading) return null;
  return (
    <div className="mt-1.5 flex justify-center">
      <ContextMeter context={reading} folded={usage?.folded} />
    </div>
  );
};

/** The most recent assistant turn's usage data part, walking back from the end — there is
 * at most one per turn (see `USAGE_PART`), and an in-progress turn's is exactly as current
 * as the round that just landed. */
function latestUsage(messages: readonly unknown[]): TurnUsage | undefined {
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
const ComposerHint: FC = () => (
  <AuiIf condition={(s) => s.composer.isEmpty && s.composer.attachments.length === 0}>
    <div className="text-muted-foreground/40 pointer-events-none me-1 flex items-center gap-3 text-[10px] select-none">
      <span>
        <kbd className="font-sans">⏎</kbd> send
      </span>
      <span aria-hidden>·</span>
      <span>
        <kbd className="font-sans">⇧⏎</kbd> new line
      </span>
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

const ComposerAction: FC = () => {
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
        <AuiIf condition={(s) => s.thread.isRunning}>
          <ComposerPrimitive.Cancel asChild>
            <Button
              type="button"
              variant="default"
              size="icon"
              className="aui-composer-cancel size-7 rounded-full"
              aria-label="Stop generating"
            >
              <SquareIcon className="aui-composer-cancel-icon size-3.5 fill-current" />
            </Button>
          </ComposerPrimitive.Cancel>
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
                return part.dataRendererUI;
              case "indicator":
                return (
                  <span
                    data-slot="aui_assistant-message-indicator"
                    className="animate-pulse font-sans"
                    aria-label="Assistant is working"
                  >
                    {"●"}
                  </span>
                );
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
        <TurnUsageFooter />
      </div>
    </MessagePrimitive.Root>
  );
};

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
const TurnUsageFooter: FC = () => {
  // Serialised for the same reason as the run above: the selector compares by identity.
  const encoded = useAuiState((s) => {
    const part = s.message.parts.find(
      (one) => one.type === "data" && one.name === USAGE_PART,
    ) as { data?: unknown } | undefined;
    return part?.data ? JSON.stringify(part.data) : "";
  });
  if (!encoded) return null;
  let usage: TurnUsage;
  try {
    usage = JSON.parse(encoded) as TurnUsage;
  } catch {
    return null;
  }
  return <TurnTokens usage={usage} />;
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
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton tooltip="Copy">
          <AuiIf condition={(s) => s.message.isCopied}>
            <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
          </AuiIf>
          <AuiIf condition={(s) => !s.message.isCopied}>
            <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
          </AuiIf>
        </TooltipIconButton>
      </ActionBarPrimitive.Copy>
      <ActionBarPrimitive.Reload asChild>
        <TooltipIconButton tooltip="Refresh">
          <RefreshCwIcon />
        </TooltipIconButton>
      </ActionBarPrimitive.Reload>
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
          <ActionBarPrimitive.ExportMarkdown asChild>
            <ActionBarMorePrimitive.Item className="aui-action-bar-more-item hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none">
              <DownloadIcon className="size-4" />
              Export as Markdown
            </ActionBarMorePrimitive.Item>
          </ActionBarPrimitive.ExportMarkdown>
          {candidate ? (
            <ActionBarMorePrimitive.Item
              onSelect={onRestore}
              className="aui-action-bar-more-item hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none"
            >
              <RotateCcw className="size-4" />
              Restore files to here
            </ActionBarMorePrimitive.Item>
          ) : null}
        </ActionBarMorePrimitive.Content>
      </ActionBarMorePrimitive.Root>
    </ActionBarPrimitive.Root>
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
          <MessagePrimitive.Parts />
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
