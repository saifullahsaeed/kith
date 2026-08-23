import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  ChevronRight,
  Folder,
  MessageSquare,
  Plus,
  Search,
  X,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { ItemMenu } from "@/components/ui/item-menu";
import { useConfirm } from "@/components/ui/confirm";
import { usePrompt } from "@/components/ui/prompt";
import {
  deleteConversation,
  fetchConversations,
  renameConversation,
  searchConversations,
  type ConversationSummary,
  type TranscriptHit,
} from "@/lib/backend";
import { fetchProjects, type Project } from "@/lib/backend/brain";
import { dayLabel, time } from "@/lib/dates";
import { keys } from "@/lib/query-keys";
import { openOnHost } from "@/lib/files";
import { pathForTab } from "@/lib/router";
import { cn } from "@/lib/utils";

/** How many to ask for at first, and the most the server will hand over in one call
 *  (`/api/conversations` clamps to 500). Past that, search is the way in — a list of a
 *  thousand titles is not something anyone reads to the end. */
const PAGE = 100;
const MAX = 500;

/** How many projects the sidebar carries before the rest live on the screen built for them.
 *  Four fits without pushing the conversations under the fold, which is the whole trade. */
const PROJECTS_SHOWN = 4;

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
 *
 * What each row *says* was the thing wrong with it for longer than any of that. It carried the
 * first line you typed and a count of the messages since — so a hundred and fifty rows read
 * "hey · 80 msg", and both halves describe the input to a conversation. Nobody opens a history
 * list to find out how a session started or how long it got; they open it to find where they
 * left something. So the row is his last word now (`lastSaid`, kept on the index row — see
 * services/conversations.py), the message count is gone, and so is the "46.6 MB on disk" footer,
 * which was a fact about his filesystem sitting where a fact about your work should be.
 *
 * And only one project is open at a time. A hundred and fifty rows is an archive, not a list:
 * the one you are working in is expanded, the rest are a name and a count until you ask, and
 * search is the way into anything older.
 */
