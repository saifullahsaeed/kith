/**
 * A small control that sits on top of something rather than beside it.
 *
 * Shared by the two surfaces in a reply that are pictures rather than text — the diagram and the
 * canvas — because both have the same problem: the controls belong to the drawing, so they sit
 * on it rather than in a bar above it. The parent decides how loud they are; this is only the
 * button.
 *
 * Backdrop-blurred and half-opaque on purpose. A solid button over a drawing hides part of it;
 * a transparent one is unreadable against whatever happens to be underneath.
 */
import type { ReactNode } from "react";

/** Where a diagram's buttons sit, and how loud: on screen at 70% all the time, full on hover or
 *  focus. Hidden-until-hover was tidier and read as "diagrams have no preview option any more" —
 *  a control you have to know to hover for is a control people report as missing. Shared by the
 *  still and the animated diagram, which must not import each other. */
export const DIAGRAM_TOOLBAR =
  "absolute end-2 top-2 flex gap-1 opacity-70 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100";

export function OverlayButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="border-border/60 bg-card/80 text-muted-foreground hover:text-foreground flex size-7 items-center justify-center rounded-md border backdrop-blur-sm transition-colors"
    >
      {children}
    </button>
  );
}
