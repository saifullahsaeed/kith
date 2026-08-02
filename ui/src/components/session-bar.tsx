import { useCallback, useEffect, useState } from "react";
import { FolderKanban, Play, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown";
import { setConversationProject } from "@/lib/backend";
import { fetchBrain, type Project } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";

const NONE = "none";

/**
 * What this conversation is working on, and whether he keeps going in it.
 *
 * Both used to be global and both were the same bug. A session's project was guessed as "the
 * only active project with a folder" — right until there were two, and two at once is the
 * whole point of sessions. Continuing was one switch over one board: on meant every open task
 * anywhere was fair game, off meant nothing happened at all.
 *
 * So this bar is the session, made visible. Switch conversations and the project switches
 * with you; leave one working and the other stays where you left it. It sits above the thread
 * rather than in the app header deliberately — the header is about Kith, and this is about
 * the piece of work in front of you.
 *
 * It draws nothing at all until a conversation exists. An empty chat has no session to
 * describe, and a disabled control on a blank screen is just noise.
 */
export function SessionBar({
  conversationId,
  projectId,
  working,
  onProject,
  onKeepWorking,
  onStop,
}: {
  conversationId: string;
  projectId: number | null;
  working: boolean;
  onProject: (projectId: number | null) => void;
  onKeepWorking: () => void;
  onStop: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [busy, setBusy] = useState(false);

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

  if (!conversationId) return null;

  const current = projects.find((p) => p.id === projectId) ?? null;
  const choose = async (value: string) => {
    const next = value === NONE ? null : Number(value);
    setBusy(true);
    try {
      await setConversationProject(conversationId, next);
      onProject(next);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-2 border-b border-border/60 px-4 py-1.5">
      <FolderKanban
        className={cn("size-3.5 shrink-0", current ? "text-blue-400/80" : "text-muted-foreground/50")}
      />
      <Dropdown
        variant="bare"
        ariaLabel="What this conversation is working on"
        className={cn("min-w-[9rem] max-w-[18rem] shrink-0", busy && "opacity-60")}
        value={projectId ? String(projectId) : NONE}
        onChange={(value) => void choose(value)}
        options={[
          // "No project" is a real choice, not an empty state: a session for a one-off
          // errand should stay unbound, and unbinding is how you correct a wrong guess.
          { value: NONE, label: <span className="text-muted-foreground">No project</span> },
          ...projects.map((project) => ({
            value: String(project.id),
            // A closed project says so, because being bound to one is why nothing is
            // happening: he is not allowed to pick up its tasks, however many are ready.
            label:
              project.status === "active" ? (
                project.name
              ) : (
                <span>
                  {project.name}{" "}
                  <span className="text-muted-foreground/70">· {project.status}</span>
                </span>
              ),
          })),
        ]}
      />
      {current ? (
        <span className="text-muted-foreground/50 hidden truncate text-[11px] sm:block">
          {current.tasks_active} open · {current.milestones_done}/{current.milestones_total}{" "}
          milestones
        </span>
      ) : null}

      <div className="flex-1" />

      {/* Per session, which is the thing roaming could never say. Stopping this one leaves
          every other session exactly as it was. */}
      {working ? (
        <Button
          size="sm"
          variant="outline"
          className="h-6 gap-1 px-2 text-[11px]"
          onClick={onStop}
          title="Stop taking steps in this conversation. Other sessions keep going."
        >
          <Square className="size-3" />
          Stop
        </Button>
      ) : (
        <Button
          size="sm"
          variant="ghost"
          className="text-muted-foreground hover:text-foreground h-6 gap-1 px-2 text-[11px]"
          onClick={onKeepWorking}
          title="Keep working here until the work is done or you stop him."
        >
          <Play className="size-3" />
          Keep working
        </Button>
      )}
    </div>
  );
}
