import { create } from "zustand";

import type { ComposerRuntime } from "@assistant-ui/react";

/**
 * The composer a dropped file belongs to.
 *
 * `DropZone` listens on the *window*, and it has to: the only drop target used to be the
 * composer shell, so a file let go over the conversation did nothing — worse than nothing,
 * because without a `preventDefault` the drop stays a navigation to a `file://` URL and the
 * desktop shell hands anything that is not the local app to `openExternally`. Dropping a PDF on
 * the chat opened it in Preview. So the listeners are global and swallowing the drop is the
 * point even when nothing will be attached.
 *
 * That worked while there was one composer in the app and `useComposerRuntime()` could find it
 * from anywhere. With a composer per chat pane there is no longer a single answer to "which
 * one", and the window-level listener sits above all of them where the hook cannot reach any.
 *
 * So the focused chat publishes its composer here and the drop reads it.
 *
 * `null` is a real answer and the common one — no chat tab open, the focused pane is the board,
 * or the focused chat is a background tab that is not mounted. The drop is still swallowed; it
 * simply attaches to nothing, which is what should happen to a file dropped on the settings
 * page.
 *
 * **A store, so the overlay can be honest about it.** It began as a module-level slot on the
 * grounds that a drop handler must not re-render when it changes — which is true of the *read*
 * and false of the *gate*. `DropZone` decided whether to arm by asking the layout tree whether
 * a chat tab existed anywhere, and a tab existing is not a composer being mounted: a pane
 * renders only its active tab, so one pane holding [chat, work] with work active satisfied the
 * tree and had no composer at all. The full-window overlay then promised "Drop to attach" and
 * the file went nowhere — precisely the outcome this module and `drop-zone` both exist to
 * prevent. The gate subscribes; the drop still reads without subscribing.
 */
type Slot = { composer: ComposerRuntime | null };

const slot = create<Slot>(() => ({ composer: null }));

/** Subscribe to whether anything can receive a dropped file. For rendering. */
export function useCanAttach(): boolean {
  return slot((state) => state.composer !== null);
}

export function setActiveComposer(composer: ComposerRuntime | null): void {
  slot.setState({ composer });
}

/** The focused chat's composer, or null. Read at drop time, never rendered against. */
export function activeComposer(): ComposerRuntime | null {
  return slot.getState().composer;
}

/** Stand down, but only if this is still the one that claimed the slot.
 *
 * The guard matters on a focus change: React unmounts and mounts in an order that puts the new
 * pane's registration *before* the old pane's cleanup, so an unguarded clear would wipe the
 * composer that had just correctly claimed the slot and leave drops attaching to nothing. */
export function clearActiveComposer(composer: ComposerRuntime): void {
  if (slot.getState().composer === composer) slot.setState({ composer: null });
}
