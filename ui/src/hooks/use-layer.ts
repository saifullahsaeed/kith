import { useEffect, useMemo, useRef } from "react";

/**
 * Which surface owns a global keystroke right now.
 *
 * Escape and ⌘F are bound to `window`, because the thing that should answer them is usually not
 * the thing with focus. That works until two surfaces are open at once, and then every one of
 * them has to answer "am I the one being talked to" — a question none of them can answer alone,
 * so each guessed, and the guesses had all drifted apart:
 *
 *     session-bar.tsx    [role="dialog"], [role="menu"], [data-state="open"]
 *     history-panel.tsx  the same three, written out again
 *     control-panel      those two, plus `event.defaultPrevented`
 *     inbox-panel.tsx    only `defaultPrevented`
 *     settings-page.tsx  nothing at all
 *
 * The bugs were the gaps between them. Settings guarded nothing, so Escape out of Persona's
 * "Name the fragment" dialog closed the dialog *and* the whole Settings page behind it, dumping
 * you back into the chat from a cancelled rename. And nothing in the list can see Settings —
 * it is a plain `div` at `z-30` — so ⌘F in the persona editor was swallowed by the session bar
 * underneath and opened a find bar over a conversation nobody could see.
 *
 * Two rules, in one place:
 *
 * **Radix first.** Anything portalled on top — a dialog, a menu, a popover — owns the key, and
 * that is a DOM question because those surfaces are not ours to register. This is the sniff the
 * five copies were each attempting.
 *
 * **Then depth, not mount order.** A surface declares how far forward it sits and is frontmost
 * when nothing deeper is mounted. Mount order would have been enough until the session bar
 * remounted on a conversation switch with Settings already open, and put itself on top of the
 * page covering it.
 *
 * Peers at the same depth are all considered frontmost. Two panels open at once is already a
 * layout the app avoids, and making them fight over Escape would be inventing a rule rather
 * than recording one.
 */

export const Layer = {
  /** Always-mounted chrome — the session bar, the composer. Anything can cover it. */
  Base: 0,
  /** Slides over the room but leaves it visible: history, inbox, the control panel. */
  Panel: 10,
  /** Takes the window: Settings, the file viewer. */
  Overlay: 20,
} as const;

/** Depth of every mounted surface. Module-level because "what is on screen" is not per-tree. */
const mounted = new Map<symbol, number>();

/** Radix marks its portalled surfaces; `[data-state="open"]` catches the ones without a role. */
const PORTALLED = '[role="dialog"], [role="alertdialog"], [role="menu"], [data-state="open"]';

function frontmost(depth: number): boolean {
  if (document.querySelector(PORTALLED)) return false;
  for (const other of mounted.values()) if (other > depth) return false;
  return true;
}

export interface LayerHandle {
  /**
   * Whether this surface should act on a global key. Call it *inside* the handler — it reads
   * what is on screen at the moment the key arrives, not at the moment the effect was bound.
   */
  frontmost: () => boolean;
}

/**
 * Register this surface while it is mounted, and get a way to ask whether it is on top.
 *
 *     const layer = useLayer(Layer.Overlay);
 *     …
 *     if (!layer.frontmost()) return;
 *
 * Pass `active: false` for a surface that is mounted but not showing — a panel rendered behind
 * a closed state should not count as covering anything.
 */
export function useLayer(depth: number, active = true): LayerHandle {
  const token = useRef<symbol | null>(null);
  token.current ??= Symbol("layer");

  useEffect(() => {
    const id = token.current;
    if (!id || !active) return;
    mounted.set(id, depth);
    return () => {
      mounted.delete(id);
    };
  }, [depth, active]);

  return useMemo(() => ({ frontmost: () => frontmost(depth) }), [depth]);
}
