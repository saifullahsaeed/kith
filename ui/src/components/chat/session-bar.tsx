import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Folder, ListChecks, Search } from "lucide-react";

import { Dropdown } from "@/components/ui/dropdown";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { ThreadFind } from "@/components/chat/thread-find";
import { fetchConversations, setConversationProject } from "@/lib/backend";
import { fetchBrain, type Project } from "@/lib/backend/brain";
import { Go } from "@/lib/go";
import { keys } from "@/lib/query-keys";
import { pathForTask } from "@/lib/router";
import { cn } from "@/lib/utils";
import { Layer, useLayer } from "@/hooks/use-layer";

const NONE = "none";

/**
 * What this conversation is working on.
 *
 * Both used to be global and both were the same bug. A session's project was guessed as "the
 * only active project with a folder" — right until there were two, and two at once is the
 * whole point of sessions. Continuing was one switch over one board: on meant every open task
 * anywhere was fair game, off meant nothing happened at all.
 *
 * So this bar is the session, made visible. Switch conversations and the project switches
 * with you; each stays where you left it. It sits above the thread
 * rather than in the app header deliberately — the header is about Kith, and this is about
 * the piece of work in front of you.
 *
 * One 44px row, as the design draws the thread's header: the project, a slash, the conversation's
 * title — then, at the far end, the task this chat is working (when it is working one) and find.
 * The title is here because this row is the only thing above the thread that names *this*
 * conversation; the tab above it is a truncated copy, and a split pane has no tab of its own.
 */
