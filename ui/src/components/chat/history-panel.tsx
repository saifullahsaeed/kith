import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  keepPreviousData,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ChevronRight, Plus, Search, X } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { agentCount, permissionHolder } from "@/components/shell/chat-states";
import { AgentDot } from "@/components/chat/agent-tree";
import { ItemMenu } from "@/components/ui/item-menu";
import { useConfirm } from "@/components/ui/confirm";
import { usePrompt } from "@/components/ui/prompt";
import {
  deleteConversation,
  fetchAgentTree,
  fetchConversations,
  fetchLiveTurns,
  renameConversation,
  searchConversations,
  type ConversationSummary,
  type PermissionRequest,
  type TranscriptHit,
} from "@/lib/backend";
import { fetchProjects, type Project } from "@/lib/backend/brain";
import { fetchOpenQuestion } from "@/lib/backend/questions";
import {
  AGENT_INDENT,
  AGENT_WORD,
  agentLook,
  countAgents,
  flattenAgents,
  useAgentTree,
} from "@/lib/agents";
import { dayLabel, time } from "@/lib/dates";
import { keys } from "@/lib/query-keys";
import { useDraftedTabs } from "@/lib/drafts";
import { openOnHost } from "@/lib/files";
import { pathForTab } from "@/lib/router";
import { cn } from "@/lib/utils";
import { Layer, useLayer } from "@/hooks/use-layer";
import { usePermissions } from "@/hooks/use-permissions";

/** How many to ask for at first, and the most the server will hand over in one call
 *  (`/api/conversations` clamps to 500). Past that, search is the way in — a list of a
 *  thousand titles is not something anyone reads to the end. */
const PAGE = 100;
const MAX = 500;

/** How many projects the sidebar carries before the rest live on the screen built for them.
 *  Four fits without pushing the conversations under the fold, which is the whole trade. */
const PROJECTS_SHOWN = 4;

/* The four colours a row can speak in, and nothing else — see KIT principle 7.
 *
 * `KITH_MUTED` is the amber of a reason line: his colour with a little of the text colour and a
 * quarter of the muted one in it, so a sentence in it can be read rather than only noticed — it
 * resolves to #DCB47A against the design's #D8B77A. `TEAL` is the agents' colour the same way:
 * Tailwind's teal-400 is a shade more electric than the design's #4CC9B8, and a quarter of
 * the muted grey brings it to #4CC7B3. Both are mixed in oklab, where a near-grey barely moves
 * the hue, and both from tokens rather than hexes: light mode gets a darker amber from its own
 * `--kith`, and a teal-600 that reads on white. */
const KITH_MUTED =
  "text-[color-mix(in_oklab,color-mix(in_oklab,var(--kith)_87%,var(--foreground))_75%,var(--muted-foreground))]";
const TEAL =
  "text-teal-600 dark:text-[color-mix(in_oklab,var(--color-teal-400)_75%,var(--muted-foreground))]";
const TEAL_DOT =
  "bg-teal-500 dark:bg-[color-mix(in_oklab,var(--color-teal-400)_75%,var(--muted-foreground))]";
const FAINT = "text-muted-foreground/85";
const BREATHE = "animate-[kith-breathe_2.4s_ease-in-out_infinite]";

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
 * Three bands, top to bottom, in the order of how much they want from you:
 *
 * - **Needs you** — conversations parked on a question, and permission requests. Present only
 *   when there is something, in amber, because it is the one thing on the rail that is blocked
 *   on the person reading it (KIT principle 4: what needs the person rises).
 * - **Projects** — the few you are in. The open one lists its conversations indented, each with
 *   the state it is in *right now* as its second line: a question in amber, "Working" in green,
 *   and otherwise his last word, muted.
 * - **Chats** — everything not in a project, one line each: what it is, and when it last moved.
 *
 * A chat that started agents carries them under its row: a quiet count that folds out into the
 * tree — each agent a conversation of its own, opened like any other. Agents are never rows of
 * their own here, because you never talk to one; they are listed under the chat you do talk to.
 *
 * Grouped by project. It was flat first — seventeen hundred rows reading "hi · 2 msg · 2:28 AM"
 * with nothing saying which day any belonged to — then grouped by day, which fixed the scanning
 * but not the finding: with two projects running at once, every day interleaves sessions from
 * both, so the one axis that told them apart was the one not in use.
 *
 * And only one project is open at a time. A hundred and fifty rows is an archive, not a list:
 * the one you are working in is expanded, the rest are a name and a count until you ask, and
 * search is the way into anything older.
 */
/** A conversation's title, ready to be read at the front of a row.
 *
 * The title is the message that started the conversation, verbatim — which is the right source
 * (it is what *you* said, so it is what you will look for) and the wrong string. Paste a snippet
 * to ask about it and the row opens with a code fence; start with a heading and it opens with
 * hashes. It only began to matter when the title moved from a fallback to the first line of
 * every row, so the cleanup lives here with the row rather than in the server's naming.
 *
 * Deliberately not a markdown parser. Three leading marks cover what people actually paste, and
 * anything cleverer would start rewriting titles that were fine. */
