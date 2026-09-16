import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

/**
 * The conversation list's rail: the one column that is not part of the layout tree.
 *
 * `conversations` used to be a surface like any other, which meant the list could be dragged
 * into a split, stacked behind a Work tab, or closed — and the list is how you reach everything
 * else, so all three were ways to lose your way around the app with no obvious way back. It is
 * furniture, not a document. Furniture does not get a tab.
 *
 * So this sits *beside* `LayoutView` rather than inside it, and owns only what a rail owns:
 * how wide it is, whether it is showing, and the handle between it and the tree. What it holds
 * — `HistoryPanel` — does not know it moved.
 */

/** Where the rail's shape is remembered. Its own key: this is a fact about the window's
 *  furniture, and folding it into the layout tree's key would tie it to that key's version
 *  bumps — the rail would reset every time the tree's shape changed. */
const KEY = "kith-sidebar";

/** Narrowest the rail is worth showing. Inherited from the surface it replaces, where it was
 *  `HISTORY_WIDTH` — measured as the width a conversation title needs before it starts eliding
 *  mid-word. */
export const SIDEBAR_MIN = 240;
/** Widest it may be dragged. Past this the rail is competing with the thread rather than
 *  indexing it. */
export const SIDEBAR_MAX = 460;
export const SIDEBAR_DEFAULT = 260;

export type SidebarShape = { width: number; open: boolean };

/**
 * What the rail looks like on a fresh load.
 *
 * Reads whatever the last session wrote and decides how much of it to trust. A stored value
 * here is a convenience, never a correctness requirement: the rail has a perfectly good default
 * and anything unusable should fall back to it rather than propagate.
 */
export function readSidebar(): SidebarShape {
  let held: unknown = null;
  try {
    const raw = localStorage.getItem(KEY);
    held = raw ? JSON.parse(raw) : null;
  } catch {
    // Private mode, no storage, or something that is not JSON. All three mean "no preference".
    held = null;
  }

  /* An object and nothing else. An array passes `typeof === "object"` and would then read
   * `.width` as undefined — which lands on the default anyway, but by accident rather than on
   * purpose, and the next field added here would not be so lucky. */
  const shape =
    held && typeof held === "object" && !Array.isArray(held)
      ? (held as Partial<SidebarShape>)
      : null;

  /* Clamp rather than discard. A width can leave the range without anyone editing storage by
   * hand — drag the rail wide on a large display, open the same app on a laptop — and "I like it
   * wide" is intent worth keeping even when it meets this screen's ceiling. `Number.isFinite`
   * because `NaN` and `Infinity` are both numbers and neither is a width. */
  const width =
    typeof shape?.width === "number" && Number.isFinite(shape.width)
      ? clamp(shape.width)
      : SIDEBAR_DEFAULT;

  /* Hiding the rail sticks. The header toggle is the single way in and out now — the on-rail
   * close button is gone — so a collapse is a deliberate act against a control you can see, and
   * forgetting it every reload means reclaiming the same 260px every morning.
   *
   * `!== false` rather than a truthiness check: only a stored, explicit `false` closes it.
   * Missing, malformed, or a value from some future shape of this key all mean "no preference",
   * and the forgiving answer is the one where the way you reach every conversation is visible. */
  const open = shape?.open !== false;

  return { width, open };
}

function writeSidebar(shape: SidebarShape): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(shape));
  } catch {
    // Storage being unavailable is not a reason to fail a drag.
  }
}

/** The rail's shape, and the two ways it changes. Persisted on every settled change — a drag
 *  writes once on release, not on every mousemove. */
export function useSidebar() {
  const [shape, setShape] = useState<SidebarShape>(readSidebar);

  const setOpen = useCallback((open: boolean) => {
    setShape((was) => {
      const next = { ...was, open };
      writeSidebar(next);
      return next;
    });
  }, []);

  /** Live during a drag. `settle` is what reaches storage, so the key is not rewritten on
   *  every pointer event. */
  const setWidth = useCallback((width: number, settle = false) => {
    setShape((was) => {
      const next = { ...was, width: clamp(width) };
      if (settle) writeSidebar(next);
      return next;
    });
  }, []);

  return { ...shape, setOpen, setWidth, toggle: () => setOpen(!shape.open) };
}

export function clamp(width: number): number {
  return Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(width)));
}

export function Sidebar({
  open,
  width,
  onWidth,
  children,
}: {
  open: boolean;
  width: number;
  /** `settle` marks the end of a drag — see `useSidebar().setWidth`. */
  onWidth: (width: number, settle?: boolean) => void;
  children: React.ReactNode;
}) {
  const rail = useRef<HTMLDivElement | null>(null);
  const [dragging, setDragging] = useState(false);

  /* Pointer capture on the handle rather than window listeners added per drag.
   *
   * The pointer stays with the handle even when it outruns a 4px target, which is what makes a
   * fast drag not break halfway across the window — and it releases itself if the gesture is
   * cancelled, so there is no listener to leak. */
  const onPointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }, []);

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      const left = rail.current?.getBoundingClientRect().left ?? 0;
      onWidth(event.clientX - left);
    },
    [dragging, onWidth],
  );

  const endDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      setDragging(false);
      const left = rail.current?.getBoundingClientRect().left ?? 0;
      onWidth(event.clientX - left, true);
    },
    [dragging, onWidth],
  );

  /* While a drag is live the whole window gets the resize cursor and stops selecting text.
   * Without it the cursor flickers back to a caret the moment the pointer leaves the 4px
   * handle, which on a fast drag is most of the gesture. */
  useEffect(() => {
    if (!dragging) return;
    const style = document.body.style;
    const heldCursor = style.cursor;
    const heldSelect = style.userSelect;
    style.cursor = "col-resize";
    style.userSelect = "none";
    return () => {
      style.cursor = heldCursor;
      style.userSelect = heldSelect;
    };
  }, [dragging]);

  if (!open) return null;

  return (
    <div
      ref={rail}
      className="relative flex min-h-0 shrink-0 flex-col"
      style={{ width }}
      data-sidebar=""
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>

      {/* The handle. 5px of target over a 1px rule — the rule is the border you see, the target
          is the one you can actually hit. */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the conversation list"
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={(event) => {
          // Keyboard resizing, because a drag handle that only takes a pointer is a control
          // half the people who need it most cannot use.
          if (event.key === "ArrowLeft") onWidth(width - 16, true);
          else if (event.key === "ArrowRight") onWidth(width + 16, true);
          else return;
          event.preventDefault();
        }}
        className={cn(
          "absolute inset-y-0 -right-[2px] z-20 w-[5px] cursor-col-resize",
          "after:bg-border after:absolute after:inset-y-0 after:right-[2px] after:w-px after:transition-colors",
          "hover:after:bg-kith/50 focus-visible:after:bg-kith focus-visible:outline-none",
          dragging && "after:bg-kith/70",
        )}
      />

    </div>
  );
}
