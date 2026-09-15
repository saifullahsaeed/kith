import { useEffect, useState } from "react";
import { ChevronDown, LayoutTemplate, Plus, RotateCcw, Trash2 } from "lucide-react";

import { useConfirm } from "@/components/ui/confirm";
import { usePrompt } from "@/components/ui/prompt";

import { useLayout } from "./store";

/**
 * Saved arrangements, in the title bar.
 *
 * Built on the same shape as `Picker` in `header-controls.tsx` rather than a new menu system:
 * a relative wrapper, escape and click-away on `window`, and `stopPropagation` on every
 * `pointerdown` inside it. That last one is not decoration — the header is
 * `.window-drag-region`, so a pointerdown that reaches it starts dragging the window, and a
 * menu you open by nudging the app two inches to the left is the bug the comment beside
 * `Picker` records having already been fixed once.
 *
 * A list rather than a `<select>`: there is deliberately no "current layout". Loading one
 * replaces the live arrangement and the live arrangement then diverges from it immediately —
 * the next tab you open is not part of any saved layout — so a control that showed one of them
 * as selected would be lying within seconds.
 */
export function LayoutsMenu() {
  const layouts = useLayout((s) => s.layouts);
  const saveLayout = useLayout((s) => s.saveLayout);
  const loadLayout = useLayout((s) => s.loadLayout);
  const deleteLayout = useLayout((s) => s.deleteLayout);
  const reset = useLayout((s) => s.reset);
  const prompt = usePrompt();
  const confirm = useConfirm();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    const onDown = () => setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const save = async () => {
    setOpen(false);
    const name = await prompt({
      title: "Save this arrangement",
      description:
        "Panes, sizes and everything open in them except chats — a shape to come back to, not a bookmark into old conversations.",
      placeholder: "Writing",
      confirmLabel: "Save",
    });
    if (name?.trim()) saveLayout(name);
  };

  const remove = async (name: string) => {
    setOpen(false);
    if (!(await confirm({ title: "Delete this arrangement?", subject: name, destructive: true }))) {
      return;
    }
    deleteLayout(name);
  };

  return (
    <div className="relative">
      <button
        type="button"
        title="Saved arrangements"
        aria-label="Saved arrangements"
        aria-expanded={open}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => setOpen((was) => !was)}
        className="text-muted-foreground hover:text-foreground hover:bg-accent/60 flex items-center gap-1 rounded-md px-1.5 py-1 transition-colors"
      >
        <LayoutTemplate className="size-4" />
        <ChevronDown className="size-3 opacity-50" />
      </button>

      {open ? (
        <div
          onPointerDown={(event) => event.stopPropagation()}
          className="border-border/70 bg-popover/95 absolute end-0 top-full z-50 mt-1 w-64 rounded-xl border p-1 shadow-xl backdrop-blur-xl"
        >
          {layouts.length ? (
            layouts.map((one) => (
              <div key={one.name} className="hover:bg-accent group flex items-center rounded-lg">
                <button
                  type="button"
                  onClick={() => {
                    loadLayout(one.name);
                    setOpen(false);
                  }}
                  className="min-w-0 flex-1 truncate px-2.5 py-2 text-left text-sm"
                >
                  {one.name}
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${one.name}`}
                  onClick={() => void remove(one.name)}
                  className="hover:text-destructive mr-1 rounded p-1.5 opacity-0 transition-opacity group-hover:opacity-60 hover:opacity-100"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            ))
          ) : (
            /* An empty screen is an invitation, and this one has to say what the thing even is —
               nothing else in the app mentions that arrangements can be kept. */
            <p className="text-muted-foreground/80 px-2.5 py-2 text-[11px] leading-snug">
              Nothing saved yet. Arrange the panes how you want them, then keep the shape.
            </p>
          )}

          <div className="bg-border/60 my-1 h-px" aria-hidden />
          <button
            type="button"
            onClick={() => void save()}
            className="hover:bg-accent flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm"
          >
            <Plus className="size-3.5 opacity-70" />
            Save this arrangement
          </button>
          <button
            type="button"
            onClick={() => {
              reset();
              setOpen(false);
            }}
            className="hover:bg-accent text-muted-foreground flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm"
          >
            <RotateCcw className="size-3.5 opacity-70" />
            Back to the default
          </button>
        </div>
      ) : null}
    </div>
  );
}
