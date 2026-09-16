import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, Maximize2, Minimize2, X, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The column a tab carries with it.
 *
 * Work opened from a chat is *about* that chat, so it belongs with it — not as a sibling in the
 * pane's tab strip, where it sits at the same level as the conversation and has to be told
 * apart from every other Work tab by reading its title. Binding the surface to the conversation
 * settled which chat it is about; this settles where it goes.
 *
 * **Deliberately not a second tiling tree.** A companion column stacks, and that is all it
 * does: no nesting, no drag-to-split, no tabs of its own. The outer tree already answers "two
 * things side by side" and answering it twice, differently, in two places, is how a window
 * manager gets a class of bug nobody can reproduce. What this adds is one axis — panels beside
 * a host, in order — which is the shape the thing actually needs.
 */

const KEY = "kith-companion-width";
const MIN = 280;
const MAX = 720;
const DEFAULT = 400;

function readWidth(): number {
  try {
    const held = Number(localStorage.getItem(KEY));
    return Number.isFinite(held) && held > 0 ? clampWidth(held) : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function clampWidth(width: number): number {
  return Math.min(MAX, Math.max(MIN, Math.round(width)));
}

export type CompanionPanel = {
  /** The companion's tab key — its identity, and what `detach` takes. */
  key: string;
  title: string;
  icon: LucideIcon;
  body: React.ReactNode;
};

export function Companions({
  host,
  panels,
  onClose,
}: {
  host: React.ReactNode;
  panels: CompanionPanel[];
  onClose: (key: string) => void;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(readWidth);
  const [dragging, setDragging] = useState(false);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  /* One panel taking the whole tab. Local and unstored on purpose: it is a fact about what you
   * are doing right now, the way zoom is for a pane, and a tab that came back from storage
   * already filled by a panel would read as a tab that had lost its conversation. */
  const [maximized, setMaximized] = useState<string | null>(null);

  const settle = useCallback((next: number) => {
    try {
      localStorage.setItem(KEY, String(next));
    } catch {
      /* a width is a convenience, not the work */
    }
  }, []);

  /* Pointer capture rather than window listeners, for the reason the rail gives: the pointer
   * stays with the handle when it outruns a 5px target, and a cancelled gesture releases
   * itself instead of leaving a listener behind. */
  const onMove = useCallback(
    (event: React.PointerEvent) => {
      if (!dragging) return;
      const right = box.current?.getBoundingClientRect().right ?? 0;
      setWidth(clampWidth(right - event.clientX));
    },
    [dragging],
  );

  const end = useCallback(
    (event: React.PointerEvent) => {
      if (!dragging) return;
      setDragging(false);
      const right = box.current?.getBoundingClientRect().right ?? 0;
      const next = clampWidth(right - event.clientX);
      setWidth(next);
      settle(next);
    },
    [dragging, settle],
  );

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

  // No companions is not a layout: the host takes the whole tab rather than sitting in a
  // one-sided split with an empty column beside it.
  if (!panels.length) return <>{host}</>;

  const full = panels.find((one) => one.key === maximized) ?? null;

  return (
    <div ref={box} className="flex h-full min-h-0 w-full">
      {/* Hidden rather than unmounted while a panel is full-screen. Unmounting the chat would
          throw away its runtime and its scroll the way switching tabs used to — and leaving a
          full-screen panel would then be an open, not a return. */}
      <div className={cn("min-w-0 flex-1", full && "hidden")}>{host}</div>

      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the panel column"
        tabIndex={0}
        onPointerDown={(event) => {
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          setDragging(true);
        }}
        onPointerMove={onMove}
        onPointerUp={end}
        onPointerCancel={end}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") setWidth((was) => clampWidth(was + 16));
          else if (event.key === "ArrowRight") setWidth((was) => clampWidth(was - 16));
          else return;
          event.preventDefault();
          settle(width);
        }}
        className={cn(
          "relative z-10 w-[5px] shrink-0 cursor-col-resize",
          full && "hidden",
          "after:bg-border after:absolute after:inset-y-0 after:left-[2px] after:w-px after:transition-colors",
          "hover:after:bg-kith/50 focus-visible:after:bg-kith focus-visible:outline-none",
          dragging && "after:bg-kith/70",
        )}
      />

      <div
        className={cn(
          "flex min-h-0 flex-col gap-1.5 overflow-hidden p-1.5",
          full ? "w-full flex-1" : "shrink-0",
        )}
        style={full ? undefined : { width }}
      >
        {(full ? [full] : panels).map((panel) => {
          const shut = collapsed.has(panel.key) && !full;
          const Mark = panel.icon;
          return (
            <section
              key={panel.key}
              className={cn(
                "border-border/60 bg-card/40 flex min-h-0 flex-col overflow-hidden rounded-xl border",
                // A collapsed panel is its own header and nothing else, so the ones still open
                // take the height it gives up rather than everything staying its old size.
                shut ? "shrink-0" : "flex-1",
              )}
            >
              <header className="border-border/60 flex h-8 shrink-0 items-center gap-2 border-b px-2.5">
                <Mark className="text-muted-foreground size-3.5 shrink-0" />
                <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium">
                  {panel.title}
                </span>
                {full ? null : (
                <button
                  type="button"
                  aria-label={shut ? `Show ${panel.title}` : `Collapse ${panel.title}`}
                  aria-expanded={!shut}
                  onClick={() =>
                    setCollapsed((was) => {
                      const next = new Set(was);
                      if (!next.delete(panel.key)) next.add(panel.key);
                      return next;
                    })
                  }
                  className="text-muted-foreground hover:text-foreground hover:bg-accent/60 focus-visible:ring-kith/60 flex size-5 shrink-0 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
                >
                  <ChevronDown className={cn("size-3.5 transition-transform", shut && "-rotate-90")} />
                </button>
                )}
                <button
                  type="button"
                  aria-label={full ? `Restore ${panel.title}` : `Expand ${panel.title}`}
                  aria-pressed={!!full}
                  onClick={() =>
                    setMaximized((was) => {
                      const next = was === panel.key ? null : panel.key;
                      // Expanding something you had collapsed should show it, not fill the tab
                      // with a header.
                      if (next) setCollapsed((held) => {
                        const kept = new Set(held);
                        kept.delete(panel.key);
                        return kept;
                      });
                      return next;
                    })
                  }
                  className="text-muted-foreground hover:text-foreground hover:bg-accent/60 focus-visible:ring-kith/60 flex size-5 shrink-0 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
                >
                  {full ? <Minimize2 className="size-3" /> : <Maximize2 className="size-3" />}
                </button>
                <button
                  type="button"
                  aria-label={`Close ${panel.title}`}
                  onClick={() => {
                    if (maximized === panel.key) setMaximized(null);
                    onClose(panel.key);
                  }}
                  className="text-muted-foreground hover:text-foreground hover:bg-accent/60 focus-visible:ring-kith/60 flex size-5 shrink-0 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
                >
                  <X className="size-3.5" />
                </button>
              </header>

              {shut ? null : <div className="min-h-0 flex-1 overflow-hidden">{panel.body}</div>}
            </section>
          );
        })}
      </div>
    </div>
  );
}
