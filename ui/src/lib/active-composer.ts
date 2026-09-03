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
 * So the focused chat publishes its composer here and the drop reads it. A module-level holder
 * rather than a context or a store, deliberately: this is read once, inside an event handler,
 * by code that must not re-render when it changes — and there is exactly one focused chat, so
 * a single slot is the whole of the state.
 *
 * `null` is a real answer and the common one — no chat tab open, or the focused pane is the
 * board. The drop is still swallowed; it simply attaches to nothing, which is what should
 * happen to a file dropped on the settings page.
 */
let current: ComposerRuntime | null = null;

export function setActiveComposer(composer: ComposerRuntime | null): void {
  current = composer;
}

/** The focused chat's composer, or null. Read at drop time, never rendered against. */
export function activeComposer(): ComposerRuntime | null {
  return current;
}

/** Stand down, but only if this is still the one that claimed the slot.
 *
 * The guard matters on a focus change: React unmounts and mounts in an order that puts the new
 * pane's registration *before* the old pane's cleanup, so an unguarded clear would wipe the
 * composer that had just correctly claimed the slot and leave drops attaching to nothing. */
export function clearActiveComposer(composer: ComposerRuntime): void {
  if (current === composer) current = null;
}
