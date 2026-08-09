import { useCallback, useEffect, useMemo, useState } from "react";
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
import { fetchBrain, type Project } from "@/lib/backend/brain";
import { dayLabel, time } from "@/lib/dates";
import { openOnHost } from "@/lib/files";
import { cn } from "@/lib/utils";

/** How many to ask for at first, and the most the server will hand over in one call
 *  (`/api/conversations` clamps to 500). Past that, search is the way in — a list of a
 *  thousand titles is not something anyone reads to the end. */
const PAGE = 100;
const MAX = 500;

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
 *
 * Grouped by project. It was flat first — seventeen hundred rows reading "hi · 2 msg · 2:28 AM"
 * with nothing saying which day any belonged to — then grouped by day, which fixed the scanning
 * but not the finding: with two projects running at once, every day interleaves sessions from
 * both, so the one axis that told them apart was the one not in use. The day a session last moved
 * is on its own row now, which is where it was always more useful than in a heading.
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
  const [limit, setLimit] = useState(PAGE);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<TranscriptHit[] | null>(null);
  const confirm = useConfirm();

  const load = useCallback(() => {
    fetchConversations(limit)
      .then((data) => {
        setItems(data.conversations);
        setStorage(data.storage);
      })
      .catch(() => {});
  }, [limit]);

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

  /* Escape gets you out — of the search first, then of the panel.
   *
   * Guarded on anything modal being open, because a confirm dialog and a context menu both
   * live inside this panel and both close on Escape: without the guard, cancelling "remove
   * this conversation?" also closed the list you were tidying. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (document.querySelector('[role="dialog"], [role="menu"], [data-state="open"]')) return;
      if (query) setQuery("");
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [query, onClose]);

  // Grouped in the order the server sent them (most recent first), never re-sorted: the
  // listing's order is the answer to "what was I just doing", and a client that sorts it
  // again is a client that can disagree with it. That order carries through to the groups —
  // the project you touched last ends up on top without anything sorting them.
  const [projects, setProjects] = useState<Project[]>([]);
  // Names and statuses for the group headings. Reloaded with the list, because he starts
  // projects himself mid-conversation, so a name can appear between one open and the next.
  useEffect(() => {
    fetchBrain()
      .then((brain) => setProjects(brain.projects))
      .catch(() => {});
  }, [items]);
  const groups = useMemo(() => groupByProject(items, projects), [items, projects]);
  // A full page back means there are almost certainly more behind it. The alternative was
  // showing 100 of seventeen hundred with nothing on screen saying so, which is a silent
  // truncation dressed as a complete list.
  const more = items.length >= limit && limit < MAX;
  const capped = items.length >= MAX;

  return (
    <aside className="bg-sidebar/40 flex h-full min-h-0 w-full flex-col backdrop-blur-md">
      <div className="border-border/60 flex items-center gap-2 border-b px-4 py-2.5">
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
          placeholder="Search everything said…"
          aria-label="Search conversations"
          className="border-border/60 bg-card/60 focus-visible:border-ring w-full rounded-lg border py-1.5 pr-7 pl-8 text-xs outline-none"
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
        <div className="kith-fade-bottom min-h-0 flex-1 overflow-y-auto p-2">
          {hits.length === 0 ? (
            <p className="text-muted-foreground p-4 text-center text-xs">
              Nothing said matches that.
            </p>
          ) : (
            <>
              <p className="text-muted-foreground/60 px-2.5 pt-1 pb-2 text-[10px] tracking-wider uppercase">
                {hits.length} {hits.length === 1 ? "mention" : "mentions"}
              </p>
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
            </>
          )}
        </div>
      ) : (
        <div className="kith-fade-bottom min-h-0 flex-1 overflow-y-auto p-2">
          {items.length === 0 ? (
            <p className="text-muted-foreground p-4 text-center text-sm">
              Nothing yet. Say something to him and it will be kept here.
            </p>
          ) : (
            <>
              {groups.map((group) => (
                <section key={group.key}>
                  {/* Sticky, so the day you are looking at is named while you are inside it.
                      It needs the panel's own backdrop to sit on, which is why the aside has
                      a background now rather than letting the room show straight through. */}
                  <h3 className="bg-sidebar/80 text-muted-foreground/70 sticky top-0 z-10 px-2.5 py-1.5 text-[10px] font-medium tracking-wider uppercase backdrop-blur-sm">
                    {group.label}
                    {group.status ? (
                      <span className="text-muted-foreground/45 normal-case"> · {group.status}</span>
                    ) : null}
                  </h3>
                  <ul className="space-y-0.5 pb-1">
                    {group.items.map((item) => (
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
                            // The file stays. Tidying a list and destroying the only record of
                            // an afternoon are not the same act, so they are not the same click.
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
                              "hover:bg-accent/60 relative flex w-full flex-col items-start gap-0.5 rounded-lg py-2 pr-2.5 pl-3 text-left transition-colors",
                              item.id === activeId && "bg-kith-soft/50",
                            )}
                          >
                            {/* A bar, not only a tint. The tint alone was invisible the moment
                                the row scrolled near the edge, so the conversation you were in
                                was unfindable in the list of the ones you were not. */}
                            {item.id === activeId ? (
                              <span
                                aria-hidden
                                className="bg-kith absolute inset-y-1.5 left-0 w-0.5 rounded-full"
                              />
                            ) : null}
                            <span className="flex w-full items-center gap-1.5">
                              <span className="min-w-0 flex-1 truncate text-sm">{item.title}</span>
                              {/* He carries on by himself in a session you have left. Nothing
                                  said which ones, so a conversation working away in the
                                  background looked exactly like one that had finished. */}
                              {item.working ? (
                                <span
                                  className="bg-roam size-1.5 shrink-0 animate-pulse rounded-full"
                                  title="Still working in this conversation"
                                />
                              ) : null}
                            </span>
                            <span className="text-muted-foreground/60 text-[10px] tabular-nums">
                              {item.messages} msg · {dayLabel(item.updatedAt)} {time(item.updatedAt)}
                            </span>
                          </button>
                        </ItemMenu>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
              {more ? (
                <button
                  type="button"
                  onClick={() => setLimit((was) => Math.min(MAX, was + 2 * PAGE))}
                  className="text-muted-foreground/70 hover:text-foreground hover:bg-accent/60 mt-1 w-full rounded-lg px-2.5 py-2 text-[11px] transition-colors"
                >
                  Load more
                </button>
              ) : capped ? (
                <p className="text-muted-foreground/50 px-2.5 py-2 text-center text-[10px] leading-snug">
                  Showing the {MAX} most recent. Search to reach older ones.
                </p>
              ) : null}
            </>
          )}
        </div>
      )}

      {/* The "Hide" button that used to sit under this is gone: the toggle it duplicated is in
          the header two inches up, and Escape does it too. */}
      {storage ? (
        <div className="text-muted-foreground/60 border-border/60 border-t px-4 py-2 font-mono text-[10px] tabular-nums">
          {storage.files} transcript{storage.files === 1 ? "" : "s"} · {bytes(storage.bytes)} on
          disk
        </div>
      ) : null}
    </aside>
  );
}

