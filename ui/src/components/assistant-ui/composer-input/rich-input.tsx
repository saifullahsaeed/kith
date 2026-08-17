"use client";

/**
 * The composer's input, as a document rather than a string.
 *
 * `ComposerPrimitive.Input` is a textarea and does a great deal more than hold text: it feeds the
 * trigger popover, reports the caret, autosizes, and syncs a controlled value. Replacing it means
 * taking on all of that by hand, and the parts that are easy to forget are the ones nothing warns
 * you about — the slash menu simply stops opening, with no error anywhere.
 *
 * What does *not* change is the value everything else reads. The composer store still holds one
 * markdown string; see `markdown.ts`. This component is a translation layer with a caret in it.
 */

import { EditorContent, useEditor } from "@tiptap/react";
import type { Editor } from "@tiptap/react";
import {
  unstable_useComposerInput,
  unstable_useTriggerPopoverAriaProps,
  unstable_useTriggerPopoverTriggers,
  useAuiState,
  useComposerRuntime,
} from "@assistant-ui/react";
import { type FC, useCallback, useEffect, useRef } from "react";

import { steerTurn } from "@/lib/commands";
import { currentConversation, queueNextSend } from "@/lib/queued-send";
import { cn } from "@/lib/utils";

import { COMPOSER_EXTENSIONS, fromMarkdown, markdownOffset, toMarkdown } from "./markdown";
import { handlePaste } from "./paste";

/**
 * Below this, the document is serialised on every keystroke; above it, on a trailing debounce.
 *
 * Not a compromise — the two halves answer different problems. Serialising a long document on
 * every character is wasted work, and that is what the debounce is for. But two things read the
 * store's text *synchronously*, and a value that lags by even one keystroke breaks both: the
 * slash menu filters on `s.composer.text`, and the send button reads the store rather than the
 * editor. Both of those happen in short documents — `/` is typed into an empty box — so writing
 * immediately while the document is small costs nothing and keeps them exact.
 */
const IMMEDIATE_BELOW = 4_000;
const DEBOUNCE_MS = 120;

