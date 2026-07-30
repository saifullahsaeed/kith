import { useCallback, useEffect, useState } from "react";
import { MessageSquare, Plus, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ItemMenu } from "@/components/ui/item-menu";
import { useConfirm } from "@/components/ui/confirm";
import {
  deleteConversation,
  fetchConversations,
  renameConversation,
  searchConversations,
  type ConversationSummary,
  type TranscriptHit,
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
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<TranscriptHit[] | null>(null);
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

  /* Searching the transcripts themselves.
   *
   * Debounced, because every keystroke otherwise reads every transcript on disk — cheap at
   * this scale but pointless work, and "sad" on the way to "sadeef" is not a search anyone
   * asked for. Two characters minimum for the same reason: one letter matches everything
   * and tells you nothing. */
  useEffect(() => {
    const text = query.trim();
    if (text.length < 2) {
      setHits(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      void searchConversations(text).then((found) => {
        if (!cancelled) setHits(found);
      });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

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

      <div className="border-border/60 relative border-b px-2.5 py-2">
        <Search className="text-muted-foreground/50 pointer-events-none absolute top-1/2 left-4.5 size-3.5 -translate-y-1/2" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => event.key === "Escape" && setQuery("")}
          placeholder="Search everything said…"
          aria-label="Search conversations"
          className="border-border/60 bg-card/40 focus-visible:border-ring w-full rounded-lg border py-1.5 pr-7 pl-8 text-xs outline-none"
        />
        {query ? (
          <button
            type="button"
            onClick={() => setQuery("")}
            aria-label="Clear the search"
            className="text-muted-foreground/50 hover:text-foreground absolute top-1/2 right-4 -translate-y-1/2"
          >
            <X className="size-3.5" />
          </button>
        ) : null}
      </div>

      {hits !== null ? (
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {hits.length === 0 ? (
            <p className="text-muted-foreground p-4 text-center text-xs">
              Nothing said matches that.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {hits.map((hit) => (
                <li key={`${hit.conversationId}-${hit.at}`}>
                  <button
                    type="button"
                    onClick={() => onOpen(hit.conversationId)}
                    className={cn(
                      "hover:bg-accent/60 flex w-full flex-col items-start gap-1 rounded-lg px-2.5 py-2 text-left transition-colors",
                      hit.conversationId === activeId && "bg-kith-soft/50",
                    )}
                  >
                    <span className="flex w-full items-baseline gap-1.5">
                      <span className="min-w-0 flex-1 truncate text-xs font-medium">
                        {hit.title}
                      </span>
                      {/* Who said it, because "did I ask for that or did he offer it" is
                          most of why you are looking. */}
                      <span className="text-muted-foreground/50 shrink-0 text-[10px]">
                        {hit.role === "user" ? "you" : "him"}
                      </span>
                    </span>
                    <span className="text-muted-foreground/80 line-clamp-2 text-[11px] leading-snug">
                      {hit.snippet}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
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
                          if (next?.trim())
                            void renameConversation(item.id, next.trim()).then(load);
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
                        description:
                          "It leaves this list. The transcript file stays in his folder.",
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
      )}

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
