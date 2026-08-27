import { useCallback, useEffect, useState } from "react";
import { FolderKanban, Search } from "lucide-react";

import { Dropdown } from "@/components/ui/dropdown";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { ThreadFind } from "@/components/chat/thread-find";
import { setConversationProject } from "@/lib/backend";
import { fetchBrain, type Project } from "@/lib/backend/brain";
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
 * It draws nothing at all until a conversation exists. An empty chat has no session to
 * describe, and a disabled control on a blank screen is just noise.
 */
export function SessionBar({
  conversationId,
  projectId,
  onProject,
}: {
  conversationId: string;
  projectId: number | null;
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
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-1.5">
        <ThreadFind focusSignal={focusFind} onClose={() => setFinding(false)} />
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 border-b border-border/60 px-4 py-1.5">
      <FolderKanban
        className={cn("size-3.5 shrink-0", current ? "text-blue-400/80" : "text-muted-foreground/50")}
      />
      {projectId && conversationId ? (
        // Locked, not merely disabled: this isn't "you could, but not right now" — a
        // conversation is stuck with the first project it picks for the rest of its life,
        // so there is nothing here to click at all. A dropdown that silently refuses every
        // selection reads as broken; a label reads as what it is.
        <span
          className="min-w-[9rem] max-w-[18rem] shrink-0 truncate text-sm"
          title="This conversation is working on this project for good — start a new conversation for something else."
        >
          {current ? (
            current.status === "active" ? (
              current.name
            ) : (
              <>
                {current.name}{" "}
                <span className="text-muted-foreground/70">· {current.status}</span>
              </>
            )
          ) : (
            `Project #${projectId}`
          )}
        </span>
      ) : (
        <Dropdown
          variant="bare"
          ariaLabel="What this conversation will work on"
          className={cn("min-w-[9rem] max-w-[18rem] shrink-0", busy && "opacity-60")}
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
      )}
      {current ? (
        <span className="text-muted-foreground/50 hidden truncate text-[11px] sm:block">
          {current.tasks_active} open · {current.milestones_done}/{current.milestones_total}{" "}
          milestones
        </span>
      ) : null}

      <div className="flex-1" />

      {/* Left of Stop, so the destructive-ish control keeps the end of the row it already has
          and does not move when this appears. Gone entirely on an empty chat — the bar shows
          there now, and there is nothing yet to search. ⌘F is guarded the same way. */}
      {conversationId ? (
        <TooltipIconButton
          tooltip="Find in this conversation (⌘F)"
          side="bottom"
          type="button"
          variant="ghost"
          size="icon"
          className="size-6 shrink-0 rounded-full"
          onClick={() => {
            setFinding(true);
            setFocusFind((n) => n + 1);
          }}
        >
          <Search className="size-3.5" />
        </TooltipIconButton>
      ) : null}
    </div>
  );
}
