"use client";

/**
 * The composer's input, as a document rather than a string.
 *
 * `ComposerPrimitive.Input` is a textarea and does a great deal more than hold text: it autosizes
 * and syncs a controlled value. Replacing it means taking on all of that by hand. The `/` sheet
 * lives here too — it is a function of this editor's first line and caret, so this is the one
 * place that knows both (see `lib/slash.ts`).
 *
 * What does *not* change is the value everything else reads. The composer store still holds one
 * markdown string; see `markdown.ts`. This component is a translation layer with a caret in it.
 */

import { EditorContent, useEditor } from "@tiptap/react";
import type { Editor } from "@tiptap/react";
import { TextSelection } from "@tiptap/pm/state";
import { unstable_useComposerInput, useAuiState, useComposerRuntime } from "@assistant-ui/react";
import { type FC, useCallback, useEffect, useRef, useState } from "react";

import { steerTurn } from "@/lib/commands";
import { useConversationId } from "@/lib/conversation";
import { holdUntilIdle } from "@/lib/queued-send";
import {
  completeWord,
  expandCommand,
  leadingCommand,
  matchCommands,
  slashWord,
  type SlashCommand,
} from "@/lib/slash";
import { cn } from "@/lib/utils";

import { COMPOSER_EXTENSIONS, fromMarkdown, toMarkdown } from "./markdown";
import { handlePaste } from "./paste";
import { SLASH_REFRESH, SlashToken } from "./slash-token";

/**
 * Below this, the document is serialised on every keystroke; above it, on a trailing debounce.
 *
 * Not a compromise — the two halves answer different problems. Serialising a long document on
 * every character is wasted work, and that is what the debounce is for. But two things read the
 * store's text *synchronously*, and a value that lags by even one keystroke breaks them: the send
 * button reads the store rather than the editor, and so does the workflow edge on the box. Both
 * matter in short documents, so writing immediately while the document is small costs nothing
 * and keeps them exact.
 */
const IMMEDIATE_BELOW = 4_000;
const DEBOUNCE_MS = 120;

/** The `/` sheet as it stands: the word being typed, what it could be, and which one Enter takes. */
interface Sheet {
  word: string;
  items: SlashCommand[];
  index: number;
}

/** The first line of the document as text, and the caret in it — or no caret, when the selection
 *  is a range or is not in that line. Hard breaks count as one character, as they do in positions. */
function firstLine(editor: Editor): { line: string; caret: number | null } {
  const { doc, selection } = editor.state;
  const first = doc.firstChild;
  if (!first || first.type.name !== "paragraph") return { line: "", caret: null };
  const line = first.textBetween(0, first.content.size, "\n", "\n");
  const inLine = selection.empty && selection.$from.parent === first;
  return { line, caret: inLine ? selection.$from.parentOffset : null };
}

