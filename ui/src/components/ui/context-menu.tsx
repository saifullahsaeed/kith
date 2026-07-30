import { ContextMenu as Primitive } from "radix-ui";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Right-click menus, themed to match the rest of the room.
 *
 * A thin wrapper over Radix rather than a hand-rolled menu: the fiddly parts of a
 * context menu are the ones you don't see — positioning near a screen edge, focus
 * capture, type-ahead, Escape, and not opening two at once — and all of them are
 * already solved here.
 */

export const ContextMenu = Primitive.Root;
export const ContextMenuTrigger = Primitive.Trigger;

export function ContextMenuContent({ className, ...props }: ComponentProps<typeof Primitive.Content>) {
  return (
    <Primitive.Portal>
      <Primitive.Content
        className={cn(
          "animate-in fade-in-0 zoom-in-95 z-50 min-w-52 overflow-hidden rounded-xl border border-border/70 bg-popover/95 p-1 text-popover-foreground shadow-xl backdrop-blur-xl duration-100",
          className,
        )}
        {...props}
      />
    </Primitive.Portal>
  );
}

export function ContextMenuItem({
  className,
  icon,
  hint,
  danger = false,
  children,
  ...props
}: ComponentProps<typeof Primitive.Item> & {
  icon?: ReactNode;
  /** A keyboard shortcut or a value, shown greyed at the end. */
  hint?: string;
  danger?: boolean;
}) {
  return (
    <Primitive.Item
      className={cn(
        "flex cursor-default items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none",
        "data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-40",
        danger && "text-destructive data-[highlighted]:bg-destructive/10",
        className,
      )}
      {...props}
    >
      {icon ? <span className="flex size-4 shrink-0 items-center justify-center">{icon}</span> : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {hint ? (
        <span className="text-muted-foreground/70 shrink-0 font-mono text-[10px]">{hint}</span>
      ) : null}
    </Primitive.Item>
  );
}

export function ContextMenuSeparator({ className, ...props }: ComponentProps<typeof Primitive.Separator>) {
  return <Primitive.Separator className={cn("bg-border/60 -mx-1 my-1 h-px", className)} {...props} />;
}

export function ContextMenuLabel({ className, ...props }: ComponentProps<typeof Primitive.Label>) {
  return (
    <Primitive.Label
      className={cn("text-muted-foreground/70 truncate px-2.5 py-1.5 text-[11px]", className)}
      {...props}
    />
  );
}
