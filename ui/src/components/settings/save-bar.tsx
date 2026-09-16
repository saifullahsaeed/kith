import { Loader2, Undo2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The one save bar, at the foot of whichever page has something unsaved.
 *
 * Saving differed per page: Conversation tracked a dirty flag and put a button under the last
 * section, Model had its own with different wording and a different disabled rule, Advanced had
 * a third, and the rest saved on change. Three of those are defensible on their own and the set
 * is not — the question "will this take effect, or do I have to do something" got a different
 * answer depending on which sidebar entry you happened to be on.
 *
 * So: one bar, pinned to the foot of the page, **absent entirely when there is nothing to
 * save** — a permanently visible Save that is usually disabled trains people to ignore it.
 *
 * It names what is unsaved rather than counting it. "3 unsaved changes" is a number; "reply
 * length, reasoning, session budget" is the thing you actually want to check before you commit
 * it, and it is the cheap half of an undo.
 */
export function SaveBar({
  changed,
  saving,
  error,
  onRevert,
  onSave,
  saveLabel = "Save changes",
  sticky,
}: {
  /** The labels of what is unsaved. Empty renders nothing at all. */
  changed: string[];
  saving?: boolean;
  error?: string;
  onRevert: () => void;
  onSave: () => void;
  /** Overridden where the noun matters — "Save fragment", "Save connection". */
  saveLabel?: string;
  /** Inside the page's own scroller rather than below it, for a page that composes two halves
   *  and owns the scrolling itself. Sticks to the foot either way. */
  sticky?: boolean;
}) {
  if (!changed.length && !error) return null;

  return (
    <div className={cn(
        "border-border/60 bg-card/80 flex shrink-0 items-center gap-3 border-t px-8 py-2.5 backdrop-blur-sm",
        sticky && "sticky bottom-0 -mx-8 mt-6",
      )}>
      <p className="min-w-0 flex-1 text-[12.5px]">
        {error ? (
          <span className="text-destructive">{error}</span>
        ) : (
          <>
            <b className="font-semibold">
              {changed.length} unsaved {changed.length === 1 ? "change" : "changes"}
            </b>
            {/* Named, not just counted — and capped, because a bar that wraps to three lines
                stops being a bar. */}
            <span className="text-muted-foreground">
              {" — "}
              {changed.slice(0, 3).join(", ")}
              {changed.length > 3 ? ` and ${changed.length - 3} more` : ""}
            </span>
          </>
        )}
      </p>

      <Button variant="ghost" size="sm" onClick={onRevert} disabled={saving}>
        <Undo2 className="size-3.5" />
        Revert
      </Button>
      <Button size="sm" onClick={onSave} disabled={saving || !changed.length}>
        {saving ? <Loader2 className="size-3.5 animate-spin" /> : null}
        {saving ? "Saving…" : saveLabel}
      </Button>
    </div>
  );
}
