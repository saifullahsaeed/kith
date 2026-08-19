/**
 * A small control that sits on top of something rather than beside it.
 *
 * Shared by the two surfaces in a reply that are pictures rather than text — the diagram and the
 * canvas — because both have the same problem: the controls belong to the drawing, so a row of
 * buttons parked permanently above every one of them is exactly the clutter these components
 * exist to remove. The parent reveals them on hover or focus; this is only the button.
 *
 * Backdrop-blurred and half-opaque on purpose. A solid button over a drawing hides part of it;
 * a transparent one is unreadable against whatever happens to be underneath.
 */
import type { ReactNode } from "react";

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