function rowTitle(title: string): string {
  return title.replace(/^[\s`>#*-]+/, "").trim() || title.trim();
}

export function HistoryPanel({
  activeId,
  onOpen,
  onNew,
}: {
  activeId: string;
  onOpen: (id: string) => void;
  /** A fresh chat, optionally already bound to a project — see workspace's `pendingProject`. */
  onNew: (projectId?: number | null) => void;
}) {
  const cache = useQueryClient();
  const prompt = usePrompt();
  const layer = useLayer(Layer.Panel);
  const searchBox = useRef<HTMLInputElement | null>(null);
  /* Which conversations are holding something unsent.
   *
   * Read from the store rather than handed down, because a draft never reaches the server — so
   * unlike `working` and `waiting` it could not arrive on a conversation row however the panel
   * asked for it. Shallow-compared inside `useDraftedTabs`, so typing does not re-render this
   * list; `""` is dropped because a chat with no conversation yet has no row here to mark, and
   * its tab is the whole answer. */
  const drafted = useDraftedTabs();
  const drafting = useMemo(
    () => new Set(Object.values(drafted).filter(Boolean)),
    [drafted],
  );
  const [limit, setLimit] = useState(PAGE);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<TranscriptHit[] | null>(null);
  // Which project sections you have opened or closed by hand. Held as the *difference* from
  // what would be open anyway, so the one you are working in stays open as you move between
  // conversations without a toggle you never pressed being remembered as a preference.
  const [toggled, setToggled] = useState<Set<string>>(() => new Set());
  // Which chats' agent trees you folded in or out, by hand. Not the XOR the groups use: a tree
  // you opened in order to click one of its agents is a tree that would *default* open the moment
  // that agent is in front of you, and the XOR then folded it shut under your cursor.
  const [agentsToggled, setAgentsToggled] = useState<Map<string, boolean>>(() => new Map());
  const showAgents = useAgentTree((state) => state.show);
  const confirm = useConfirm();
  const navigate = useNavigate();

  /* The list, cached and keyed by how much of it was asked for.
   *
   * `activeId` is in the key rather than in an effect that refetches on it: opening a conversation
   * moves it to the top and creating one adds it, so the answer genuinely differs by which one is
   * active — and a key that says so gets the right list from cache when you go back to one instead
   * of refetching for it. A `project` change invalidates this too, because a group here is named
   * after a project. */
  const { data: items = [], isPending } = useQuery({
    queryKey: keys.conversations(limit, activeId),
    queryFn: async () => (await fetchConversations(limit)).conversations,
    /* Keep the list on screen while the next one loads.
     *
     * Every switch is a key with no cached data, and without this `items` fell back to `[]` and
     * the whole sidebar was replaced by "Nothing yet" until the fetch returned. `keepPreviousData`
     * shows the last list against the new key until the new one arrives, which is nearly always
     * the same rows in a slightly different order. */
    placeholderData: keepPreviousData,
  });
  const load = useCallback(
    () => void cache.invalidateQueries({ queryKey: ["conversations"] }),
    [cache],
  );

  /* Which chat the conversation in front of you belongs under.
   *
   * Itself, nearly always. But an agent is never in this list — you do not talk to one — so when
   * you are reading an agent, the chat to keep open (its project, its fold of agents) is the one
   * at the top of its tree, which only the tree can say. Asked only when the active conversation is
   * not a row here, which is the only time the answer is not already known. */
  const listed = items.some((one) => one.id === activeId);
  const { data: activeTree } = useQuery({
    queryKey: keys.agentTree(activeId),
    queryFn: () => fetchAgentTree(activeId),
    enabled: Boolean(activeId) && !isPending && !listed,
  });
  const home = listed ? activeId : (activeTree?.root ?? activeId);

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

  /* Escape clears the search, and stops there. It used to close the panel once the search was
   * empty, which suited a tab you could reopen from its own strip. The rail is furniture: a stray
   * Escape should not make the way you reach every conversation disappear.
   *
   * Guarded on anything modal being open, because a confirm dialog and a context menu both live
   * inside this panel and both close on Escape: without the guard, cancelling "remove this
   * conversation?" also cleared the search you were tidying from. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !layer.frontmost()) return;
      if (query) setQuery("");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [query, layer]);

  /* ⌘K puts you in the search — the field says so, so it has to be true.
   *
   * The control panel binds the same key to its own search while it is open, and both of us are
   * `Layer.Panel`. Deferred a tick so every listener has had the event first: if the panel took
   * it (`preventDefault`), it is the panel's. Otherwise nothing else in the app answers ⌘K, and
   * the rail is the one search that is always on screen. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey)
        return;
      if (event.key.toLowerCase() !== "k") return;
      setTimeout(() => {
        if (event.defaultPrevented || !layer.frontmost()) return;
        searchBox.current?.focus();
        searchBox.current?.select();
      }, 0);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [layer]);

  /* Names and statuses for the project rows.
   *
   * The same `["projects"]` the control panel holds, so asking for it here is free when that is
   * already loaded, and a `project` change refreshes both at once. It carries no task counts —
   * the open project's "6/13" comes from its own board; see `fetchProjectProgress`. */
  const { data: projects = [] } = useQuery({
    queryKey: keys.projects(),
    queryFn: fetchProjects,
  });
  // Grouped in the order the server sent them (most recent first), never re-sorted: the
  // listing's order is the answer to "what was I just doing", and a client that sorts it
  // again is a client that can disagree with it.
  const groups = useMemo(
    () => groupByProject(items, projects),
    [items, projects],
  );

  /* Projects are a short list at the top; chats are the panel.
   *
   * Every project used to get a heading of its own, so six of them — four of which were finished
   * — pushed the twenty-three loose conversations below the fold in a sidebar whose job is
   * finding a conversation. The few you are actually in belong here, and the rest belong on the
   * screen that already exists for them.
   */
  const { shownProjects, projectCount, chats, ordered } = useMemo(() => {
    const all = groups.filter((group) => group.kind === "project");
    const loose = groups.find((group) => group.kind === "chats");
    // Always include the one you are in, even when it is not recent enough to make the cut —
    // a panel that hides the project you are working in is worse than one that shows five.
    const holding = all.find((group) =>
      group.items.some((one) => one.id === home),
    );
    const top = all.slice(0, PROJECTS_SHOWN);
    if (holding && !top.includes(holding)) top.push(holding);
    return {
      shownProjects: top,
      // Every project, not every project that happens to have a conversation in it. A count on
      // a link is a promise about where it goes, and the screen it opens lists them all.
      projectCount: Math.max(projects.length, all.length),
      chats: loose,
      ordered: [...top, ...(loose ? [loose] : [])],
    };
  }, [groups, projects, home]);

  /* Open by default: the project you are in, and the project you just started a chat in.
   *
   * That last one is not a nicety. A new chat has no id until its first turn, so `activeId` is ""
   * and no group holds it — which meant pressing + on a project collapsed that project in the
   * same frame as the thread cleared. You asked for a chat in Sadeef One and the panel answered
   * by closing Sadeef One. Held until the conversation actually lands somewhere, at which point
   * `holding` takes over and this is forgotten. */
  const [startedIn, setStartedIn] = useState<number | null>(null);
  useEffect(() => {
    if (activeId) setStartedIn(null);
  }, [activeId]);

  const openByDefault = useMemo(() => {
    const holding = ordered.find((group) =>
      group.items.some((one) => one.id === home),
    );
    const started =
      startedIn === null
        ? undefined
        : ordered.find((group) => group.projectId === startedIn);
    return new Set([holding?.key, started?.key].filter(Boolean) as string[]);
  }, [ordered, home, startedIn]);

  // XOR against the default: no effect syncing state to props, and nothing to go stale when the
  // active conversation moves to another project.
  const isOpen = (group: Group) =>
    openByDefault.has(group.key) !== toggled.has(group.key);
  /** Whether a chat's agents are folded out: the tree you are reading inside, unless you closed
   *  it, and any other you opened. */
  const agentsOpen = (id: string) => agentsToggled.get(id) ?? (id === home && home !== activeId);
  const toggleAgents = (id: string) =>
    setAgentsToggled((current) => {
      const next = new Map(current);
      next.set(id, !agentsOpen(id));
      return next;
    });

  /* How far each *open* project has got: tasks done of the tasks not dropped — the "6/13" the
   * session bar shows.
   *
   * `/api/projects` is names and statuses only, on purpose, and `/api/brain` (which has the
   * counts) is the whole database — its route explains why this panel must not ask for it. One
   * project's board is about 11KB, and only the open one is asked for — normally exactly one.
   * Under `["task", …]`, so the `task` change that moves a card invalidates it with no extra
   * wiring. */
  const openIds = shownProjects
    .filter(isOpen)
    .map((group) => group.projectId)
    .filter((id): id is number => id !== null);
  const boards = useQueries({
    queries: openIds.map((id) => ({
      queryKey: ["task", "project", id] as const,
      queryFn: () => fetchProjectProgress(id),
      staleTime: 30_000,
    })),
  });
  const progressOf = new Map<number, Progress>();
  openIds.forEach((id, at) => {
    const found = boards[at]?.data;
    if (found && found.total > 0) progressOf.set(id, found);
  });

  /** Start a chat in a project, and keep that project open while it is being started. */
  const startIn = (projectId: number | null) => {
    setStartedIn(projectId);
    onNew(projectId);
  };

  /* ── What each conversation is doing ───────────────────────────────────
   *
   * Read from the same three sources, through the same functions, as the tab strip and the
   * presence pill (`shell/chat-states.ts`), so the rail cannot say "2 agents working" while the
   * tab says nothing. A live turn is `/api/chat/live`, and a parked question and the agents busy
   * under a chat are both on the list row. */
  const { pending } = usePermissions();
  const { data: liveIds = [] } = useQuery({
    queryKey: keys.liveTurns(),
    queryFn: fetchLiveTurns,
  });
  const live = useMemo(
    () =>
      new Set([
        ...liveIds,
        ...items.filter((one) => one.working).map((one) => one.id),
      ]),
    [liveIds, items],
  );
  const waiting = useMemo(() => items.filter((one) => one.waiting), [items]);
  /* The chat a permission request belongs to, when that can be said honestly — see
   * `permissionHolder`. Usually it cannot, and the card then names the command or path itself.
   * Given exactly the inputs the workspace gives it for the tabs, so the two cannot pin one
   * request on different chats. */
  const holder = useMemo(
    () =>
      permissionHolder(
        pending.length,
        liveIds,
        new Set(waiting.map((one) => one.id)),
      ),
    [pending.length, liveIds, waiting],
  );

  /* The question each waiting conversation is parked on, so the row can say *what* it is asking.
   *
   * Only for the waiting ones — usually none, rarely more than two — and under the same key the
   * ask card uses, so the conversation you are in costs nothing and the `question` change the
   * server publishes keeps every one of them current without a timer. */
  const asked = useQueries({
    queries: waiting.map((one) => ({
      queryKey: keys.question(one.id),
      queryFn: () => fetchOpenQuestion(one.id),
    })),
  });
  const questionOf = new Map<string, string>();
  waiting.forEach((one, at) => {
    const text = asked[at]?.data?.questions?.[0]?.question?.trim();
    if (text) questionOf.set(one.id, lowerFirst(text));
  });

  /** What a conversation is doing right now, in the ranking `chatState` uses for its tab.
   *
   * What needs you first — a question, then a permission — because those are the only states
   * that stop without you. Then a live turn, the thing happening this second. Then its agents,
   * which carry on in the background after the turn that started them has ended. */
  const stateOf = (item: ConversationSummary): RowState => {
    if (item.waiting)
      return { kind: "waiting", question: questionOf.get(item.id) ?? "" };
    if (holder === item.id && pending[0])
      return { kind: "permission", request: pending[0] };
    if (live.has(item.id)) return { kind: "working" };
    if (item.agentsWorking > 0) return { kind: "agents", count: item.agentsWorking };
    if (drafting.has(item.id)) return { kind: "draft" };
    return { kind: "idle" };
  };
  const held = holder ? items.find((one) => one.id === holder) : undefined;

  // A full page back means there are almost certainly more behind it. The alternative was
  // showing 100 of seventeen hundred with nothing on screen saying so, which is a silent
  // truncation dressed as a complete list.
  const more = items.length >= limit && limit < MAX;
  const capped = items.length >= MAX;
  const needCount = waiting.length + pending.length;

  /** The right-click menu every conversation row carries, wherever it is listed. */
  const rowMenu = (
    item: ConversationSummary,
    group: Group | undefined,
  ): MenuProps => ({
    title: item.title,
    copy: item.title,
    // The id is the name of its transcript file, what the API addresses it by, and the only way
    // to point him at one particular afternoon.
    copyId: item.id,
    actions: [
      ...(group && group.projectId !== null
        ? [
            {
              label: "New chat here",
              hint: group.label,
              onSelect: () => startIn(group.projectId),
            },
          ]
        : []),
      ...(item.agents > 0
        ? [{ label: "Show its agents", onSelect: () => showAgents(item.id) }]
        : []),
      {
        label: "Reveal transcript",
        hint: "in Finder",
        onSelect: () => void openOnHost(item.transcript, true),
      },
      {
        label: "Rename",
        onSelect: () => {
          // `window.prompt` does not exist in Electron — it throws, the throw escapes Radix's
          // `onSelect`, and the menu just closed with nothing happening. See `ui/prompt.tsx`.
          void prompt({
            title: "Rename this conversation",
            initial: item.title,
          }).then((next) => {
            if (next) void renameConversation(item.id, next).then(load);
          });
        },
      },
    ],
    deleteLabel: "Remove from list",
    onDelete: async () => {
      // The file stays. Tidying a list and destroying the only record of an afternoon are not
      // the same act, so they are not the same click.
      const ok = await confirm({
        title: "Remove this conversation?",
        subject: item.title,
        description:
          "It leaves this list. The transcript file stays in his folder.",
        confirmLabel: "Remove",
      });
      if (ok) void deleteConversation(item.id, false).then(load);
    },
  });

  return (
    <aside className="bg-sidebar text-foreground flex h-full min-h-0 w-full flex-col leading-[normal]">
      {/* The search, drawn as the one field at the top of the rail. No "New chat" button above
          it any more: the pencil in the title bar is the way to start one, and the rail is for
          finding. Still a real input — typing searches what was said, Escape clears it. */}
      <div className="px-3 pt-3 pb-2">
        <label
          className={cn(
            "border-input bg-muted/50 flex h-[34px] cursor-text items-center gap-2 rounded-[10px] border px-3 text-[13px] transition-colors",
            "focus-within:border-ring",
          )}
        >
          <Search
            className="text-foreground/90 size-3.5 shrink-0"
            aria-hidden
          />
          <input
            ref={searchBox}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search chats, files, memory"
            aria-label="Search conversations"
            className="placeholder:text-muted-foreground min-w-0 flex-1 bg-transparent outline-none"
          />
          {query ? (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label="Clear the search"
              className="text-muted-foreground hover:text-foreground -me-1 shrink-0 rounded p-0.5"
            >
              <X className="size-3.5" />
            </button>
          ) : (
            <kbd className={cn("shrink-0 font-mono text-[11px]", FAINT)}>
              ⌘K
            </kbd>
          )}
        </label>
      </div>

      {hits !== null ? (
        <div className="kith-fade-bottom flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 pt-1 pb-3">
          {hits.length === 0 ? (
            <p className="text-muted-foreground p-4 text-center text-xs">
              Nothing said matches that.
            </p>
          ) : (
            <>
              <BandLabel>
                {hits.length} {hits.length === 1 ? "mention" : "mentions"}
              </BandLabel>
              <ul className="flex flex-col gap-0.5">
                {hits.map((hit) => (
                  <li key={`${hit.conversationId}-${hit.at}`}>
                    <button
                      type="button"
                      onClick={() => onOpen(hit.conversationId)}
                      className={cn(
                        ROW,
                        "flex-col items-stretch gap-1 px-2 py-1.5",
                        hit.conversationId === activeId
                          ? "bg-accent text-foreground"
                          : "text-foreground/80 hover:bg-accent/50",
                      )}
                    >
                      <span className="flex items-baseline gap-2">
                        <span className="min-w-0 flex-1 truncate text-[13px]">
                          {hit.title}
                        </span>
                        {/* Who said it, because "did I ask for that or did he offer it" is
                            most of why you are looking. */}
                        <span className={cn("shrink-0 text-[11px]", FAINT)}>
                          {hit.role === "user" ? "you" : "him"}
                        </span>
                      </span>
                      <span className="text-muted-foreground line-clamp-2 text-[11px] leading-snug">
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
        <div className="kith-fade-bottom flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 pt-1 pb-3">
          {needCount > 0 ? (
            <section aria-label="Needs you" className="flex flex-col gap-0.5">
              <BandLabel tone="kith" first>
                Needs you <span className="tabular-nums">{needCount}</span>
              </BandLabel>
              {waiting.map((item) => (
                <NeedCard
                  key={item.id}
                  menu={rowMenu(item, undefined)}
                  glyph="?"
                  title={rowTitle(item.title)}
                  why={
                    questionOf.get(item.id)
                      ? `Asking · ${questionOf.get(item.id)}`
                      : "Waiting for your answer"
                  }
                  when={ago(Date.parse(item.updatedAt))}
                  whenTitle={`${dayLabel(item.updatedAt)} ${time(item.updatedAt)}`}
                  current={item.id === activeId}
                  onClick={() => onOpen(item.id)}
                />
              ))}
              {pending.map((request) => (
                <PermissionCard
                  key={request.id}
                  request={request}
                  chat={held}
                  current={Boolean(held) && held?.id === activeId}
                  onOpen={onOpen}
                  menu={held ? rowMenu(held, undefined) : undefined}
                />
              ))}
            </section>
          ) : null}

          {/* Only once the list has actually answered. "Nothing yet" while a fetch is in flight is
              a statement about the network dressed as a statement about your conversations. */}
          {isPending ? null : items.length === 0 ? (
            <p className="text-muted-foreground p-4 text-center text-sm">
              Nothing yet. Say something to him and it will be kept here.
            </p>
          ) : (
            <>
              {/* The projects band, and the way out of it. A count on the link rather than a bare
                  "View all": the number is the reason to press it. */}
              {shownProjects.length > 0 ? (
                <BandLabel first={needCount === 0}>
                  Projects
                  <span className="flex-1" />
                  <button
                    type="button"
                    onClick={() => navigate(pathForTab("projects"))}
                    className={cn(
                      "hover:text-foreground rounded text-[11px] font-normal tracking-normal normal-case transition-colors",
                      FAINT,
                    )}
                  >
                    All {projectCount}
                  </button>
                </BandLabel>
              ) : null}

              {shownProjects.map((group) => {
                const open = isOpen(group);
                const states = group.items.map(stateOf);
                return (
                  <section key={group.key} className="flex flex-col gap-0.5">
                    <ProjectRow
                      group={group}
                      open={open}
                      progress={
                        group.projectId === null
                          ? undefined
                          : progressOf.get(group.projectId)
                      }
                      asking={
                        states.filter((one) => one.kind === "waiting").length
                      }
                      permitting={
                        states.filter((one) => one.kind === "permission").length
                      }
                      working={
                        states.filter((one) => one.kind === "working").length
                      }
                      onToggle={() =>
                        setToggled((current) => {
                          const next = new Set(current);
                          if (!next.delete(group.key)) next.add(group.key);
                          return next;
                        })
                      }
                      onNewChat={() => startIn(group.projectId)}
                      onOpenProjects={() => navigate(pathForTab("projects"))}
                    />
                    {/* `hidden` rather than unmounted, so a context menu or focus inside a
                        section you fold is not torn down mid-gesture. */}
                    <ul
                      className={cn("flex flex-col gap-0.5", !open && "hidden")}
                    >
                      {group.items.map((item) => (
                        <li key={item.id}>
                          <ProjectChatRow
                            item={item}
                            menu={rowMenu(item, group)}
                            state={stateOf(item)}
                            active={item.id === activeId}
                            onClick={() => onOpen(item.id)}
                          />
                          {item.agents > 0 ? (
                            <AgentsUnder
                              item={item}
                              indent={PROJECT_ROW_INDENT}
                              open={agentsOpen(item.id)}
                              onToggle={() => toggleAgents(item.id)}
                              activeId={activeId}
                              onOpen={onOpen}
                            />
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </section>
                );
              })}

              {chats ? (
                <section className="flex flex-col gap-0.5">
                  {/* A label, not a fold. The loose chats are the list — most of what anyone says
                      to him — not a drawer to be opened. Right-click still starts one. */}
                  <ItemMenu
                    title={chats.label}
                    actions={[
                      { label: "New chat", onSelect: () => onNew(null) },
                    ]}
                  >
                    <BandLabel
                      first={needCount === 0 && shownProjects.length === 0}
                    >
                      Chats
                      <span className="flex-1" />
                      <span className="text-[11px] font-normal tracking-normal tabular-nums">
                        {chats.items.length}
                      </span>
                    </BandLabel>
                  </ItemMenu>
                  <ul className="flex flex-col gap-0.5">
                    {chats.items.map((item) => (
                      <li key={item.id}>
                        <ChatRow
                          item={item}
                          menu={rowMenu(item, chats)}
                          state={stateOf(item)}
                          active={item.id === activeId}
                          onClick={() => onOpen(item.id)}
                        />
                        {item.agents > 0 ? (
                          <AgentsUnder
                            item={item}
                            indent={CHAT_ROW_INDENT}
                            open={agentsOpen(item.id)}
                            onToggle={() => toggleAgents(item.id)}
                            activeId={activeId}
                            onOpen={onOpen}
                          />
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}

              {more ? (
                <button
                  type="button"
                  onClick={() =>
                    setLimit((was) => Math.min(MAX, was + 2 * PAGE))
                  }
                  className="text-muted-foreground hover:text-foreground hover:bg-accent/50 mt-1 w-full rounded-[8px] px-2 py-2 text-[11px] transition-colors"
                >
                  Load more
                </button>
              ) : capped ? (
                <p
                  className={cn(
                    "px-2 py-2 text-center text-[10px] leading-snug",
                    FAINT,
                  )}
                >
                  Showing the {MAX} most recent. Search to reach older ones.
                </p>
              ) : null}
            </>
          )}
        </div>
      )}
    </aside>
  );
}

/* ── Pieces ─────────────────────────────────────────────────────────────── */

/** A conversation's right-click menu, handed to the row that owns the element it opens on —
 *  `ItemMenu`'s trigger is `asChild`, so it has to wrap the real button, not a component. */
type MenuProps = Omit<React.ComponentProps<typeof ItemMenu>, "children">;

/** Shared by every clickable row: full width, left-aligned, the 8px radius the design uses, and a
 *  focus ring, because a list you can only point at is a list half its readers cannot use. */
const ROW =
  "flex w-full rounded-[8px] text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none";

/** A band's label: 10px, tracked, uppercase — the one place the rail raises its voice. */
function BandLabel({
  children,
  tone = "muted",
  first = false,
  className,
  ...rest
}: React.ComponentProps<"div"> & {
  tone?: "muted" | "kith";
  /** The first band sits 2px higher: there is nothing above it to separate it from. */
  first?: boolean;
}) {
  /* Spreads what it is handed onto its own element, because the Chats label is a context-menu
   * trigger and Radix's `asChild` gives the trigger's handlers and ref to its child. */
  return (
    <div
      {...rest}
      className={cn(
        "flex items-center gap-1.5 px-2 pb-1.5 text-[10px] font-semibold tracking-[0.12em] uppercase",
        first ? "pt-2.5" : "pt-3",
        tone === "kith" ? "text-kith" : FAINT,
        className,
      )}
    >
      {children}
    </div>
  );
}

/** One thing waiting on you: a glyph that says which kind, where it is, and why it stopped. */
function NeedCard({
  glyph,
  title,
  why,
  when,
  whenTitle,
  current,
  onClick,
  mono = false,
  tooltip,
  menu,
}: {
  glyph: "?" | "!";
  title: string;
  why: string;
  when: string;
  whenTitle?: string;
  current?: boolean;
  /** Absent for a card with nowhere to go — see `PermissionCard`. */
  onClick?: () => void;
  /** The title is a command or a path, and reads as one. */
  mono?: boolean;
  tooltip?: string;
  /** The conversation's right-click menu, for a card that is one. */
  menu?: MenuProps;
}) {
  const body = (
    <>
      <span className="bg-kith/16 text-kith flex size-5 shrink-0 items-center justify-center rounded-[6px] text-[11px] font-bold">
        {glyph}
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span
          className={cn(
            "truncate",
            mono ? "font-mono text-[12px]" : "text-[13px]",
          )}
        >
          {title}
        </span>
        <span className={cn("truncate text-[11px]", KITH_MUTED)}>{why}</span>
      </span>
      <span
        className={cn("shrink-0 text-[10px] tabular-nums", FAINT)}
        title={whenTitle}
      >
        {when}
      </span>
    </>
  );
  const shape = cn(
    "text-foreground mb-1 flex w-full gap-2.5 rounded-[10px] border p-2 text-left transition-colors",
    current
      ? "bg-kith/14 border-kith/40 shadow-[inset_2px_0_0_var(--kith)]"
      : "bg-kith/7 border-kith/18",
  );
  if (!onClick) {
    return (
      <div className={shape} title={tooltip}>
        {body}
      </div>
    );
  }
  const button = (
    <button
      type="button"
      onClick={onClick}
      aria-current={current ? "page" : undefined}
      title={tooltip}
      className={cn(
        shape,
        !current && "hover:bg-kith/10",
        "focus-visible:ring-ring/50 focus-visible:ring-2 focus-visible:outline-none",
      )}
    >
      {body}
    </button>
  );
  return menu ? <ItemMenu {...menu}>{button}</ItemMenu> : button;
}

/** What he wants, said about the thing itself — for a request no chat can be named for. */
const PERMISSION_WHAT: Record<PermissionRequest["kind"], string> = {
  read: "Wants to read outside his folder",
  write: "Wants to write outside his folder",
  delete: "Wants to delete outside his folder",
  command: "Wants to run a command",
  plugin: "Wants to use a program that is not Kith",
};

/** The same, as a verb to put in front of the command or path — for a request pinned on a chat. */
const PERMISSION_VERB: Record<PermissionRequest["kind"], string> = {
  read: "Wants to read",
  write: "Wants to write to",
  delete: "Wants to delete",
  command: "Wants to run",
  plugin: "Wants to use",
};

/** "Wants to run ffmpeg -i …" — the reason line for a permission, wherever it is shown. */
function permissionLine(request: PermissionRequest): string {
  return `${PERMISSION_VERB[request.kind] ?? "Wants"} ${request.what}`;
}

/** A permission request, which does not itself say which conversation raised it.
 *
 * When `permissionHolder` can say — exactly one live turn is parked and not on a question — the
 * card is that chat's, like the design's: its title, then "Wants to run …", and it takes you
 * there. Otherwise it names the thing itself, the command or the path, and is not a link, because
 * there is nowhere honest to take you: the prompt above the composer, which every chat shows,
 * already has the buttons. */
function PermissionCard({
  request,
  chat,
  current,
  onOpen,
  menu,
}: {
  request: PermissionRequest;
  chat?: ConversationSummary;
  current?: boolean;
  onOpen: (id: string) => void;
  menu?: MenuProps;
}) {
  const at = request.at * 1000;
  const tooltip = `${request.what}\n${request.why}\nAnswer it above the composer.`;
  if (chat) {
    return (
      <NeedCard
        glyph="!"
        title={rowTitle(chat.title)}
        why={permissionLine(request)}
        when={ago(at)}
        whenTitle={new Date(at).toLocaleString()}
        tooltip={tooltip}
        current={current}
        onClick={() => onOpen(chat.id)}
        menu={menu}
      />
    );
  }
  return (
    <NeedCard
      glyph="!"
      title={request.what}
      mono
      why={PERMISSION_WHAT[request.kind] ?? "Wants your yes"}
      when={ago(at)}
      whenTitle={new Date(at).toLocaleString()}
      tooltip={tooltip}
    />
  );
}

/** A project's own row: fold, name, what is happening inside, and how far along it is. */
function ProjectRow({
  group,
  open,
  progress: known,
  asking,
  permitting,
  working,
  onToggle,
  onNewChat,
  onOpenProjects,
}: {
  group: Group;
  open: boolean;
  /** Present only for an open project whose board has answered — see `fetchProjectProgress`. */
  progress?: Progress;
  /** How many of its conversations are parked on a question, on a permission, and live — from
   *  the same `stateOf` its rows are drawn from, so a badge and a row cannot disagree. */
  asking: number;
  permitting: number;
  working: number;
  onToggle: () => void;
  onNewChat: () => void;
  onOpenProjects: () => void;
}) {
  /* The number on the right answers the question the row's state raises. Folded, it is how many
   * conversations are behind it — the reason to open it. Open, those are on screen, so it becomes
   * how far the project has got: tasks done of the ones not dropped, the same "6/13" the session
   * bar shows. A project with no tasks keeps its conversation count. */
  const progress = open && known ? known : null;
  return (
    // Sticky, so the project you are looking at is named while you are inside it. The rail's own
    // background under it, so the rows scrolling past do not show through.
    <h3 className="group/heading bg-sidebar sticky top-0 z-10">
      {/* Right-click a project to start a conversation in it. The binding is written once the
          conversation has an id; see workspace's `pendingProject`. */}
      <ItemMenu
        title={group.label}
        actions={[
          { label: "New chat here", hint: group.label, onSelect: onNewChat },
          { label: "Open in Projects", onSelect: onOpenProjects },
        ]}
      >
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className={cn(
            ROW,
            "hover:bg-accent/50 items-center gap-2 px-2 py-1.5 text-[13px] font-normal",
            open
              ? "text-foreground"
              : group.finished
                ? "text-muted-foreground/70"
                : "text-foreground/80",
          )}
        >
          <ChevronRight
            className={cn(
              "size-3 shrink-0 transition-transform",
              FAINT,
              open && "rotate-90",
            )}
            aria-hidden
          />
          <span className="min-w-0 flex-1 truncate" title={group.label}>
            {group.label}
          </span>
          {/* A dot, not the word. "done" and "paused" spelled out beside a truncated name were
              two things fighting for the same inch; the state matters, its spelling does not. */}
          {group.status ? (
            <>
              <span
                aria-hidden
                title={group.status}
                className="bg-muted-foreground/40 size-1.5 shrink-0 rounded-full"
              />
              <span className="sr-only">{group.status}</span>
            </>
          ) : null}
          {/* What is happening inside, whether or not you can see the rows — and counted, because
              "one of these is waiting" and "four of them are" are different sizes of problem. */}
          {asking + permitting > 0 ? (
            <Badge
              tone="kith"
              title={[
                asking ? `${asking} waiting for your answer` : "",
                permitting ? `${permitting} waiting for your yes` : "",
              ]
                .filter(Boolean)
                .join(", ")}
            >
              {asking + permitting}
            </Badge>
          ) : null}
          {working > 0 ? (
            <Badge tone="roam" title={`${working} still working`}>
              {working}
            </Badge>
          ) : null}
          <span
            className={cn(
              "shrink-0 text-[11px] tabular-nums transition-opacity group-focus-within/heading:opacity-0 group-hover/heading:opacity-0",
              FAINT,
            )}
            title={
              progress
                ? `${progress.done} of ${progress.total} tasks done`
                : `${group.items.length} ${group.items.length === 1 ? "conversation" : "conversations"}`
            }
          >
            {progress
              ? `${progress.done}/${progress.total}`
              : group.items.length}
          </span>
        </button>
      </ItemMenu>
      {/* Start a chat in this project, from the project. A sibling of the toggle, not a control
          inside it — a button cannot contain one — laid over the count so revealing it does not
          shove the row sideways. Revealed on hover *and* on its own focus, because hover-only is
          how a control becomes unreachable by keyboard. */}
      <button
        type="button"
        onClick={onNewChat}
        title={`New chat in ${group.label}`}
        aria-label={`New chat in ${group.label}`}
        className="text-muted-foreground hover:text-foreground hover:bg-accent focus-visible:ring-ring/50 absolute top-1/2 right-1 -translate-y-1/2 rounded-[6px] p-1 opacity-0 transition group-focus-within/heading:opacity-100 group-hover/heading:opacity-100 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:outline-none"
      >
        <Plus className="size-3.5" />
      </button>
    </h3>
  );
}

function Badge({
  tone,
  title,
  children,
}: {
  tone: "kith" | "roam";
  title: string;
  children: React.ReactNode;
}) {
  return (
    <span
      title={title}
      className={cn(
        "flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full px-[5px] text-[10px] font-bold tabular-nums",
        tone === "kith" ? "bg-kith/16 text-kith" : "bg-roam/16 text-roam",
      )}
    >
      {children}
    </span>
  );
}

type RowState =
  | { kind: "waiting"; question: string }
  | { kind: "permission"; request: PermissionRequest }
  | { kind: "working" }
  | { kind: "agents"; count: number }
  | { kind: "draft" }
  | { kind: "idle" };

/** The dot a row carries for its state, and what it says when you hover it.
 *
 * The draft ring is hollow and still against the others' solid breathing, which is the whole of
 * how it reads as a different kind of fact: amber is "waiting on you", green is "running", teal
 * is agents at work under it, and something you typed and did not send is none of those. */
function dotFor(state: RowState): { look: string; title: string } {
  switch (state.kind) {
    case "waiting":
      return { look: cn("bg-kith", BREATHE), title: "Waiting for your answer" };
    case "permission":
      return { look: cn("bg-kith", BREATHE), title: "Waiting for your yes" };
    case "working":
      return {
        look: cn("bg-roam", BREATHE),
        title: "Still working in this conversation",
      };
    case "agents":
      return {
        look: cn(TEAL_DOT, BREATHE),
        title: `${agentCount(state.count)} working under this chat`,
      };
    case "draft":
      return {
        look: "border-muted-foreground/70 border",
        title: "You typed something here and did not send it",
      };
    default:
      return { look: "", title: "" };
  }
}

function StateDot({
  state,
  className,
}: {
  state: RowState;
  className?: string;
}) {
  const { look, title } = dotFor(state);
  return (
    <span
      aria-hidden={state.kind === "idle" ? true : undefined}
      title={title || undefined}
      className={cn("size-1.5 shrink-0 rounded-full", look, className)}
    />
  );
}

/** A project row's second line: its present tense, and the colour that tense is said in.
 *
 * His last word is the default — where the conversation was left — and any live state replaces
 * it, because the question he is waiting on is what you need from the row more than what he said
 * last. Agents working under it do not: the fold beneath the row already counts them, and saying
 * it twice would cost the row the one line that says where the conversation itself was left. */
function statusLine(
  item: ConversationSummary,
  state: RowState,
): { text: string; tone: string } {
  switch (state.kind) {
    case "waiting":
      return {
        text: state.question
          ? `Asking you · ${state.question}`
          : "Waiting for your answer",
        tone: KITH_MUTED,
      };
    case "permission":
      return { text: permissionLine(state.request), tone: KITH_MUTED };
    case "working":
      return { text: "Working", tone: "text-roam" };
    default:
      return { text: item.lastSaid, tone: FAINT };
  }
}

/** A conversation inside the open project: its state dot, what it is, and where it stands. */
function ProjectChatRow({
  item,
  menu,
  state,
  active,
  onClick,
}: {
  item: ConversationSummary;
  menu: MenuProps;
  state: RowState;
  active: boolean;
  onClick: () => void;
}) {
  const sub = statusLine(item, state);
  return (
    <ItemMenu {...menu}>
      <button
        type="button"
        onClick={onClick}
        aria-current={active ? "page" : undefined}
        title={`${dayLabel(item.updatedAt)} ${time(item.updatedAt)}`}
        className={cn(
          ROW,
          "items-start gap-2 py-1.5 pr-2 pl-7",
          active
            ? "bg-accent text-foreground"
            : "text-foreground/80 hover:bg-accent/50",
        )}
      >
        <StateDot state={state} className="mt-[7px]" />
        <span className="flex min-w-0 flex-1 flex-col gap-px">
          <span className="truncate text-[13px]">{rowTitle(item.title)}</span>
          {sub.text ? (
            <span className={cn("truncate text-[11px]", sub.tone)}>
              {sub.text}
            </span>
          ) : null}
        </span>
      </button>
    </ItemMenu>
  );
}

/** A conversation in no project: one line, what it is and when it last moved. */
function ChatRow({
  item,
  menu,
  state,
  active,
  onClick,
}: {
  item: ConversationSummary;
  menu: MenuProps;
  state: RowState;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <ItemMenu {...menu}>
      <button
        type="button"
        onClick={onClick}
        aria-current={active ? "page" : undefined}
        className={cn(
          ROW,
          "items-baseline gap-2 px-2 py-1.5",
          active
            ? "bg-accent text-foreground"
            : "text-foreground/80 hover:bg-accent/50",
        )}
      >
        <span className="min-w-0 flex-1 truncate text-[13px]">
          {rowTitle(item.title)}
        </span>
        {/* The same states as a project's rows, as a dot beside the stamp — absent when there is
          nothing to say, which is nearly always, so the row stays a title and a day. */}
        {state.kind !== "idle" ? (
          <StateDot state={state} className="self-center" />
        ) : null}
        <span
          className={cn("shrink-0 text-[11px] tabular-nums", FAINT)}
          title={`${dayLabel(item.updatedAt)} ${time(item.updatedAt)}`}
        >
          {stamp(item.updatedAt)}
        </span>
      </button>
    </ItemMenu>
  );
}

/* ── Agents under a chat ──────────────────────────────────────────────── */

/** Where a row's text starts, so the fold under it lines up with it: a loose chat's title, and a
 *  project chat's state dot. */
const CHAT_ROW_INDENT = 8;
const PROJECT_ROW_INDENT = 28;

/**
 * The agents a chat started, folded under its row: "3 agents · 2 working", and the tree when
 * opened.
 *
 * Under the row rather than as rows, because an agent is not a conversation you have — you never
 * type into one — but it is one you will want to read, and the chat that started it is where you
 * will look. The count comes with the listing; the tree is asked for only when it is opened, and
 * kept current by the `agents` change like every other tree.
 *
 * Exported for its test.
 */
export function AgentsUnder({
  item,
  indent,
  open,
  onToggle,
  activeId,
  onOpen,
}: {
  item: ConversationSummary;
  indent: number;
  open: boolean;
  onToggle: () => void;
  activeId: string;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="flex flex-col">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        title={open ? "Fold its agents away" : "Show the agents this chat started"}
        className={cn(
          ROW,
          "hover:bg-accent/40 hover:text-foreground items-center gap-1.5 py-0.5 pr-2 text-[11px]",
          FAINT,
        )}
        style={{ paddingInlineStart: indent }}
      >
        <ChevronRight
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")}
          aria-hidden
        />
        <span className="truncate tabular-nums">
          {agentCount(item.agents)}
          {item.agentsWorking > 0 ? (
            <>
              {" · "}
              <span className={TEAL}>{item.agentsWorking} working</span>
            </>
          ) : null}
        </span>
      </button>
      {open ? (
        <AgentList root={item.id} indent={indent} activeId={activeId} onOpen={onOpen} />
      ) : null}
    </div>
  );
}

/** The tree itself: one row per agent, stepped in by depth. Closed ones wait behind a toggle —
 *  they are finished and merged, and a list of every agent ever is not what the fold is for. */
function AgentList({
  root,
  indent,
  activeId,
  onOpen,
}: {
  root: string;
  indent: number;
  activeId: string;
  onOpen: (id: string) => void;
}) {
  const [closed, setClosed] = useState(false);
  const { data, isPending } = useQuery({
    queryKey: keys.agentTree(root),
    queryFn: () => fetchAgentTree(root),
  });
  const nodes = data?.agents ?? [];
  const rows = flattenAgents(nodes, closed);
  const hidden = countAgents(nodes).closed;
  const start = indent + 12;
  if (isPending) return null;
  return (
    <ul aria-label="Its agents" className="flex flex-col gap-px pb-1">
      {rows.map(({ node, depth }) => {
        const look = agentLook(node);
        const active = node.id === activeId;
        return (
          <li key={node.id}>
            <button
              type="button"
              onClick={() => onOpen(node.id)}
              aria-current={active ? "page" : undefined}
              title={`${node.name} — ${AGENT_WORD[look]}`}
              className={cn(
                ROW,
                "items-start gap-2 py-1 pr-2",
                active ? "bg-accent text-foreground" : "text-foreground/80 hover:bg-accent/50",
                look === "closed" && !active && "opacity-60",
              )}
              style={{ paddingInlineStart: start + depth * AGENT_INDENT }}
            >
              <AgentDot look={look} className="mt-[6px]" />
              <span className="flex min-w-0 flex-1 flex-col gap-px">
                <span className="truncate text-[12.5px]">{node.name}</span>
                {node.lastSaid ? (
                  <span className={cn("truncate text-[11px]", FAINT)}>{node.lastSaid}</span>
                ) : null}
              </span>
            </button>
          </li>
        );
      })}
      {hidden ? (
        <li>
          <button
            type="button"
            onClick={() => setClosed((was) => !was)}
            aria-pressed={closed}
            className={cn(ROW, "hover:text-foreground py-0.5 pr-2 text-[11px]", FAINT)}
            style={{ paddingInlineStart: start }}
          >
            {closed ? "Hide closed" : `Show ${hidden} closed`}
          </button>
        </li>
      ) : null}
    </ul>
  );
}

/* ── Project progress ─────────────────────────────────────────────────── */

interface Progress {
  done: number;
  total: number;
}

/** One project's tasks, counted: done, of every task not dropped. Zero on any failure — the row
 *  then keeps its conversation count, which is a smaller loss than a rail that will not draw. */
async function fetchProjectProgress(projectId: number): Promise<Progress> {
  try {
    const response = await fetch(`/api/projects/${projectId}/tasks`);
    if (!response.ok) return { done: 0, total: 0 };
    const body = (await response.json()) as {
      tasks?: { status?: string }[];
    } | null;
    const tasks = body?.tasks ?? [];
    return {
      done: tasks.filter((one) => one.status === "done").length,
      total: tasks.filter((one) => one.status !== "dropped").length,
    };
  } catch {
    return { done: 0, total: 0 };
  }
}

/* ── Grouping ───────────────────────────────────────────────────────────── */

interface Group {
  key: string;
  label: string;
  /** The project this band is, or null for the loose chats — what "start a chat in here" needs. */
  projectId: number | null;
  /** A project, or the loose chats. They are not two projects: one is a folder of work, the
   *  other is most of what anyone says to him, and the rail draws them as different things. */
  kind: "project" | "chats";
  /** Shown beside the name when the project is not active — being bound to a closed one is
   *  *why* nothing is happening in these sessions, which is the thing worth knowing. */
  status?: string;
  /** Done, paused, archived. Sorted after the live ones and rendered quietly. */
  finished: boolean;
  items: ConversationSummary[];
}

/**
 * Group the listing by the project each session is working on.
 *
 * By project rather than by day. A day is how you'd look for something if you remembered when it
 * happened; a project is how you look for it when you remember what it was about. The day each
 * session last moved is on its own row instead, so nothing is lost by dropping date headings.
 *
 * "Chats" is a real group, not an empty state: a session for a one-off errand is meant to stay
 * unbound.
 */
function groupByProject(
  items: ConversationSummary[],
  projects: Project[],
): Group[] {
  const known = new Map(projects.map((project) => [project.id, project]));
  const groups = new Map<string, Group>();
  for (const item of items) {
    /* A project that no longer exists is not a project. The server clears dangling bindings at
     * the source (repositories/projects.py, migration v41); this is the panel refusing to invent
     * a name from a number in any case — but only once the projects have actually loaded, or
     * every id would be unknown on the first render and the whole list would rearrange a beat
     * after it appeared. */
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
        // better answer than a blank heading. "Chats" rather than "No project": talking to him
        // without a project is not a filing failure; it is most of what the app is for.
        label: id === null ? "Chats" : (project?.name ?? `Project #${id}`),
        kind: id === null ? "chats" : "project",
        projectId: id,
        status:
          project && project.status !== "active" ? project.status : undefined,
        finished: Boolean(project && project.status !== "active"),
        items: [],
      };
      groups.set(key, group);
    }
    group.items.push(item);
  }
  // Insertion order inside each band is already most-recently-active first, because `items`
  // arrives newest first. Three bands, in the order of "where is the thing I want": what is
  // live, then where most conversations are, then what is over.
  const all = [...groups.values()];
  return [
    ...all.filter((g) => g.kind === "project" && !g.finished),
    ...all.filter((g) => g.kind === "chats"),
    ...all.filter((g) => g.kind === "project" && g.finished),
  ];
}

