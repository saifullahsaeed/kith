/**
 * Upload, for the Files screen: the queue that sends files into his folder, the tray that shows
 * each one arriving, and the board's "Drop files here" target.
 *
 * Apart from `files.tsx` because it is a small machine of its own — a queue, a question, a retry —
 * and the screen only needs three things from it: add these files to that folder, show the tray,
 * and say when one has landed so the listing can select it. The queue is `upload-queue.ts`; this
 * file is what it looks like.
 *
 * **One at a time.** The renderer shares six sockets with every stream it holds open, and a drop of
 * twenty files sent at once is twenty requests queued behind those — the freeze `kith-connection-
 * pool` describes. A queue sends them in order, and each one's progress is its own.
 *
 * **A name already taken is a question, not a failure.** The server answers 409 with the file that
 * is there; the row asks "replace it, or keep both?" with that file's size and date, and keeping
 * both picks `name (2).ext` here, past every name the folder listing already shows.
 */
import { Check, FileUp, Upload, X } from "lucide-react";

import { formatSize } from "@/lib/files";
import { cn } from "@/lib/utils";
import { BUTTON } from "./chrome";
import { modifiedLabel } from "./library";
import { folderName, type UploadItem } from "./upload-queue";

/* ── The tray ───────────────────────────────────────────────────────────── */

export function UploadTray({
  items,
  onReplace,
  onKeepBoth,
  onCancel,
  onDismiss,
}: {
  items: UploadItem[];
  onReplace: (id: string) => void;
  onKeepBoth: (id: string) => void;
  onCancel: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <ul aria-label="Uploads" className="border-border/80 flex max-h-[188px] shrink-0 flex-col gap-0.5 overflow-y-auto border-t px-2 py-1.5">
      {items.map((item) => (
        <UploadRow
          key={item.id}
          item={item}
          onReplace={() => onReplace(item.id)}
          onKeepBoth={() => onKeepBoth(item.id)}
          onCancel={() => onCancel(item.id)}
          onDismiss={() => onDismiss(item.id)}
        />
      ))}
    </ul>
  );
}

function UploadRow({
  item,
  onReplace,
  onKeepBoth,
  onCancel,
  onDismiss,
}: {
  item: UploadItem;
  onReplace: () => void;
  onKeepBoth: () => void;
  onCancel: () => void;
  onDismiss: () => void;
}) {
  const where = folderName(item.folder);
  const percent = item.total ? Math.min(100, Math.round((item.sent / item.total) * 100)) : 0;
  const small = cn(BUTTON.secondary, BUTTON.small);
  let line: React.ReactNode;
  let actions: React.ReactNode;
  switch (item.state) {
    case "queued":
      line = `Waiting · into ${where}`;
      actions = <IconAction label={`Don't upload ${item.name}`} onClick={onCancel} />;
      break;
    case "sending":
      line = `${formatSize(item.sent)} of ${formatSize(item.total)} · into ${where}`;
      actions = <IconAction label={`Stop uploading ${item.name}`} onClick={onCancel} />;
      break;
    case "done":
      line = `In ${where}`;
      actions = <Check aria-label="Uploaded" className="text-roam size-4 shrink-0" />;
      break;
    case "conflict": {
      const there = item.existing;
      line = (
        <>
          Already in {where}
          {there ? ` — ${formatSize(there.size)}, changed ${modifiedLabel(there.modified).replace(/^(Today|Yesterday)/, (d) => d.toLowerCase())}` : ""}
        </>
      );
      actions = (
        <span className="flex shrink-0 items-center gap-1.5">
          <button type="button" onClick={onReplace} className={small}>
            Replace
          </button>
          <button type="button" onClick={onKeepBoth} className={small}>
            Keep both
          </button>
          <IconAction label={`Don't upload ${item.name}`} onClick={onDismiss} />
        </span>
      );
      break;
    }
    case "failed":
      line = <span className="text-destructive">{item.error}</span>;
      actions = <IconAction label={`Dismiss ${item.name}`} onClick={onDismiss} />;
      break;
  }
  return (
    <li
      aria-label={`${item.name}: ${item.state === "sending" ? `${percent}%` : item.state}`}
      className="relative flex h-11 shrink-0 items-center gap-3 overflow-hidden rounded-[10px] px-2"
    >
      <span
        aria-hidden
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-lg [&_svg]:size-3.5",
          item.state === "conflict" ? "bg-kith/10 text-kith" : "bg-muted/60 text-muted-foreground",
        )}
      >
        <FileUp />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-px">
        <span className="text-foreground truncate text-[13px]">{item.name}</span>
        <span className="text-muted-foreground/80 truncate text-[11.5px] tabular-nums">{line}</span>
      </span>
      {item.state === "sending" ? (
        <span className="text-muted-foreground w-9 shrink-0 text-right text-xs tabular-nums">{percent}%</span>
      ) : null}
      {actions}
      {item.state === "sending" || item.state === "queued" ? (
        <span
          role="progressbar"
          aria-label={`Uploading ${item.name}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          className="bg-muted absolute inset-x-2 bottom-0.5 h-[2px] overflow-hidden rounded-full"
        >
          <span className="bg-kith block h-full rounded-full transition-[width] duration-150" style={{ width: `${percent}%` }} />
        </span>
      ) : null}
    </li>
  );
}

function IconAction({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="text-muted-foreground/80 hover:bg-muted/50 hover:text-foreground flex size-7 shrink-0 items-center justify-center rounded-[7px] transition-colors"
    >
      <X className="size-3.5" />
    </button>
  );
}

/* ── The drop target ────────────────────────────────────────────────────── */

/** The board's dashed box under the list. Lit while files are dragged over the list, naming the
 *  folder they would land in — the open one, or the folder row under the pointer. A button too:
 *  clicking it is the same as Upload, which is what anyone tries on a box that says "drop here". */
export function DropTarget({ folder, active, onPick }: { folder: string; active: boolean; onPick: () => void }) {
  return (
    <button
      type="button"
      onClick={onPick}
      className={cn(
        "mx-4 mt-auto mb-3 flex h-[60px] shrink-0 items-center justify-center gap-2 rounded-xl border border-dashed text-[12.5px] transition-colors",
        active
          ? "border-kith/50 bg-kith/10 text-foreground"
          : "text-muted-foreground/80 hover:text-foreground border-[color-mix(in_oklab,var(--foreground)_14%,transparent)]",
      )}
    >
      <Upload className="size-3.5" aria-hidden />
      {/* One span, so the button's gap does not open up between the sentence and its folder. */}
      <span>
        Drop files here to put them in{" "}
        {folder === "." || folder === "" ? (
          <span className="text-foreground/80">his folder</span>
        ) : (
          <span className="text-foreground/80 font-mono">{folderName(folder)}</span>
        )}
      </span>
    </button>
  );
}
