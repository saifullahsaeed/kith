import { useEffect, useState } from "react";
import { useComposerRuntime } from "@assistant-ui/react";
import { Paperclip } from "lucide-react";

/**
 * Dropping a file anywhere in the window attaches it.
 *
 * The only drop target used to be the composer shell — a box about ninety pixels tall at the
 * bottom of the column — so a file let go over the conversation, which is the whole rest of the
 * window and the obvious place to aim, did nothing at all.
 *
 * Except it was worse than nothing. Without a `preventDefault` the drop stays a navigation to
 * a `file://` URL, and the desktop shell hardens navigation by handing anything that is not
 * the local app to `openExternally` — so dropping a PDF on the chat opened it in Preview and
 * left the composer empty. Two failures that look identical from the outside: the file went
 * somewhere, and it was not to Kith.
 *
 * So the listeners are on the window, and they are attached whether or not this surface is
 * the one on screen. Swallowing the drop is the point even when nothing will be attached: a
 * file let go over the settings page should do nothing, which is not the same as opening it in
 * another application.
 */
export function DropZone({ enabled }: { enabled: boolean }) {
  const composer = useComposerRuntime();
  // How many files are hovering. Zero means no drag — one piece of state rather than a
  // boolean and a count that can disagree.
  const [hovering, setHovering] = useState(0);

  useEffect(() => {
    /* `dragenter` and `dragleave` fire for every element the pointer crosses on its way in,
       so treating a leave as "the drag is over" flickers the overlay off and on again over
       every row of the thread. Counting the depth and only clearing at zero is what makes it
       hold steady. */
    let depth = 0;

    // Text being dragged out of a message is a drag too, and it is not an attachment.
    const carriesFiles = (event: DragEvent) =>
      Array.from(event.dataTransfer?.types ?? []).includes("Files");

    const clear = () => {
      depth = 0;
      setHovering(0);
    };

    const onEnter = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      depth += 1;
      if (enabled) setHovering(countFiles(event));
    };

    const onOver = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      // The line that stops the window navigating away from the app. Unconditional, because
      // a stray drop must never take you somewhere else even on a surface that cannot accept
      // it — and a `dragover` that is not prevented never produces a `drop` at all.
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = enabled ? "copy" : "none";
    };

    const onLeave = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setHovering(0);
    };

    const onDrop = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      clear();
      if (!enabled) return;
      for (const file of Array.from(event.dataTransfer?.files ?? [])) {
        void composer.addAttachment(file);
      }
    };

    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    // Released outside the window, or cancelled with Escape. Without these the overlay can be
    // left on screen over an app you can no longer see, with nothing to click to dismiss it.
    window.addEventListener("dragend", clear);
    window.addEventListener("blur", clear);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("dragend", clear);
      window.removeEventListener("blur", clear);
    };
  }, [enabled, composer]);

  if (!hovering) return null;

  return (
    // `pointer-events-none` so the overlay is scenery: every drag event goes on reaching the
    // window listeners above rather than being caught by a div that appeared mid-gesture.
    <div
      className="fade-in animate-in pointer-events-none fixed inset-0 z-[70] flex items-center justify-center duration-150"
      aria-hidden
    >
      <div className="bg-background/75 absolute inset-0 backdrop-blur-sm" />
      {/* The frame, inset from the edges, is what says "the whole window" rather than "some
          region of it" — which was the actual thing wrong with a highlight on one small box. */}
      <div className="border-kith/45 absolute inset-3 rounded-2xl border-2 border-dashed" />
      <div className="zoom-in-95 animate-in relative flex flex-col items-center gap-4 duration-200">
        <span className="kith-drop-ring flex size-24 items-center justify-center rounded-3xl">
          <Paperclip className="text-kith size-8" strokeWidth={1.75} />
        </span>
        <div className="space-y-1 text-center">
          <p className="text-lg font-medium">Drop to attach</p>
          <p className="text-muted-foreground text-sm">
            {hovering === 1 ? "One file" : `${hovering} files`} — he can open anything
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * How many files are on their way in.
 *
 * `dataTransfer.files` is deliberately empty until the drop lands, so the count has to come
 * off `items` — which the browser does expose mid-drag, without the names. Falls back to one
 * rather than zero: a drag carrying files that will not say how many is still a drag, and an
 * overlay reading "0 files" would be worse than a round number.
 */
function countFiles(event: DragEvent): number {
  const items = event.dataTransfer?.items;
  if (!items) return 1;
  let files = 0;
  for (const item of Array.from(items)) if (item.kind === "file") files += 1;
  return files || 1;
}