export function HistoryPanel({
  activeId,
  onOpen,
  onNew,
  onClose,
}: {
  activeId: string;
  onOpen: (id: string) => void;
  /** A fresh chat, optionally already bound to a project — see workspace's `pendingProject`. */
  onNew: (projectId?: number | null) => void;
  onClose: () => void;
}) {
  const cache = useQueryClient();
  const prompt = usePrompt();
  const [limit, setLimit] = useState(PAGE);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<TranscriptHit[] | null>(null);
  // Which project sections you have opened or closed by hand. Held as the *difference* from
  // what would be open anyway, so the one you are working in stays open as you move between
  // conversations without a toggle you never pressed being remembered as a preference.
  const [toggled, setToggled] = useState<Set<string>>(() => new Set());
  const confirm = useConfirm();
  const navigate = useNavigate();

  /* The list, cached and keyed by how much of it was asked for.
   *
   * `activeId` is in the key rather than in an effect that refetches on it: opening a conversation
   * moves it to the top and creating one adds it, so the answer genuinely differs by which one is
   * active — and a key that says so gets the right list from cache when you go back to one instead
   * of refetching for it. A `project` change invalidates this too, because a group here is named
   * after a project. */
  const { data: items = [] } = useQuery({
    queryKey: keys.conversations(limit, activeId),
    queryFn: async () => (await fetchConversations(limit)).conversations,
  });
  const load = useCallback(
    () => void cache.invalidateQueries({ queryKey: ["conversations"] }),
    [cache],
  );

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
      if (
        document.querySelector(
          '[role="dialog"], [role="menu"], [data-state="open"]',
        )
      )
        return;
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
  /* Names and statuses for the group headings.
   *
   * This refetched the entire brain snapshot every time `items` changed — a new object identity on
   * every list refresh, so in practice on every reload of the panel. It is the same `["brain"]` the
   * control panel holds, so asking for it here is now free when that is already loaded, and a
   * `project` change refreshes both at once. */
  const { data: projects = [] } = useQuery({
    queryKey: keys.projects(),
    queryFn: fetchProjects,
  });
  const groups = useMemo(
    () => groupByProject(items, projects),
    [items, projects],
  );

  /* Projects are a short list at the top; chats are the panel.
   *
   * Every project used to get a heading of its own, so six of them — four of which were finished
   * — pushed the twenty-three loose conversations below the fold in a sidebar whose job is
   * finding a conversation. A sidebar is not a project manager: the few you are actually in
   * belong here, and the rest belong on the screen that already exists for them.
   */
  const { shownProjects, projectCount, chats, ordered } = useMemo(() => {
    const all = groups.filter((group) => group.kind === "project");
    const loose = groups.find((group) => group.kind === "chats");
    // Always include the one you are in, even when it is not recent enough to make the cut —
    // a panel that hides the project you are working in is worse than one that shows five.
    const holding = all.find((group) =>
      group.items.some((one) => one.id === activeId),
    );
    const top = all.slice(0, PROJECTS_SHOWN);
    if (holding && !top.includes(holding)) top.push(holding);
    return {
      shownProjects: top,
      // Every project, not every project that happens to have a conversation in it. The link
      // said "All 5" and landed on a screen showing six, because these are two different
      // questions: the sidebar groups conversations, and a project he started but has not
      // talked in yet has none. A count on a link is a promise about where it goes.
      projectCount: Math.max(projects.length, all.length),
      chats: loose,
      ordered: [...top, ...(loose ? [loose] : [])],
    };
  }, [groups, projects, activeId]);

  // Open by default: the chats, always — they are the list, not a drawer — and the project you
  // are in. A panel that opens entirely shut answers nothing until you click.
  const openByDefault = useMemo(() => {
    const holding = ordered.find((group) =>
      group.items.some((one) => one.id === activeId),
    );
    return new Set([chats?.key, holding?.key].filter(Boolean) as string[]);
  }, [ordered, chats, activeId]);
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
        <Button size="sm" variant="outline" onClick={() => onNew()}>
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
              <p className="text-muted-foreground/60 px-2.5 pt-1 pb-2 text-[10px] font-semibold tracking-[0.12em] uppercase">
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
              {/* The projects band, and the way out of it.
                  A count on the link rather than a bare "View all": the number is the reason to
                  press it, and without one there is nothing to say whether the four on screen
                  are most of them or a tenth. */}
              {shownProjects.length > 0 ? (
                <div className="flex items-center gap-1.5 px-2.5 pt-0.5 pb-1">
                  <span className="text-muted-foreground/45 text-[10px] font-semibold tracking-[0.12em] uppercase">
                    Projects
                  </span>
                  <span className="flex-1" />
                  <button
                    type="button"
                    onClick={() => navigate(pathForTab("projects"))}
                    className="text-muted-foreground/50 hover:text-foreground flex items-center gap-0.5 text-[10px] transition-colors"
                  >
                    {projectCount > shownProjects.length
                      ? `All ${projectCount}`
                      : "Open"}
                    <ArrowUpRight className="size-3" />
                  </button>
                </div>
              ) : null}
              {ordered.map((group) => {
                // XOR against the default: no effect syncing state to props, and nothing to
                // go stale when the active conversation moves to another project.
                const open =
                  openByDefault.has(group.key) !== toggled.has(group.key);
                // The rule between the projects and the conversations. Two different kinds of
                // thing in one column need a line, or the last project reads as the first chat.
                const bandStarts =
                  group.kind === "chats" && shownProjects.length > 0;
                return (
                  <section
                    key={group.key}
                    className={cn(
                      bandStarts && "border-border/40 mt-1.5 border-t pt-1.5",
                    )}
                  >
                    {/* Sticky, so the project you are looking at is named while you are inside
                      it. It needs the panel's own backdrop to sit on, which is why the aside
                      has a background now rather than letting the room show straight through. */}
                    {/* Sentence case, not uppercase with letterspacing.

                      A heading is navigation and a conversation is content, and the old treatment
                      had it backwards: `SADEEF CAPITAL SERVI…` was the loudest thing on the
                      screen *and* truncated, because uppercase plus 0.12em of tracking costs
                      roughly a third of the width for the same words. In sentence case the real
                      names fit, and they stop competing with the sentences underneath them.

                      An icon rather than a word for the kind. It is the distinction the panel was
                      missing — a project and the loose chats are not two projects — and it costs
                      twelve pixels instead of a row. */}
                    <h3 className="bg-sidebar/80 sticky top-0 z-10 backdrop-blur-sm">
                      {/* Right-click a project to start a conversation in it.
                          The project is already on screen here, named, with its sessions under
                          it — so this is where you are when you decide the next chat belongs to
                          it. The alternative was the long way round: new chat, then find the
                          project again in the session bar's picker. The binding is written once
                          the conversation has an id; see workspace's `pendingProject`. */}
                      <ItemMenu
                        title={group.label}
                        actions={
                          group.projectId === null
                            ? [
                                {
                                  label: "New chat",
                                  onSelect: () => onNew(null),
                                },
                              ]
                            : [
                                {
                                  label: "New chat here",
                                  hint: group.label,
                                  onSelect: () => onNew(group.projectId),
                                },
                                {
                                  label: "Open in Projects",
                                  onSelect: () =>
                                    navigate(pathForTab("projects")),
                                },
                              ]
                        }
                      >
                        <button
                          type="button"
                          aria-expanded={open}
                          onClick={() =>
                            setToggled((current) => {
                              const next = new Set(current);
                              if (!next.delete(group.key)) next.add(group.key);
                              return next;
                            })
                          }
                          className={cn(
                            "hover:text-foreground flex w-full items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-medium transition-colors",
                            // Finished projects are still reachable and no longer in the way.
                            group.finished
                              ? "text-muted-foreground/45"
                              : "text-muted-foreground/85",
                          )}
                        >
                          <ChevronRight
                            className={cn(
                              "size-3 shrink-0 transition-transform",
                              open && "rotate-90",
                            )}
                          />
                          {group.kind === "chats" ? (
                            <MessageSquare
                              className="size-3 shrink-0 opacity-60"
                              aria-hidden
                            />
                          ) : (
                            <Folder
                              className="size-3 shrink-0 opacity-60"
                              aria-hidden
                            />
                          )}
                          <span
                            className="min-w-0 truncate"
                            title={group.label}
                          >
                            {group.label}
                          </span>
                          {/* A dot, not the word. "done" and "paused" spelled out beside a
                          truncated name were two things fighting for the same inch; the state
                          matters, its spelling does not. */}
                          {group.status ? (
                            <span
                              aria-hidden
                              title={group.status}
                              className="bg-muted-foreground/40 size-1.5 shrink-0 rounded-full"
                            />
                          ) : null}
                          {group.status ? (
                            <span className="sr-only">{group.status}</span>
                          ) : null}
                          {/* The count is what makes a closed section honest — a name on its own
                          gives no reason to open it, and no sense of what is behind it. */}
                          <span className="text-muted-foreground/40 ms-auto shrink-0 font-normal tabular-nums">
                            {group.items.length}
                          </span>
                          {/* What is happening inside, whether or not you can see the rows.
                            *
                            * This was `!open && …`, on the reasoning that an open section shows
                            * you its own rows — which is true only if the row is on screen. Twenty
                            * conversations under a project means the one that is working is as
                            * likely to be scrolled past as not, and a heading is the thing you
                            * scan. So it says so either way, and counts rather than merely
                            * existing, because "one of these is waiting" and "four of them are"
                            * are different sizes of problem. */}
                          {group.waiting > 0 ? (
                            <span
                              className="bg-kith/15 text-kith flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium tabular-nums"
                              title={`${group.waiting} waiting for your answer`}
                            >
                              <span className="bg-kith size-1.5 animate-pulse rounded-full" />
                              {group.waiting}
                            </span>
                          ) : null}
                          {group.working > 0 ? (
                            <span
                              className="bg-roam/15 text-roam flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium tabular-nums"
                              title={`${group.working} still working`}
                            >
                              <span className="bg-roam size-1.5 animate-pulse rounded-full" />
                              {group.working}
                            </span>
                          ) : null}
                        </button>
                      </ItemMenu>
                    </h3>
                    <ul className={cn("space-y-0.5 pb-1", !open && "hidden")}>
                      {group.items.map((item) => (
                        <li key={item.id}>
                          <ItemMenu
                            title={item.title}
                            copy={item.title}
                            actions={[
                              // The rows are what you right-click, not the heading above them —
                              // so the same offer is here, named after the project it lands in.
                              ...(group.projectId !== null
                                ? [
                                    {
                                      label: "New chat here",
                                      hint: group.label,
                                      onSelect: () => onNew(group.projectId),
                                    },
                                  ]
                                : []),
                              {
                                label: "Reveal transcript",
                                hint: "in Finder",
                                onSelect: () =>
                                  void openOnHost(item.transcript, true),
                              },
                              {
                                label: "Rename",
                                onSelect: () => {
                                  // `window.prompt` does not exist in Electron — it throws, the
                                  // throw escapes Radix's `onSelect`, and the menu just closed
                                  // with nothing happening. See `ui/prompt.tsx`.
                                  void prompt({
                                    title: "Rename this conversation",
                                    initial: item.title,
                                  }).then((next) => {
                                    if (next) void renameConversation(item.id, next).then(load);
                                  });
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
                              if (ok)
                                void deleteConversation(item.id, false).then(
                                  load,
                                );
                            }}
                          >
                            {/* A line, not a card.

                                This was `text-sm` over two clamped lines with the date on a
                                third, inside a full-width tint — around seventy pixels for one
                                conversation against twenty-six for a whole project's heading,
                                so a single row outweighed every project below it. The rule for
                                a list is that no row may be louder than the thing that groups
                                it: smaller type, tighter leading, and the stamp moved up beside
                                the text instead of taking a line of its own. */}
                            <button
                              type="button"
                              onClick={() => onOpen(item.id)}
                              className={cn(
                                "hover:bg-accent/50 relative flex w-full items-start gap-2 rounded-md py-1.5 pr-2 pl-3 text-left transition-colors",
                                item.id === activeId && "bg-kith-soft/40",
                              )}
                            >
                              {/* A bar, not only a tint. The tint alone was invisible the moment
                                the row scrolled near the edge, so the conversation you were in
                                was unfindable in the list of the ones you were not. */}
                              {item.id === activeId ? (
                                <span
                                  aria-hidden
                                  className="bg-kith absolute inset-y-1 left-0 w-0.5 rounded-full"
                                />
                              ) : null}
                              {/* Where he left it, not how you opened it. Two lines, because one
                                truncates every sentence at the verb; the title is the fallback
                                only while he has yet to say anything. */}
                              <span
                                className={cn(
                                  "min-w-0 flex-1 text-[12.5px] leading-[1.45]",
                                  item.lastSaid
                                    ? "line-clamp-2"
                                    : "text-muted-foreground truncate italic",
                                )}
                              >
                                {item.lastSaid || item.title}
                              </span>
                              {/* Two states a row can be in that its text cannot say.
                                *
                                * `waiting` first, and not only for tidiness: a conversation that is
                                * working needs nothing from you and one that is waiting needs only
                                * you, so when a turn has parked on a question the answer is the
                                * whole story and "still working" is the wrong word for it. Amber
                                * because that is his colour when he is *not* running — he is
                                * standing there — against the green of work in progress.
                                *
                                * Both were unreachable until the state behind them was real:
                                * `working` was read from a database column that does not exist, so
                                * this dot could never light, and `waiting` had no representation
                                * outside the conversation at all. */}
                              {item.waiting ? (
                                <span
                                  className="bg-kith mt-1.5 size-1.5 shrink-0 animate-pulse rounded-full"
                                  title="Waiting for your answer"
                                />
                              ) : item.working ? (
                                <span
                                  className="bg-roam mt-1.5 size-1.5 shrink-0 animate-pulse rounded-full"
                                  title="Still working in this conversation"
                                />
                              ) : null}
                              <span
                                className="text-muted-foreground/45 mt-px shrink-0 text-[10px] tabular-nums"
                                title={`${dayLabel(item.updatedAt)} ${time(item.updatedAt)}`}
                              >
                                {stamp(item.updatedAt)}
                              </span>
                            </button>
                          </ItemMenu>
                        </li>
                      ))}
                    </ul>
                  </section>
                );
              })}
              {more ? (
                <button
                  type="button"
                  onClick={() =>
                    setLimit((was) => Math.min(MAX, was + 2 * PAGE))
                  }
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

      {/* Two things used to sit under this and both are gone. The "Hide" button duplicated the
          toggle in the header two inches up, which Escape also does. And "152 transcripts ·
          46.6 MB on disk" was the last line of the panel — the most permanent thing on it —
          spent on how much space his notes take up, which is a question nobody has ever had
          about their own conversations. */}
    </aside>
  );
}

interface Group {
  key: string;
  label: string;
  /** The project this band is, or null for the loose chats — what "start a chat in here" needs. */
  projectId: number | null;
  /** What kind of thing this is, which is the distinction the panel was missing.
   *
   * A project and the loose conversations are not two projects, and rendering them as six
   * identical shouting headings — one of which was called "NO PROJECT" — made the everyday
   * chats look like the leftovers of a filing system rather than the place most talking
   * actually happens. */
  kind: "project" | "chats";
  /** Shown beside the name when the project is not active — being bound to a closed one is
   *  *why* nothing is happening in these sessions, which is the thing worth knowing. */
  status?: string;
  /** How many conversations in here have a turn running. */
  working: number;
  /** How many are blocked on an answer from you. Counted apart because they are a different
   *  request: one is progress you can ignore, the other is progress that has stopped for you. */
  waiting: number;
  /** Done, paused, archived. Sorted to the bottom and rendered quietly: four finished projects
   *  were each taking a full row above twenty-three live conversations. */
  finished: boolean;
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
function groupByProject(
  items: ConversationSummary[],
  projects: Project[],
): Group[] {
  const known = new Map(projects.map((project) => [project.id, project]));
  const groups = new Map<string, Group>();
  for (const item of items) {
    /* A project that no longer exists is not a project.
     *
     * `delete_project` orphaned the tasks and dropped the milestones but left the conversations
     * pointing at the row it had just deleted — and the fallback below turns a dangling id into
     * a heading, so a project you removed came back as "Project #15" holding the conversations
     * it used to hold. The server clears them at the source now (repositories/projects.py, and
     * migration v41 for the ones already written); this is the panel refusing to invent a name
     * from a number in any case.
     *
     * Only once the projects have actually loaded. `projects` is empty on the first render, when
     * every id is unknown — folding them all into Chats then would rearrange the whole list a
     * beat after it appeared. */
    const gone =
      item.projectId !== null &&
      projects.length > 0 &&
      !known.has(item.projectId);
    const id = gone ? null : item.projectId;
    const key = id === null ? "none" : String(id);
    let group = groups.get(key);
    if (!group) {
      const project = id === null ? undefined : known.get(id);
      group = {
        key,
        // A project he started this session on may not be in the list yet, and an id is a
        // better answer than a blank heading.
        //
        // "Chats" rather than "No project". The old name described these by the thing they did
        // not have, put them last, and left the largest group in the panel — twenty-three
        // sessions against four — reading as an unfiled remainder. Talking to him without a
        // project is not a filing failure; it is most of what the app is for.
        label: id === null ? "Chats" : (project?.name ?? `Project #${id}`),
        kind: id === null ? "chats" : "project",
        projectId: id,
        status:
          project && project.status !== "active" ? project.status : undefined,
        finished: Boolean(project && project.status !== "active"),
        working: 0,
        waiting: 0,
        items: [],
      };
      groups.set(key, group);
    }
    group.items.push(item);
  }
  // Counted here rather than in the heading, so the numbers exist whether or not that section
  // is being rendered open.
  for (const group of groups.values()) {
    group.working = group.items.filter((one) => one.working).length;
    group.waiting = group.items.filter((one) => one.waiting).length;
  }
  // Insertion order inside each band is already most-recently-active first, because `items`
  // arrives newest first — so the project you touched last is at the top of its band without
  // sorting anything.
  //
  // Three bands, and the order is the answer to "where is the thing I want": what is live, then
  // where most conversations are, then what is over. Finished projects used to sit above the
  // chats purely because a session in one had been touched more recently, which put four dead
  // projects between you and everything you actually say.
  const all = [...groups.values()];
  return [
    ...all.filter((g) => g.kind === "project" && !g.finished),
    ...all.filter((g) => g.kind === "chats"),
    ...all.filter((g) => g.kind === "project" && g.finished),
  ];
}

/**
 * The whole stamp in the space of one word: the clock for today, the day for anything else.
 *
 * "Today 04:03 PM" was both halves at once, and one of them is always the redundant one —
 * the clock is what separates four of today's sessions from each other, and is noise on
 * something from August. The full date is in the tooltip for when it is the thing you want.
 */
function stamp(iso: string): string {
  const day = dayLabel(iso);
  return day === "Today" ? time(iso) : day;
}
