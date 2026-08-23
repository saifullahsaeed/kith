import { useEffect, useRef, useState } from "react";
import { Clipboard, Copy, Scissors, TextSelect } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Right-click, styled like the rest of the app instead of like the operating system.
 *
 * Electron gives a `BrowserWindow` no context menu of its own — nothing shows on right-click
 * unless something builds one. The first version of this built a real native one
 * (`Menu.popup()` in the main process), and it worked, but a native menu is OS chrome: it
 * cannot take a class name, so it looked like a different, plainer app landed on top of this
 * one's own rounded corners and warm-paper colours for the one interaction that happened to
 * route through Electron. This is the same menu, in HTML, so it looks like the rest of Kith
 * and can follow the theme.
 *
 * `onMouseDown` on the menu itself calls `preventDefault` — the same trick
 * `SelectionToolbarPrimitive.Root` already uses for its own floating button — so clicking an
 * item never steals focus from whatever was selected or being edited first. Without it, the
 * click that opens "Copy" would already have cleared the very selection it's supposed to act
 * on by the time the handler runs — and it's *because* focus survives that
 * `execCommand("copy"/"cut")` below can be trusted to act on the right thing without this
 * component having to track what that thing was.
 */

type MenuState = {
  x: number;
  y: number;
  target: HTMLInputElement | HTMLTextAreaElement | null;
  hasSelection: boolean;
  editable: boolean;
};

const EDITABLE_SELECTOR = "input, textarea, [contenteditable='true']";

function editableAncestor(el: EventTarget | null): HTMLInputElement | HTMLTextAreaElement | null {
  if (!(el instanceof HTMLElement)) return null;
  const found = el.closest(EDITABLE_SELECTOR);
  return found instanceof HTMLInputElement || found instanceof HTMLTextAreaElement ? found : null;
}

/**
 * Whether anything is actually selected, either in the page or inside a focused field.
 *
 * `window.getSelection()` answers this for ordinary text and `[contenteditable]`, but an
 * `<input>`/`<textarea>`'s own selection lives entirely in `selectionStart`/`selectionEnd` —
 * the Selection API cannot see inside a form control at all. Checking only the former is why
 * "Copy" would silently never appear for text selected inside the message composer.
 */
function hasRealSelection(target: HTMLInputElement | HTMLTextAreaElement | null): boolean {
  if (target && typeof target.selectionStart === "number") {
    return target.selectionStart !== target.selectionEnd;
  }
  return !(window.getSelection()?.isCollapsed ?? true);
}

export function ContextMenu() {
  const [menu, setMenu] = useState<MenuState | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onContextMenu = (event: MouseEvent) => {
      /* Somewhere below already claimed this click.
       *
       * A Radix `ContextMenuTrigger` calls `preventDefault()` on its own `onContextMenu`, and
       * React 19 delegates to `#root` — which is *below* `document`, so that handler has already
       * run and marked the event by the time this one does. Without this check both menus open at
       * the same coordinates: Radix's, and this one because the card contains an editable field or
       * because there is a selection somewhere on the page. This one paints on top (`z-index: 100`
       * against Radix's `z-50`) and is inert, because Radix's modal layer puts `pointer-events:
       * none` on the body — so the visible menu is the one you cannot click.
       *
       * The docstring above already promises this does not happen. It is the first statement in
       * the handler so that nothing else runs on an event that was never ours. */
      if (event.defaultPrevented) return;
      const target = editableAncestor(event.target);
      const editable = target !== null;
      const selected = hasRealSelection(target);
      // Nothing useful to offer: not a selection, not somewhere you could paste into. Leave it
      // alone rather than showing a menu whose only option is greyed out.
      if (!selected && !editable) return;
      event.preventDefault();
      setMenu({ x: event.clientX, y: event.clientY, target, hasSelection: selected, editable });
    };
    const close = (event: Event) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      setMenu(null);
    };
    const closeOnKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    const closeAll = () => setMenu(null);
    document.addEventListener("contextmenu", onContextMenu);
    document.addEventListener("mousedown", close);
    document.addEventListener("scroll", close, true);
    document.addEventListener("keydown", closeOnKey);
    window.addEventListener("blur", closeAll);
    return () => {
      document.removeEventListener("contextmenu", onContextMenu);
      document.removeEventListener("mousedown", close);
      document.removeEventListener("scroll", close, true);
      document.removeEventListener("keydown", closeOnKey);
      window.removeEventListener("blur", closeAll);
    };
  }, []);

  if (!menu) return null;

  const items: { label: string; icon: typeof Copy; onClick: () => void }[] = [];

  if (menu.hasSelection) {
    items.push({ label: "Copy", icon: Copy, onClick: () => document.execCommand("copy") });
  }
  if (menu.editable && menu.hasSelection) {
    items.push({ label: "Cut", icon: Scissors, onClick: () => document.execCommand("cut") });
  }
  if (menu.editable) {
    items.push({
      label: "Paste",
      icon: Clipboard,
      onClick: () => {
        // Best-effort: clipboard-read can be denied outright, and unlike copy/cut there is no
        // fallback for it — `execCommand("paste")` is blocked in Chromium for the same reason
        // reading the clipboard from a page normally is. Cmd/Ctrl+V still works either way.
        navigator.clipboard
          .readText()
          .then((text) => {
            menu.target?.focus();
            document.execCommand("insertText", false, text);
          })
          .catch(() => {});
      },
    });
  }
  items.push({
    label: "Select All",
    icon: TextSelect,
    onClick: () => {
      if (menu.target) {
        menu.target.focus();
        menu.target.select();
      } else {
        document.execCommand("selectAll");
      }
    },
  });

  // Clamped rather than let it run off-screen — a right-click near the window's edge is the
  // common case, not the exception, on a narrower panel width.
  const width = 168;
  const height = items.length * 32 + 8;
  const left = Math.min(menu.x, window.innerWidth - width - 8);
  const top = Math.min(menu.y, window.innerHeight - height - 8);

  return (
    <div
      ref={menuRef}
      role="menu"
      style={{ position: "fixed", left, top, zIndex: 100, width }}
      className="animate-in fade-in-0 zoom-in-95 rounded-lg border bg-popover p-1 shadow-xl duration-100 ease-[cubic-bezier(0.32,0.72,0,1)]"
      onMouseDown={(event) => event.preventDefault()}
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          onClick={() => {
            item.onClick();
            setMenu(null);
          }}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm",
            "hover:bg-accent hover:text-accent-foreground",
          )}
        >
          <item.icon className="text-muted-foreground size-3.5 shrink-0" />
          {item.label}
        </button>
      ))}
    </div>
  );
}