/* ── Time ───────────────────────────────────────────────────────────────── */

/** Midnight-to-midnight days between then and now — the same comparison `dayLabel` makes, so a
 *  daylight-saving night does not put "Mon" on a Tuesday. */
function daysAgo(at: Date): number {
  const midnight = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((midnight(new Date()) - midnight(at)) / 86_400_000);
}

/**
 * The whole stamp in the space of one short word: the clock for today, the weekday within the
 * week, then the date — "7:46 PM", "Tue", "Sep 18".
 *
 * Short on purpose. "Wednesday" and "September 18" took a third of a 264px row from the title
 * beside them, and the long form is in the tooltip for when it is the thing you want.
 */
function stamp(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const days = daysAgo(at);
  if (days <= 0) return time(iso);
  if (days < 7) return at.toLocaleDateString(undefined, { weekday: "short" });
  if (at.getFullYear() === new Date().getFullYear())
    return at.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return at.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** How long something has been waiting on you — "now", "5m", "2h", "3d". A card that needs you
 *  is measured from when it started needing you, not placed on a calendar. */
function ago(ms: number): string {
  if (!Number.isFinite(ms)) return "";
  const minutes = Math.floor(Math.max(0, Date.now() - ms) / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/** "Which of three products?" reads as "Asking · which of three products?" — but "I", "API" and
 *  "Asterion" keep their capital, so only a capital followed by a lowercase letter is lowered. */
function lowerFirst(text: string): string {
  return /^[A-Z][a-z]/.test(text) && !/^I\b/.test(text)
    ? text[0]!.toLowerCase() + text.slice(1)
    : text;
}
