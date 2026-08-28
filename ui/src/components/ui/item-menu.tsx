import { Copy, Hash, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { copyText } from "@/lib/files";

export interface ItemAction {
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  /** Shown greyed at the end — a shortcut, or what the action will produce. */
  hint?: string;
}

/**
 * Right-click on anything in the control panel.
 *
 * One wrapper rather than a menu per page. Thirteen kinds of thing live in here —
 * memories, notes, tasks, people, tools — and they differ in what they *are*, not in
 * how you want to reach them: name it, copy it, do the two or three things specific to
 * it, delete it. Encoding that once means a new page gets the behaviour by wrapping its
 * card, and the menus can't drift apart.
 *
 * Copy and Delete are supplied here because every one of them wants both, and a page
 * that had to remember them would eventually be a page that forgot.
 */
export function ItemMenu({
  title,
  copy,
  copyId,
  actions = [],
  onDelete,
  deleteLabel = "Delete",
  children,
  className,
}: {
  /** What this thing is, shown at the top so the menu is unambiguous when rows are dense. */
  title?: string;
  /** Text to put on the clipboard. Omit to leave Copy out. */
  copy?: string;
  /**
   * The thing's own identifier, when it has one worth taking away.
   *
   * Beside "Copy text" rather than among the actions above, because the two are the same
   * gesture on different halves of the row and separating them would mean looking in two
   * places for "copy". A conversation's id is the name of its transcript file, what the API
   * addresses it by, and the only way to point Kith at one particular afternoon — none of
   * which was reachable without opening the folder and reading a filename.
   */
  copyId?: string;
  actions?: ItemAction[];
  /** Omit for anything that can't be deleted from here. */
  onDelete?: () => void;
  deleteLabel?: string;
  children: ReactNode;
  className?: string;
}) {
  const hasBody = Boolean(copy) || Boolean(copyId) || actions.length > 0 || Boolean(onDelete);
  // Nothing to offer means no menu at all — an empty panel on right-click is worse
  // than the browser's own.
  if (!hasBody) return <>{children}</>;

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild className={className}>
        {children}
      </ContextMenuTrigger>
      <ContextMenuContent>
        {title ? <ContextMenuLabel>{title}</ContextMenuLabel> : null}

        {actions.map((action) => (
          <ContextMenuItem
            key={action.label}
            icon={action.icon}
            hint={action.hint}
            danger={action.danger}
            onSelect={action.onSelect}
          >
            {action.label}
          </ContextMenuItem>
        ))}

        {copy || copyId ? (
          <>
            {actions.length > 0 ? <ContextMenuSeparator /> : null}
            {copy ? (
              <ContextMenuItem
                icon={<Copy className="size-3.5" />}
                onSelect={() => void copyText(copy)}
              >
                Copy text
              </ContextMenuItem>
            ) : null}
            {copyId ? (
              <ContextMenuItem
                icon={<Hash className="size-3.5" />}
                // The id itself as the hint, because it is short enough to read and seeing it
                // is often the whole reason for reaching for this.
                hint={copyId}
                onSelect={() => void copyText(copyId)}
              >
                Copy id
              </ContextMenuItem>
            ) : null}
          </>
        ) : null}

        {onDelete ? (
          <>
            <ContextMenuSeparator />
            <ContextMenuItem icon={<Trash2 className="size-3.5" />} danger onSelect={onDelete}>
              {deleteLabel}
            </ContextMenuItem>
          </>
        ) : null}
      </ContextMenuContent>
    </ContextMenu>
  );
}