export const RichComposerInput: FC<{
  placeholder?: string;
  className?: string;
  autoFocus?: boolean;
  /** What `/` offers. Actions run from here; prefixes are only ever words in the box. */
  commands?: readonly SlashCommand[];
}> = ({ placeholder, className, autoFocus, commands = [] }) => {
  const { value, setText, send, isDisabled } = unstable_useComposerInput();
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
  const [sheet, setSheet] = useState<Sheet | null>(null);
  /** Closed by hand — Escape, or Tab onto an action — for the rest of this slash word. It opens
   *  again only for a new one, once the caret has left this word or the slash is gone: the old
   *  sheet reopened on the very next keystroke, which is not what closing it meant. */
  const dismissed = useRef(false);

  /** Read by callbacks that outlive the render they were made in. `useEditor`'s handlers are
   *  created once; without this they would forever see the first render's values. */
  const latest = useRef({ send, running, text: value, clear: () => setText(""), commands, sheet });
  latest.current = { send, running, text: value, clear: () => setText(""), commands, sheet };
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

  /**
   * What ⏎ / ⌘⏎ actually do. One function, because two callers need it and they must not drift.
   *
   * Neither branch may go through the runtime while a turn is running: `performRoundtrip`'s
   * first line is `abortController.abort()`, so anything routed that way kills the run it was
   * meant to steer or wait behind. ⏎ posts to `/steer`; ⌘⏎ holds the text here until the turn
   * has genuinely ended and only then hands it back to the composer to send normally.
   */
  /* Which chat this composer belongs to, from the pane above it rather than from a module-level
   * slot. `currentConversation()` was one getter for the whole process, overwritten by whichever
   * pane built its adapter last — so with several chats open, a steer could be delivered into a
   * conversation other than the one you were typing in. A context has one value per subtree, and
   * a subtree here is a conversation. */
  const here = useConversationId();

  const decide = useCallback(
    (queueIt: boolean) => {
      const said = latest.current.text;
      const where = here;
      const running = latest.current.running;

      // `/fold` and `/stop` are the whole message and are done here, not sent — while he works
      // too, where sending would have steered him with the word "/stop".
      const opening = leadingCommand(said.trim(), latest.current.commands);
      if (opening?.command.kind === "action" && !opening.rest.trim()) {
        latest.current.clear();
        opening.command.run?.();
        return;
      }

      if (running && where && said.trim()) {
        latest.current.clear();
        if (queueIt) {
          void holdUntilIdle(where, said, (text) => {
            setText(text);
            // A tick, so the store holds the restored text before the send reads it.
            window.setTimeout(() => latest.current.send(), 0);
          });
        } else {
          // Spelled out here because a steer never passes the adapter, which does it for sends.
          void steerTurn(where, expandCommand(said));
        }
        return;
      }
      // Nothing running (or nothing to say): an ordinary send, which is what both keys mean
      // when there is no turn to steer or queue behind.
      latest.current.send();
    },
    [setText],
  );

  /** Where the sheet stands after anything moved: open while the caret is in a slash word at the
   *  start of the message that could still become a command, unless it was closed on this word. */
  const readSheet = useCallback((editor: Editor) => {
    const { line, caret } = firstLine(editor);
    const word = editor.isFocused ? slashWord(line, caret) : null;
    if (word === null || dismissed.current) {
      if (word === null) dismissed.current = false;
      setSheet(null);
      return;
    }
    const items = matchCommands(latest.current.commands, word);
    setSheet((was) =>
      items.length ? { word, items, index: was && was.word === word ? Math.min(was.index, items.length - 1) : 0 } : null,
    );
  }, []);

  /** Put a command in place of the slash word, and — for an action picked with Enter or a click —
   *  do it. A prefix leaves the caret after its space, ready for the request. */
  const pick = useCallback((editor: Editor, command: SlashCommand, go: boolean) => {
    setSheet(null);
    if (go && command.kind === "action") {
      editor.commands.clearContent(true);
      command.run?.();
      return;
    }
    const { line } = firstLine(editor);
    const done = completeWord(line, command);
    const tr = editor.state.tr.insertText(done.text, 1, 1 + done.replaced);
    tr.setSelection(TextSelection.create(tr.doc, 1 + done.caret));
    dismissed.current = true;
    editor.view.dispatch(tr);
  }, []);
  const pickRef = useRef(pick);
  pickRef.current = pick;

  const editor = useEditor({
    extensions: [...COMPOSER_EXTENSIONS, SlashToken.configure({ commands: () => latest.current.commands })],
    content: fromMarkdown(value).toJSON(),
    autofocus: autoFocus ? "end" : false,
    editable: !isDisabled,
    editorProps: {
      attributes: {
        class: cn(
          "kith-composer-doc max-h-64 min-h-14 w-full overflow-y-auto px-3.5 py-2.5 text-base outline-none",
          className,
        ),
        role: "textbox",
        "aria-label": "Message input",
        "aria-autocomplete": "list",
      },
      handlePaste: (view, event) =>
        handlePaste(view, event, {
          attach: (file) => void composer.addAttachment(file),
          ordinal: () => ++pastes.current,
        }),
      handleKeyDown: (_view, event) => {
        // The sheet first, while it is open: Enter there picks a command rather than sending.
        // ↑↓ move, Enter picks (and does an action), Tab takes the highlighted command into the
        // box and closes the sheet without doing anything, Escape just closes it.
        const open = latest.current.sheet;
        const ed = self.current;
        if (open && ed && !event.isComposing) {
          const move = (by: number) =>
            setSheet((was) => (was ? { ...was, index: (was.index + by + was.items.length) % was.items.length } : was));
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            move(event.key === "ArrowDown" ? 1 : -1);
            return true;
          }
          if ((event.key === "Enter" && !event.shiftKey) || (event.key === "Tab" && !event.shiftKey)) {
            const command = open.items[open.index];
            if (command) pickRef.current(ed, command, event.key === "Enter");
            return true;
          }
          if (event.key === "Escape" || (event.key === "Tab" && event.shiftKey)) {
            dismissed.current = true;
            setSheet(null);
            return true;
          }
        }
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          // Flushed first: with a pending debounce the store still holds the text as it was one
          // keystroke ago, and sending would post that instead of what is on screen.
          flush(self.current);

          // Both keys, and what they mean, live in `decide` — see it for why neither may reach
          // the runtime while a turn is in flight. The same function backs the DOM-level
          // fallback below, so the two paths cannot disagree about what ⌘⏎ does.
          decide(event.metaKey || event.ctrlKey);
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
      readSheet(editor);
    },
    onSelectionUpdate: ({ editor }) => readSheet(editor),
    onFocus: ({ editor }) => readSheet(editor),
    onBlur: ({ editor }) => {
      flush(editor);
      setSheet(null);
    },
  });

  useEffect(() => {
    self.current = editor;
  }, [editor]);

  /** Text that arrived from somewhere other than typing.
   *
   * No selection handling here, and that is a deliberate non-change. It looks like it needs some:
   * `setContent` replaces the whole document, so surely the caret ends up at the top and every
   * `/skill` and every restored draft drops you in front of the words. It does not — ProseMirror
   * maps the existing selection through the replacement's `ReplaceStep`, which lands it at the end
   * of the inserted content. Measured: an empty editor given a 17-character draft ends at position
   * 17 of 18, and a `setTextSelection(doc.content.size)` after it changes nothing.
   *
   * Worse than nothing, in fact, which is why the line that was here is gone. StarterKit's
   * `TrailingNode` keeps an empty paragraph after any trailing block, so on a draft that ends in a
   * list, a fenced block, a table or a quote, the document's true end is *inside that paragraph* —
   * one line below the block. Forcing the caret there turned "- two" plus "three" into a new
   * top-level paragraph instead of continuing the item. */
  useEffect(() => {
    if (!editor || value === pushed.current) return;
    pushed.current = value;
    editor.commands.setContent(fromMarkdown(value).toJSON(), { emitUpdate: false });
    readSheet(editor);
  }, [editor, value, readSheet]);

  /* The skill list lands after the editor exists; a word typed before it did is redrawn as the
   * token it now is, and an open sheet learns what it can offer. */
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    editor.view.dispatch(editor.state.tr.setMeta(SLASH_REFRESH, true));
    readSheet(editor);
  }, [editor, commands, readSheet]);

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

  /**
   * ⏎ and ⌘⏎, again, in case the editor never offered them.
   *
   * `handleKeyDown` in `editorProps` is the intended path and usually the only one that runs.
   * But it is ProseMirror's to call, and it is not the only thing bound to these keys: TipTap's
   * base keymap claims `Mod-Enter` (it is `exitCode`, for leaving a code block), and in the
   * desktop shell ⌘⏎ was reaching the composer and doing nothing at all — no send, no queue, no
   * fall-through, which is not a state the handler above can produce. Whatever consumed it, the
   * message was lost between the keyboard and the code meant to act on it.
   *
   * So the decision lives in `decide()` and is offered twice: once where it belongs, and once
   * here as a backstop. **Bubble phase, and only when nothing has claimed the event** — if the
   * editor's own handler ran it called `preventDefault`, and this sees that and stays out of the
   * way. It can therefore only ever fire when the first path did not, which is exactly the case
   * being covered.
   */
  useEffect(() => {
    if (!editor) return;
    const dom = editor.view.dom;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return; // the editor already dealt with it
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
      if (!dom.contains(document.activeElement)) return;
      event.preventDefault();
      flush(self.current);
      decide(event.metaKey || event.ctrlKey);
    };
    dom.addEventListener("keydown", onKeyDown);
    return () => dom.removeEventListener("keydown", onKeyDown);
  }, [editor, flush, decide]);

  useEffect(() => {
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  return (
    <div className="relative w-full">
      {sheet && editor ? (
        <SlashSheet
          sheet={sheet}
          onHover={(index) => setSheet((was) => (was ? { ...was, index } : was))}
          onPick={(command) => pick(editor, command, true)}
        />
      ) : null}
      <EditorContent editor={editor} />
      {/* The placeholder, rendered rather than an extension: one more package to hold one line of
          text, and this way it says the same thing in the same place as it did in the textarea. */}
      {!value && (
        <span className="text-muted-foreground/70 pointer-events-none absolute start-3.5 top-2.5 select-none text-base">
          {placeholder}
        </span>
      )}
    </div>
  );
};

/**
 * The sheet: what the slash word could be, above the line you are typing on.
 *
 * Inside the box rather than floating over the conversation — no fill, border or shadow of its
 * own, one hairline between it and the input, growing upward because the composer is pinned to the
 * bottom of the window. A long description is given in full beneath the list, since a skill's
 * runs to a paragraph and one truncated line does not say when to use it.
 */
function SlashSheet({
  sheet,
  onHover,
  onPick,
}: {
  sheet: Sheet;
  onHover: (index: number) => void;
  onPick: (command: SlashCommand) => void;
}) {
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    list.current?.querySelector('[data-highlighted="true"]')?.scrollIntoView({ block: "nearest" });
  }, [sheet.index, sheet.word]);
  const current = sheet.items[sheet.index];

  return (
    // `data-slash-panel` is how the composer's Escape knows the key is the sheet's, not Stop's.
    <div data-slash-panel className="border-border flex max-h-80 flex-col overflow-hidden border-b">
      <div ref={list} role="listbox" aria-label="Commands" className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {sheet.items.map((command, index) => (
          <button
            key={command.name}
            type="button"
            role="option"
            aria-selected={index === sheet.index}
            data-highlighted={index === sheet.index}
            // Keep the caret where it is; a click must not take focus from the editor first.
            onMouseDown={(event) => event.preventDefault()}
            onMouseMove={() => index !== sheet.index && onHover(index)}
            onClick={() => onPick(command)}
            className="data-[highlighted=true]:bg-accent/70 flex w-full items-baseline gap-2.5 rounded-lg px-2.5 py-1.5 text-left"
          >
            <span className="text-foreground w-44 shrink-0 truncate font-mono text-[12.5px]">/{command.name}</span>
            <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">{command.description}</span>
          </button>
        ))}
      </div>
      {/* Only when the row cuts it short — a one-line description said twice is noise. */}
      {current && current.description.length > 60 ? (
        <p className="border-border text-foreground/85 line-clamp-3 shrink-0 border-t px-3 py-2 text-xs leading-relaxed">
          {current.description}
        </p>
      ) : null}
      <p className="border-border text-muted-foreground/70 flex shrink-0 gap-3 border-t px-3 py-1.5 font-mono text-[10px]">
        <span>↑↓ move</span>
        <span>↵ choose</span>
        <span>tab complete</span>
        <span>esc close</span>
      </p>
    </div>
  );
}
