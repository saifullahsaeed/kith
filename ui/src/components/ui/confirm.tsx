import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { AlertTriangle, HelpCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export type ConfirmOptions = {
  title: string;
  /** Why this matters — one line under the title. */
  description?: string;
  /** The thing being acted on, quoted back so you can see what you're about to hit. */
  subject?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Red confirm button + warning glyph. Default for deletes. */
  destructive?: boolean;
};

type Ask = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<Ask | null>(null);

/** Replaces the browser's `window.confirm` — same await-a-yes shape, but it
 * looks like the rest of the room and can carry the subject of the action.
 *
 *     const confirm = useConfirm();
 *     if (!(await confirm({ title: "Delete this note?", subject: note.title, destructive: true }))) return;
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{ resolve: (ok: boolean) => void } | null>(null);
  // Held separately so the question is still on screen while the dialog fades out.
  const [o, setO] = useState<ConfirmOptions | null>(null);

  const ask = useCallback<Ask>(
    (options) =>
      new Promise<boolean>((resolve) => {
        setO(options);
        setPending({ resolve });
      }),
    [],
  );

  const settle = (ok: boolean) => {
    pending?.resolve(ok);
    setPending(null);
  };

  const destructive = o?.destructive ?? false;

  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      <Dialog open={pending != null} onOpenChange={(open) => !open && settle(false)}>
        <DialogContent showCloseButton={false} className="max-w-[26rem] gap-0 p-0 sm:max-w-[26rem]">
          <div className="flex gap-3.5 p-5">
            <span
              className={cn(
                "flex size-9 shrink-0 items-center justify-center rounded-xl",
                destructive ? "bg-destructive/12 text-destructive" : "bg-muted/70 text-kith",
              )}
            >
              {destructive ? <AlertTriangle className="size-4" /> : <HelpCircle className="size-4" />}
            </span>
            <div className="min-w-0 flex-1">
              <DialogTitle className="text-[15px] leading-snug">{o?.title ?? ""}</DialogTitle>
              <DialogDescription className="mt-1.5 text-[13px] leading-relaxed">
                {o?.description ?? (destructive ? "This can't be undone." : "")}
              </DialogDescription>
              {o?.subject ? (
                <p className="mt-3 line-clamp-4 rounded-lg border border-border/60 bg-muted/40 px-3 py-2 text-[13px] leading-relaxed break-words text-muted-foreground">
                  {o.subject}
                </p>
              ) : null}
            </div>
          </div>
          <DialogFooter className="gap-2 border-t border-border/60 bg-muted/25 px-5 py-3.5">
            {/* Destructive dialogs open with Cancel focused, so a stray Enter is safe. */}
            <Button variant="outline" size="sm" autoFocus={destructive} onClick={() => settle(false)}>
              {o?.cancelLabel ?? "Cancel"}
            </Button>
            <Button
              variant={destructive ? "destructive" : "default"}
              size="sm"
              autoFocus={!destructive}
              onClick={() => settle(true)}
            >
              {o?.confirmLabel ?? (destructive ? "Delete" : "Confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): Ask {
  const ask = useContext(ConfirmContext);
  if (!ask) throw new Error("useConfirm() needs a <ConfirmProvider> above it");
  return ask;
}