export function SessionBar({
  conversationId,
  projectId,
  agent = "",
  onProject,
}: {
  conversationId: string;
  projectId: number | null;
  /** The agent's name, when this conversation is one. It is its title here — the listing that
   *  titles the rest does not hold agents — and it has no project picker: an agent works in its
   *  handler's project, which was set for it when it was started. */
  agent?: string;
  onProject: (projectId: number | null) => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [busy, setBusy] = useState(false);
  const layer = useLayer(Layer.Base);
  const [finding, setFinding] = useState(false);
  // Bumped on every ⌘F so the bar can be told to take focus again when it is already open,
  // which a boolean cannot express — `finding` is already true, so nothing re-renders and the
  // second press would do nothing at all.
  const [focusFind, setFocusFind] = useState(0);

  const load = useCallback(() => {
    fetchBrain()
      .then((brain) =>
        // Active ones, plus whichever this session is actually bound to whatever its state.
        // Filtering to active alone meant a project that completed itself vanished from the
        // list while the session was still on it — and with nothing matching the id, the
        // picker fell back to rendering the raw value, so the bar read "1". Which is both
        // useless and actively misleading: it looks like a count, and the one thing you
        // needed to know was that the project had closed under you.
        setProjects(
          brain.projects.filter((p) => p.status === "active" || p.id === projectId),
        ),
      )
      .catch(() => {});
  }, [projectId]);

  // Reloaded when the session changes as well as on mount: he starts projects himself
  // mid-conversation, so the list a picker was opened with goes stale within a turn.
  useEffect(load, [load, conversationId, projectId]);

  const title = useConversationTitle(conversationId) || agent;
  const task = useWorkingTask(conversationId);

  /* ⌘F opens the find bar, and takes the key off the browser to do it.
   *
   * That is a fix rather than a preference. Every message carries `content-visibility: auto`
   * with a guessed height, so native find-in-page has to reveal each subtree as it reaches it
   * and its scrolling lands wrong — the same estimate problem the viewport's `autoScroll` note
   * describes. It also searches this bar, the panels and the composer, none of which are the
   * conversation.
   *
   * Not while anything is over it — a dialog, a menu, the Settings page. This bar stays
   * mounted underneath all of them, so without that test ⌘F in Settings was swallowed here
   * and opened a find bar over a conversation nobody could see. See `useLayer`. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "f" || !(event.metaKey || event.ctrlKey) || event.altKey) return;
      if (!conversationId) return;
      if (!layer.frontmost()) return;
      event.preventDefault();
      setFinding(true);
      setFocusFind((n) => n + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [conversationId]);

  /* Shown on an empty chat too, which it was not.
   *
   * The reason it hid was sound at the time: an empty chat had no session to describe, and a
   * disabled control on a blank screen is noise. Both halves stopped being true. A chat started
   * from a project's + already *has* a project before its first word — held in `pendingProject`
   * and written the moment the turn returns an id — so there was something to say and nothing
   * said it; and picking the project *before* typing is now the obvious thing to want, which
   * needs a control rather than a label.
   *
   * The find bar still needs a conversation to search, so ⌘F stays guarded where it is bound. */
  const current = projects.find((p) => p.id === projectId) ?? null;
  const choose = async (value: string) => {
    const next = value === NONE ? null : Number(value);
    // Nothing to write it against yet. The choice is held by the workspace and written when the
    // first turn names the conversation — the same path "New chat here" uses.
    if (!conversationId) {
      onProject(next);
      return;
    }
    setBusy(true);
    try {
      await setConversationProject(conversationId, next);
      onProject(next);
    } finally {
      setBusy(false);
    }
  };

  /* One row either way, and that is the point of swapping rather than stacking. A find bar on a
   * second row pushes the whole thread down the moment it opens and pulls it back up when it
   * closes, so the thing you were reading moves while you are trying to search it. Nothing here
   * is lost while it is open: the project is fixed for the conversation's life, the counts are a
   * glance, and Stop is one Escape away. */
  if (finding) {
    return (
      <div className="border-border/80 flex h-11 shrink-0 items-center gap-2 border-b px-5">
        <ThreadFind focusSignal={focusFind} onClose={() => setFinding(false)} />
      </div>
    );
  }

  const counts = current
    ? `${current.tasks_active} open · ${current.tasks_done}/${current.tasks_total - current.tasks_dropped} done`
    : "";

  return (
    <div className="border-border/80 flex h-11 shrink-0 items-center gap-2.5 border-b px-5">
      {agent && !projectId ? null : projectId && conversationId ? (
        // Locked, not merely disabled: this isn't "you could, but not right now" — a
        // conversation is stuck with the first project it picks for the rest of its life,
        // so there is nothing here to click at all. A dropdown that silently refuses every
        // selection reads as broken; a label reads as what it is.
        <span
          className="text-foreground/75 flex max-w-[16rem] min-w-0 shrink-0 items-center gap-1.5 text-[13px]"
          title={
            "This conversation is working on this project for good — start a new conversation for something else." +
            (counts ? `\n${counts}` : "")
          }
        >
          <Folder className="size-3.5 shrink-0" aria-hidden />
          <span className="truncate">
            {current ? (
              current.status === "active" ? (
                current.name
              ) : (
                <>
                  {current.name} <span className="text-muted-foreground/70">· {current.status}</span>
                </>
              )
            ) : (
              `Project #${projectId}`
            )}
          </span>
        </span>
      ) : (
        <span className="flex shrink-0 items-center gap-1.5">
          <Folder
            className={cn("size-3.5 shrink-0", current ? "text-foreground/75" : "text-muted-foreground/60")}
            aria-hidden
          />
          <Dropdown
            variant="bare"
            ariaLabel="What this conversation will work on"
            className={cn("min-w-[8rem] max-w-[16rem] shrink-0 text-[13px]", busy && "opacity-60")}
            value={projectId ? String(projectId) : NONE}
            onChange={(value) => void choose(value)}
            options={[
              // "No project" is a real choice, not an empty state: a session for a one-off
              // errand should stay unbound. Picking an actual project below is the one-way
              // door — there is no unbinding a conversation once it has chosen.
              { value: NONE, label: <span className="text-muted-foreground">No project</span> },
              ...projects.map((project) => ({
                value: String(project.id),
                label: project.name,
              })),
            ]}
          />
        </span>
      )}
      {agent && !projectId ? null : (
        <span className="text-muted-foreground/45 shrink-0" aria-hidden>
          /
        </span>
      )}
      <span className="text-foreground min-w-0 truncate text-[13px] font-semibold" title={title}>
        {title || "New chat"}
      </span>

      <div className="flex-1" />

      {/* The task this chat is working, one click from its page. Only while a task is
          `working` — the same rule the Work panel's card follows, off the same query, so the two
          can never disagree about whether there is one. */}
      {task ? (
        <Go>
          {(go) => (
            <button
              type="button"
              onClick={() => go(pathForTask(task.id))}
              title={task.goal}
              className="border-border bg-muted/70 text-foreground/75 hover:text-foreground flex h-[26px] max-w-[22rem] min-w-0 shrink items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors"
            >
              <ListChecks className="size-3 shrink-0" aria-hidden />
              <span className="shrink-0 tabular-nums">#{task.id}</span>
              <span className="min-w-0 truncate">{task.goal}</span>
              {task.total ? (
                <span className="shrink-0 tabular-nums">
                  · {task.done}/{task.total}
                </span>
              ) : null}
            </button>
          )}
        </Go>
      ) : null}

      {/* Gone entirely on an empty chat — there is nothing yet to search. ⌘F is guarded the
          same way. */}
      {conversationId ? (
        <TooltipIconButton
          tooltip="Find in this conversation (⌘F)"
          side="bottom"
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Find in this chat"
          className="text-muted-foreground hover:text-foreground size-[30px] shrink-0 rounded-lg [&_svg]:size-[15px]"
          onClick={() => {
            setFinding(true);
            setFocusFind((n) => n + 1);
          }}
        >
          <Search />
        </TooltipIconButton>
      ) : null}
    </div>
  );
}

/** The conversation's title, from the same list the sidebar and the tabs read — a cache read
 *  rather than a request of its own. `""` for a chat that has not been named yet. */
function useConversationTitle(conversationId: string): string {
  const { data: known = [] } = useQuery({
    queryKey: keys.conversations(TITLE_LIMIT, ""),
    queryFn: async () => (await fetchConversations(TITLE_LIMIT)).conversations ?? [],
    staleTime: 30_000,
    enabled: Boolean(conversationId),
  });
  if (!conversationId) return "";
  return known.find((one) => one.id === conversationId)?.title ?? "";
}

/** The same page size the workspace reads titles with, so this shares its cache entry. */
const TITLE_LIMIT = 200;

interface WorkingTask {
  id: number;
  goal: string;
  done: number;
  total: number;
}

/**
 * The task this conversation is working, if any — the Work panel's `WorkingOn` query, under the
 * same key and with the same request, so the cache holds one answer and a `task` event refreshes
 * both at once. Only `working` tasks come back: the server clears the stamp when a task stops.
 */
function useWorkingTask(conversationId: string): WorkingTask | null {
  const { data = null } = useQuery({
    queryKey: keys.workingOn(conversationId),
    queryFn: async () => {
      const response = await fetch(`/api/chat/${conversationId}/working-on`);
      if (!response.ok) return null;
      const body = (await response.json()) as {
        id?: number;
        goal?: string;
        status?: string;
        checklist?: { done: boolean | number }[];
      } | null;
      return body && body.id ? body : null;
    },
    enabled: Boolean(conversationId),
  });
  if (!data || !data.id) return null;
  const items = data.checklist ?? [];
  return {
    id: data.id,
    goal: data.goal ?? "",
    done: items.filter((one) => Boolean(one.done)).length,
    total: items.length,
  };
}