export const RichComposerInput: FC<{
  placeholder?: string;
  className?: string;
  autoFocus?: boolean;
}> = ({ placeholder, className, autoFocus }) => {
  const { value, setText, send, isDisabled } = unstable_useComposerInput();
  const triggers = unstable_useTriggerPopoverTriggers();
  const aria = unstable_useTriggerPopoverAriaProps();
  const composer = useComposerRuntime();
  /** Whether a turn is in flight. Enter means something different when it is — see the key
   *  handler — so this is read here rather than inferred from a disabled button. */
  const running = useAuiState((s) => s.thread.isRunning);

  /** The markdown this component last wrote to the store. Anything else arriving on `value` came
   *  from elsewhere — `/skill`, a quote, a restored draft — and has to be parsed back in. Without
   *  this the editor would rebuild itself from its own output on every keystroke, which loses the
   *  caret on every character typed. */
  const pushed = useRef("");
  const timer = useRef<number | null>(null);
  /** Pastes carried so far, for naming. A ref, not state: naming a file must not re-render. */
  const pastes = useRef(0);
  /** Read by callbacks that outlive the render they were made in. `useEditor`'s handlers are
   *  created once; without this they would forever see the first render's triggers, and the slash
   *  menu registers itself after that render. */
  const latest = useRef({ triggers, send, running, text: value, clear: () => setText("") });
  latest.current = { triggers, send, running, text: value, clear: () => setText("") };
  /** The editor, reachable from handlers that are only given a view. */
  const self = useRef<Editor | null>(null);

  const write = useCallback(
    (editor: Editor) => {
      const markdown = toMarkdown(editor.state.doc);
      pushed.current = markdown;
      setText(markdown);
    },
    [setText],
  );

  const flush = useCallback(
    (editor: Editor | null) => {
      if (timer.current === null || !editor) return;
      window.clearTimeout(timer.current);
      timer.current = null;
      write(editor);
    },
    [write],
  );

  /** Tell every registered trigger where the caret is, in characters of markdown. This is what
   *  makes `/` know it is at the start of a word rather than in the middle of one. */
  const reportCaret = useCallback((editor: Editor) => {
    const offset = markdownOffset(editor.state.doc, editor.state.selection.from);
    for (const trigger of latest.current.triggers.values()) trigger.resource.setCursorPosition(offset);
  }, []);

  const editor = useEditor({
    extensions: COMPOSER_EXTENSIONS,
    content: fromMarkdown(value).toJSON(),
    autofocus: autoFocus ? "end" : false,
    editable: !isDisabled,
    editorProps: {
      attributes: {
        class: cn(
          "kith-composer-doc max-h-64 min-h-10 w-full overflow-y-auto px-2.5 py-1 text-base outline-none",
          className,
        ),
        role: "textbox",
        "aria-label": "Message input",
      },
      handlePaste: (view, event) =>
        handlePaste(view, event, {
          attach: (file) => void composer.addAttachment(file),
          ordinal: () => ++pastes.current,
        }),
      handleKeyDown: (_view, event) => {
        // Triggers first, always. Enter with the slash menu open must run the highlighted command,
        // not send the message — and the menu can only claim the key if it is offered the key
        // before anything else looks at it.
        for (const trigger of latest.current.triggers.values()) {
          if (trigger.resource.handleKeyDown(event)) return true;
        }
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          // Flushed first: with a pending debounce the store still holds the text as it was one
          // keystroke ago, and sending would post that instead of what is on screen.
          flush(self.current);

          const queueIt = event.metaKey || event.ctrlKey;
          // ⏎ while he is working steers the turn — and it is done HERE, not in the adapter,
          // because anything that reaches the adapter has already gone through
          // `performRoundtrip`, whose first line is `abortController.abort()`. That cancels the
          // run in flight, our abort handler posts `/stop`, and the turn dies. A steer routed
          // through the runtime would kill the work it exists to redirect.
          //
          // ⌘⏎ is the other intent — after this, not instead of it — and it does go through the
          // runtime, because by the time it sends there is nothing left to abort.
          if (!queueIt && latest.current.running) {
            const said = latest.current.text;
            const where = currentConversation();
            if (where && said.trim()) {
              void steerTurn(where, said);
              latest.current.clear();
              return true;
            }
          }
          if (queueIt) queueNextSend();
          latest.current.send();
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor }) => {
      if (editor.state.doc.content.size < IMMEDIATE_BELOW) {
        if (timer.current !== null) window.clearTimeout(timer.current);
        timer.current = null;
        write(editor);
      } else {
        if (timer.current !== null) window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => {
          timer.current = null;
          write(editor);
        }, DEBOUNCE_MS);
      }
      reportCaret(editor);
    },
    onSelectionUpdate: ({ editor }) => reportCaret(editor),
    onBlur: ({ editor }) => flush(editor),
  });

  useEffect(() => {
    self.current = editor;
  }, [editor]);

  /** Text that arrived from somewhere other than typing. */
  useEffect(() => {
    if (!editor || value === pushed.current) return;
    pushed.current = value;
    editor.commands.setContent(fromMarkdown(value).toJSON(), { emitUpdate: false });
  }, [editor, value]);

  useEffect(() => {
    editor?.setEditable(!isDisabled);
  }, [editor, isDisabled]);

  /**
   * The send button is not in this component and does not know it exists — it reads the store. So
   * a pending debounce has to be resolved before any click lands anywhere, and the only moment
   * that is reliably true is pointer-down, before focus moves and before the click fires.
   */
  useEffect(() => {
    if (!editor) return;
    const onPointerDown = () => flush(editor);
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [editor, flush]);

  useEffect(() => {
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  return (
    <div className="relative w-full">
      <EditorContent editor={editor} {...aria} />
      {/* The placeholder, rendered rather than an extension: one more package to hold one line of
          text, and this way it says the same thing in the same place as it did in the textarea. */}
      {!value && (
        <span className="text-muted-foreground/80 pointer-events-none absolute start-2.5 top-1 select-none text-base">
          {placeholder}
        </span>
      )}
    </div>
  );
};
