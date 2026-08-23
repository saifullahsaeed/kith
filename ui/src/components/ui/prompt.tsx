import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";

export type PromptOptions = {
  title: string;
  description?: string;
  /** What the field starts with — a rename starts with the current name, selected. */
  initial?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
};

/** Resolves to the typed text, or null when it was cancelled — the shape `window.prompt` had. */
type Ask = (options: PromptOptions) => Promise<string | null>;

const PromptContext = createContext<Ask | null>(null);

/**
 * Replaces `window.prompt`, which does not exist in this app.
 *
 * Not a preference: **Electron does not implement it.** Calling `window.prompt()` in the renderer
 * throws `prompt() is not supported` — verified by launching this repo's own Electron and
 * evaluating it — and the throw escapes a Radix `onSelect` handler, where React does not catch it
 * and no error boundary sees it. So three features were simply dead in the desktop shell, with no
 * dialog, no error and nothing in the interface to say why: renaming a conversation, adding a
 * persona fragment, and renaming one. They worked when the same build was opened in a browser tab,
 * which is how it survived.
 *
 * Deliberately the same shape as `ConfirmProvider` next door — one provider at the root, a hook
 * that returns a promise — because the two are the same question with a different answer type, and
 * two dialog systems is how they come to look like two different applications.
 *
 *     const prompt = usePrompt();
 *     const name = await prompt({ title: "Rename this conversation", initial: current });
 *     if (name) …
 */
export function PromptProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{ resolve: (value: string | null) => void } | null>(null);
  // Held apart from `pending` so the question is still on screen while the dialog fades out.
  const [o, setO] = useState<PromptOptions | null>(null);
  const [value, setValue] = useState("");

  const ask = useCallback<Ask>(
    (options) =>
      new Promise<string | null>((resolve) => {
        setO(options);
        setValue(options.initial ?? "");
        setPending({ resolve });
      }),
    [],
  );

  const settle = (text: string | null) => {
    // Empty is a cancel. Renaming something to nothing is never what was meant, and the
    // alternative — a disabled button — leaves you looking for what is wrong with an empty field.
    pending?.resolve(text && text.trim() ? text.trim() : null);
    setPending(null);
  };

  return (
    <PromptContext.Provider value={ask}>
      {children}
      <Dialog open={pending != null} onOpenChange={(open) => !open && settle(null)}>
        <DialogContent showCloseButton={false} className="max-w-[26rem] gap-0 p-0 sm:max-w-[26rem]">
          <div className="flex gap-3.5 p-5">
            <span className="bg-muted/70 text-kith flex size-9 shrink-0 items-center justify-center rounded-xl">
              <Pencil className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <DialogTitle className="text-[15px] leading-snug">{o?.title ?? ""}</DialogTitle>
              {o?.description ? (
                <DialogDescription className="mt-1.5 text-[13px] leading-relaxed">
                  {o.description}
                </DialogDescription>
              ) : null}
              <form
                className="mt-3"
                onSubmit={(event) => {
                  event.preventDefault();
                  settle(value);
                }}
              >
                {/* Focused and selected on open, so typing replaces the old name — which is what
                    a rename usually is, and what `window.prompt` did. */}
                <input
                  autoFocus
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  onFocus={(event) => event.currentTarget.select()}
                  placeholder={o?.placeholder}
                  aria-label={o?.title ?? "Value"}
                  className="border-border/60 bg-background/60 focus-visible:border-ring focus-visible:ring-ring/40 w-full rounded-lg border px-3 py-2 text-[13px] outline-none focus-visible:ring-2"
                />
                {/* Enter submits. The button below is outside the form, so this is what carries it. */}
                <button type="submit" className="hidden" aria-hidden />
              </form>
            </div>
          </div>
          <DialogFooter className="border-border/60 bg-muted/25 gap-2 border-t px-5 py-3.5">
            <Button variant="outline" size="sm" onClick={() => settle(null)}>
              {o?.cancelLabel ?? "Cancel"}
            </Button>
            <Button size="sm" onClick={() => settle(value)}>
              {o?.confirmLabel ?? "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PromptContext.Provider>
  );
}

export function usePrompt(): Ask {
  const ask = useContext(PromptContext);
  if (!ask) throw new Error("usePrompt() needs a <PromptProvider> above it");
  return ask;
}
