import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { Check, Pencil, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const INPUT =
  "rounded-md border border-border/70 bg-background/60 px-2 py-1 text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40";

/**
 * Click-to-edit text: shows the rendered value, edits the source.
 *
 * Lifted out of the control panel so the task page can use it too. That page had a
 * permanent `<textarea>` for the task description — which made the field he writes the
 * most Markdown into the one place it could never be seen rendered.
 */
export function EditableText({
  value,
  onSave,
  multiline,
  placeholder,
  className = "",
  render,
}: {
  value: string;
  onSave: (v: string) => void;
  multiline?: boolean;
  placeholder?: string;
  className?: string;
  /** Display the saved value through this (e.g. rendered Markdown). Editing is
   * always the raw text. */
  render?: (value: string) => ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const ref = useRef<HTMLInputElement | HTMLTextAreaElement>(null);
  useEffect(() => {
    if (editing) ref.current?.focus();
  }, [editing]);
  // Cards can be clickable as a whole — editing shouldn't also open them.
  const startEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setDraft(value);
    setEditing(true);
  };

  if (!editing && render) {
    // Rendered content is block-level, so the pencil sits in the top-right
    // gutter instead of trailing the text inline.
    return (
      <div className={`group/edit relative pe-5 ${className}`}>
        {value ? (
          render(value)
        ) : (
          <em className="text-muted-foreground">{placeholder ?? "empty"}</em>
        )}
        <button
          className="absolute top-0 right-0 rounded p-0.5 text-muted-foreground opacity-0 transition group-hover/edit:opacity-100 hover:bg-accent hover:text-foreground"
          onClick={startEdit}
          aria-label="Edit"
        >
          <Pencil className="size-3" />
        </button>
      </div>
    );
  }

  if (!editing) {
    return (
      <span className={`group/edit inline-flex max-w-full items-start gap-1 ${className}`}>
        <span className="break-words whitespace-pre-wrap">
          {value || <em className="text-muted-foreground">{placeholder ?? "empty"}</em>}
        </span>
        <button
          className="mt-0.5 shrink-0 text-muted-foreground opacity-0 transition group-hover/edit:opacity-100 hover:text-foreground"
          onClick={startEdit}
          aria-label="Edit"
        >
          <Pencil className="size-3" />
        </button>
      </span>
    );
  }
  const commit = () => {
    onSave(draft);
    setEditing(false);
  };
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") setEditing(false);
    // ⌘/Ctrl+Enter saves from a textarea; plain Enter saves a single-line field.
    if (e.key === "Enter" && (!multiline || e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      commit();
    }
  };

  if (render) {
    // Markdown gets the full width to write in, with the buttons underneath.
    return (
      <div className="w-full" onClick={(e) => e.stopPropagation()}>
        <textarea
          ref={ref as RefObject<HTMLTextAreaElement>}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={keys}
          className={cn(INPUT, "min-h-44 w-full resize-y font-mono text-[13px] leading-relaxed")}
        />
        <div className="mt-1.5 flex items-center justify-end gap-1.5">
          <span className="me-auto text-[10px] whitespace-nowrap text-muted-foreground/70">
            ⌘↵ saves
          </span>
          <Button variant="ghost" size="xs" onClick={() => setEditing(false)}>
            Cancel
          </Button>
          <Button size="xs" onClick={commit}>
            <Check className="size-3" />
            Save
          </Button>
        </div>
      </div>
    );
  }

  return (
    <span className="flex w-full items-start gap-1" onClick={(e) => e.stopPropagation()}>
      {multiline ? (
        <textarea
          ref={ref as RefObject<HTMLTextAreaElement>}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={keys}
          className={`${INPUT} min-h-16 flex-1 resize-y`}
        />
      ) : (
        <input
          ref={ref as RefObject<HTMLInputElement>}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={keys}
          className={`${INPUT} flex-1`}
        />
      )}
      <Button variant="ghost" size="icon" className="size-6" onClick={commit} aria-label="Save">
        <Check className="size-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="size-6"
        onClick={() => setEditing(false)}
        aria-label="Cancel"
      >
        <X className="size-4" />
      </Button>
    </span>
  );
}