interface Group {
  key: string;
  label: string;
  /** Shown beside the name when the project is not active — being bound to a closed one is
   *  *why* nothing is happening in these sessions, which is the thing worth knowing. */
  status?: string;
  items: ConversationSummary[];
}

/**
 * Group the listing by the project each session is working on.
 *
 * By project rather than by day, which is what this was. A day is how you'd look for something
 * if you remembered when it happened; a project is how you look for it when you remember what it
 * was about — and with two projects running at once, consecutive days interleave sessions from
 * both, so the one axis that separated them was the one not being used. The day each session
 * last moved is on its own row instead, so nothing is lost by dropping the date headings.
 *
 * "No project" goes last and is a real group, not an empty state: a session for a one-off errand
 * is meant to stay unbound.
 */
function groupByProject(items: ConversationSummary[], projects: Project[]): Group[] {
  const known = new Map(projects.map((project) => [project.id, project]));
  const groups = new Map<string, Group>();
  for (const item of items) {
    const id = item.projectId;
    const key = id === null ? "none" : String(id);
    let group = groups.get(key);
    if (!group) {
      const project = id === null ? undefined : known.get(id);
      group = {
        key,
        // A project he started this session on may not be in the list yet, and an id is a
        // better answer than a blank heading.
        label: id === null ? "No project" : (project?.name ?? `Project #${id}`),
        status: project && project.status !== "active" ? project.status : undefined,
        items: [],
      };
      groups.set(key, group);
    }
    group.items.push(item);
  }
  // Insertion order is already most-recently-active first, because `items` arrives newest first
  // — so the project you touched last is at the top without sorting anything.
  const all = [...groups.values()];
  return [...all.filter((g) => g.key !== "none"), ...all.filter((g) => g.key === "none")];
}


function bytes(count: number): string {
  if (count < 1024) return `${count} B`;
  if (count < 1024 * 1024) return `${(count / 1024).toFixed(0)} KB`;
  return `${(count / 1024 / 1024).toFixed(1)} MB`;
}
