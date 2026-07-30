import { useCallback, useEffect, useState } from "react";
import { MessageSquare, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ItemMenu } from "@/components/ui/item-menu";
import { useConfirm } from "@/components/ui/confirm";
import {
  deleteConversation,
  fetchConversations,
  renameConversation,
  type ConversationSummary,
} from "@/lib/backend";
import { openOnHost } from "@/lib/files";
import { cn } from "@/lib/utils";

/**
 * Every conversation you have had with him, and the way back into one.
 *
 * Chat had no history at all: the messages lived in React state and a reload was the end
 * of them. So this is not a convenience — it is the difference between a conversation being
 * a thing you had and a thing you have.
 *
 * Resuming loads the stored messages and reattaches the same conversation id, so the turn
 * you send next lands in the same transcript and on the same OpenRouter session — the cache
 * it warmed is still warm.
 *
 * Right-click gives Reveal, because each conversation is a real file on disk and the point
 * of keeping them in plain text is that you can go and look.
 */
export function HistoryPanel({
  activeId,
  onOpen,
  onNew,
  onClose,
}: {
  activeId: string;
  onOpen: (id: string) => void;
  onNew: () => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [storage, setStorage] = useState<{ files: number; bytes: number } | null>(null);
  const confirm = useConfirm();

  const load = useCallback(() => {
    fetchConversations(100)
      .then((data) => {
        setItems(data.conversations);
        setStorage(data.storage);
      })
      .catch(() => {});
  }, []);

  useEffect(load, [load]);
  // Reload when the active conversation changes: a new one has just been created and a
  // resumed one has just moved to the top.
  useEffect(load, [activeId, load]);

  return (
    <aside className="flex h-full min-h-0 w-full flex-col">
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
        <MessageSquare className="text-muted-foreground size-4" />
        <span className="text-sm font-medium">Conversations</span>
        <div className="flex-1" />
        <Button size="sm" variant="outline" onClick={onNew}>
          <Plus className="size-3.5" />
          New
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {items.length === 0 ? (
          <p className="text-muted-foreground p-4 text-center text-sm">
            Nothing yet. Say something to him and it will be kept here.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {items.map((item) => (
              <li key={item.id}>
                <ItemMenu
                  title={item.title}
                  copy={item.title}
                  actions={[
                    {
                      label: "Reveal transcript",
                      hint: "in Finder",
                      onSelect: () => void openOnHost(item.transcript, true),
                    },
                    {
                      label: "Rename",
                      onSelect: () => {
                        const next = window.prompt("Rename this conversation", item.title);
                        if (next?.trim()) void renameConversation(item.id, next.trim()).then(load);
                      },
                    },
                  ]}
                  deleteLabel="Remove from list"
                  onDelete={async () => {
                    // The file stays. Tidying a list and destroying the only record of an
                    // afternoon are not the same act, so they are not the same click.
                    const ok = await confirm({
                      title: "Remove this conversation?",
                      subject: item.title,
                      description: "It leaves this list. The transcript file stays in his folder.",
                      confirmLabel: "Remove",
                    });
                    if (ok) void deleteConversation(item.id, false).then(load);
                  }}
                >
                  <button
                    type="button"
                    onClick={() => onOpen(item.id)}
                    className={cn(
                      "hover:bg-accent/60 flex w-full flex-col items-start gap-0.5 rounded-lg px-2.5 py-2 text-left transition-colors",
                      item.id === activeId && "bg-kith-soft/50",
                    )}
                  >
                    <span className="w-full truncate text-sm">{item.title}</span>
                    <span className="text-muted-foreground/60 font-mono text-[10px] tabular-nums">
                      {item.messages} msg · {when(item.updatedAt)}
                    </span>
                  </button>
                </ItemMenu>
              </li>
            ))}
          </ul>
        )}
      </div>

      {storage ? (
        <div className="text-muted-foreground/60 border-t border-border/60 px-4 py-2 font-mono text-[10px] tabular-nums">
          {storage.files} transcript{storage.files === 1 ? "" : "s"} · {bytes(storage.bytes)} on
          disk
        </div>
      ) : null}

      <button
        type="button"
        onClick={onClose}
        className="text-muted-foreground/60 hover:text-foreground border-t border-border/60 px-4 py-1.5 text-[11px]"
      >
        Hide
      </button>
    </aside>
  );
}

function when(iso: string): string {
  try {
    const at = new Date(iso);
    const today = new Date();
    const sameDay = at.toDateString() === today.toDateString();
    return sameDay
      ? at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
      : at.toLocaleDateString([], { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

function bytes(count: number): string {
  if (count < 1024) return `${count} B`;
  if (count < 1024 * 1024) return `${(count / 1024).toFixed(0)} KB`;
  return `${(count / 1024 / 1024).toFixed(1)} MB`;
}
