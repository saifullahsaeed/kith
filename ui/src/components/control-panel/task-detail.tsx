/**
 * One task, in full: what it is, where it stands, and the one decision on it that is yours.
 *
 * Drawn from the "Task" board of the 2026-09-30 control-panel design. The page reads top to
 * bottom in the order you would ask about a task:
 *
 *   1. Where it is — the project and its id, and J / K through that project's other waiting plans.
 *   2. What it is — the goal, as the title (still renamed in place).
 *   3. Where it stands — a stepper through the four columns of the board, Planning → Done.
 *   4. What it needs from you — when a plan is waiting, an amber bar with Approve on it. Approving
 *      is the one move that is yours; he carries it the rest of the way.
 *   5. The detail — description, plan, checklist, what he filed, and what happened, with the
 *      task's attributes in a rail on the right and a composer pinned under it all.
 *
 * Everything that page did before is still here — rename, edit the brief, tick / add / delete a
 * step, preview / download / delete a deliverable, the design editor and the board of designs —
 * restyled to the design, plus the moves the design added: approve, request changes, drop.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  ArrowUp,
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ClipboardCheck,
  Copy,
  Download,
  Expand,
  FileClock,
  FileText,
  Folder,
  Link2,
  PanelsTopLeft,
  Plus,
  Trash2,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { useConfirm } from "@/components/ui/confirm";
import { FileViewer, Markdown, MarkdownInline, skipTextRead } from "@/components/files";
import { EditableText } from "@/components/ui/editable-text";
import { DesignPage } from "@/components/design/design-page";
import { DesignBoardPage } from "@/components/design/design-board-page";
import { Layer, useLayer } from "@/hooks/use-layer";
import { dayLabel, time } from "@/lib/dates";
import { looksLikeADesign } from "@/lib/design/detect";
import { copyText, openWorkspaceFile } from "@/lib/files";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import {
  createBrainItem,
  deleteBrainItem,
  fetchBrain,
  fetchTaskDetail,
  fetchWorkspaceFile,
  updateBrainItem,
  type Deliverable,
  type TaskDetail,
} from "@/lib/backend/brain";
import { BUTTON, Kbd, LiveDot, Segmented } from "./chrome";
import { shortPath } from "./format";
import { sendInNewChat } from "./start-chat";
import {
  LONG_TABLE,
  chatAbout,
  namedTasks,
  parsePlan,
  plain,
  previewPlan,
  shortTitle,
  size,
  stepThrough,
  stepWindow,
  stepsGist,
  summariseTable,
  taskActivity,
  waitingPlans,
  waited,
  type ActivityEvent,
  type Plan,
  type PlanBlock,
  type PreviewSection,
  type TableSummary,
} from "./task-model";
import { LOOSE, type ProjectRef } from "./types";

/** What the page is handed, plus two fields it reads when the server sends them.
 *
 * `plan_modified` is when `.kith/work/task-<id>.md` was last written — the plan header's
 * "written 17 Sep" and the activity's "Wrote the plan" wait on it. `comments` is optional because
 * the server stopped sending the thread; the type still says it is there. */
type Detail = Omit<TaskDetail, "comments"> & { plan_modified?: string; comments?: TaskDetail["comments"] };

/** The board's columns, in the order work moves through them. `dropped` is not a step. */
const STEPS = [
  { status: "planning", label: "Planning" },
  { status: "approved", label: "Up next" },
  { status: "working", label: "Working" },
  { status: "done", label: "Done" },
] as const;

const STATUS_WORD: Record<string, string> = {
  planning: "Planning",
  approved: "Up next",
  working: "Working",
  done: "Done",
  dropped: "Dropped",
};

const PRIORITY_OPTIONS = [
  { value: "low", label: "Low" },
  { value: "normal", label: "Normal" },
  { value: "high", label: "High" },
] as const;

/* Blue is "approved / up next" in the design, and the theme has no token for it. */
const BLUE_TEXT = "text-sky-600 dark:text-[#6FA8F0]";
const BLUE_DOT = "bg-sky-500 dark:bg-[#6FA8F0]";

/** The design's link-styled text button: 12px, second-tier text, brightening on hover. */
const LINK = "text-foreground/80 hover:text-foreground text-[12px] transition-colors";

/** "17 Sep, 6:41 PM" — a moment that stands on its own line. */
function stamp(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const sameYear = at.getFullYear() === new Date().getFullYear();
  const day = at.toLocaleDateString(undefined, { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) });
  return `${day}, ${at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
}

/** "17 Sep" */
function shortDay(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "" : at.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** The full-page view of one task. Rendered in place of the tab content, not a panel. */
export function TaskDetailPage({
  taskId,
  projects,
  trail,
  onBack,
  onChanged,
  onOpenTask,
  onOpenProject,
  onNewConversation,
}: {
  taskId: number;
  /** For naming the project the task belongs to. */
  projects: {
    id: number;
    name: string;
  }[];
  /** Where "back" lands — the project you came in from, if any. */
  trail?: string;
  onBack: () => void;
  onChanged: () => void;
  /** Open another task — J / K, and the tasks the plan names. Without it they are not offered. */
  onOpenTask?: (id: number) => void;
  /** Open the task's project, from the breadcrumb and the rail. Falls back to `onBack`. */
  onOpenProject?: (ref: ProjectRef) => void;
  /** Open a new chat in a project (or none) and go to it; what you wrote is sent as its first
   *  message (see `start-chat.ts`). */
  onNewConversation?: (projectId: number | null) => void;
}) {
  const confirm = useConfirm();
  const cache = useQueryClient();
  const layer = useLayer(Layer.Panel);
  /** The design being looked at, if any. Held here rather than in the row that opened it so
   *  that leaving the editor lands back on this page with its scroll and state intact. */
  const [designing, setDesigning] = useState<{ path: string; title: string } | null>(null);
  /** The board — every design on this task at once, which is the surface comparison needs. */
  const [onBoard, setOnBoard] = useState(false);
  /** A download that could not be read. Said out loud, because the alternative — which this
   *  page shipped with — was handing you a .txt containing the path and calling it done. */
  const [refused, setRefused] = useState("");
  // The title being typed, held apart from the loaded task — see the input below for why.
  const [goalDraft, setGoalDraft] = useState<string | null>(null);
  const goalCancelled = useRef(false);
  const [descOpen, setDescOpen] = useState(false);
  const [descClipped, setDescClipped] = useState(false);
  const descClamp = useRef<HTMLParagraphElement>(null);
  /** What you are writing to him, and whether it is a change to the plan ("Request changes")
   *  rather than a reply — the chat it starts says which. */
  const [reply, setReply] = useState("");
  const [changing, setChanging] = useState(false);
  const [replyNote, setReplyNote] = useState("");
  const replyBox = useRef<HTMLInputElement>(null);
  const [moving, setMoving] = useState<string | null>(null);
  const [moveError, setMoveError] = useState("");

  const { data: task = null, isError } = useQuery({
    queryKey: keys.task(taskId),
    queryFn: () => fetchTaskDetail(taskId) as Promise<Detail>,
  });
  /* The panel's snapshot, for the project's other plans, its folder, and the tasks the plan
   * names. The same key and function the shell reads, so this is the cached answer and not a
   * second request. */
  const { data: snap = null } = useQuery({ queryKey: keys.brain(), queryFn: fetchBrain });

  const load = useCallback(
    () => void cache.invalidateQueries({ queryKey: keys.task(taskId) }),
    [cache, taskId],
  );

  /* Kept current by `task` events, through `STALE_ON` — he ticks a checklist item, attaches a
   * deliverable, and it appears.
   *
   * What was here: a 30-second interval, a `focus` listener, a DOM event to bridge them, and a
   * guard that skipped any refresh while a field on the page had something typed in it. All four
   * are gone, and the guard is worth a sentence because deleting a guard should never be quiet.
   *
   * It was protecting against server data replacing what someone was typing — and none of these
   * fields is written from server data while it is being edited. `EditableText` copies `value` into
   * its own draft when you click the pencil and never reads it again until you are done; the title
   * renders `goalDraft ?? task.goal`, where the draft wins; the new-step box and the composer are
   * local. The controls that *are* driven by the task — priority, status — hold no typed text to
   * lose. */
  const refresh = () => {
    load();
    onChanged();
  };
  const patch = async (data: Record<string, unknown>) => {
    await updateBrainItem("task", taskId, data);
    refresh();
  };
  /** Into the cache first, so the page answers the click rather than the round trip. */
  const optimistic = (data: Partial<Detail>) => {
    if (task) cache.setQueryData(keys.task(taskId), { ...task, ...data });
  };

  const plan = useMemo<Plan | null>(() => (task?.plan ? parsePlan(task.plan) : null), [task?.plan]);
  const tasks = useMemo(() => snap?.tasks ?? [], [snap]);
  const waiting = useMemo(
    () => (task ? waitingPlans(tasks, task.project_id ?? null) : []),
    [tasks, task],
  );
  const through = useMemo(() => stepThrough(waiting, taskId), [waiting, taskId]);

  /* J and K walk the project's waiting plans, the way the design's breadcrumb says.
   *
   * Window-level, like the panel's own keys, and with the same three refusals: not while typing,
   * not with a modifier held (⌘K is search), and not while anything sits above the panel — a
   * preview or a confirm owns the keyboard while it is open. */
  useEffect(() => {
    if (!onOpenTask || designing || onBoard) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key !== "j" && key !== "k") return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }
      if (!layer.frontmost()) return;
      const to = key === "j" ? through.next : through.prev;
      if (to == null) return;
      event.preventDefault();
      onOpenTask(to);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onOpenTask, designing, onBoard, through, layer]);

  // "Show all" is only offered when two lines did not hold the whole brief.
  useLayoutEffect(() => {
    const el = descClamp.current;
    setDescClipped(el ? el.scrollHeight > el.clientHeight + 1 : false);
  }, [task?.description, descOpen]);

  if (!task) {
    return (
      <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-3 text-sm">
        {isError ? (
          <>
            <span>Couldn't load #{taskId} — it may have been deleted.</span>
            <button type="button" onClick={onBack} className={BUTTON.secondary}>
              Back to projects
            </button>
          </>
        ) : (
          "Loading task…"
        )}
      </div>
    );
  }

  /* A design takes the page rather than opening beside it.
   *
   * The first question anyone asks of a mockup is whether it breathes, and that is not a
   * question you can answer in a gutter beside a checklist. Swapped in here rather than routed
   * to, because this panel navigates by state and a URL for it would be the only one in the
   * app. */
  if (onBoard) {
    return (
      <DesignBoardPage
        taskId={task.id}
        projectId={task.project_id}
        designs={task.deliverables
          .filter((d) => looksLikeADesign(d.kind, d.content))
          .map((d) => ({ file: d.content, title: d.title }))}
        onBack={() => setOnBoard(false)}
      />
    );
  }
  if (designing) {
    return (
      <DesignPage
        path={designing.path}
        title={designing.title}
        projectId={task.project_id}
        onBack={() => setDesigning(null)}
      />
    );
  }

  const project =
    (snap?.projects ?? []).find((one) => one.id === task.project_id) ??
    projects.find((one) => one.id === task.project_id);
  const projectName = project?.name ?? (task.project_id == null ? "No project" : (trail ?? "Project"));
  const folder = (snap?.projects ?? []).find((one) => one.id === task.project_id)?.directory ?? null;
  const openProject = () => {
    if (onOpenProject) onOpenProject(task.project_id ?? LOOSE);
    else onBack();
  };
  const othersWaiting = waiting.filter((one) => one.id !== task.id).length;
  const hasPlan = Boolean(task.plan);
  const isDesign = (d: Deliverable) => looksLikeADesign(d.kind, d.content);

  /* The one decision that is yours, and the two ways out of it. Status moves go through the same
   * call the board's drag uses, so a plan approved here and one dropped into Up next are the
   * same event to everything that listens for it. */
  const move = async (status: string) => {
    setMoving(status);
    setMoveError("");
    const was = task.status;
    optimistic({ status });
    try {
      await patch({ status });
    } catch {
      optimistic({ status: was });
      setMoveError(status === "approved" ? "Couldn't approve it — the server said no. Try again." : "Couldn't move it.");
    } finally {
      setMoving(null);
    }
  };
  const drop = async () => {
    const ok = await confirm({
      title: "Drop this task?",
      description: "It leaves the board and he won't pick it up. The plan, the steps and what he filed all stay.",
      subject: task.goal,
      destructive: true,
      confirmLabel: "Drop",
    });
    if (ok) await move("dropped");
  };
  const requestChanges = () => {
    setChanging(true);
    setReplyNote("");
    replyBox.current?.focus();
  };

  /* The composer starts a chat and sends what you wrote as its first message, because a chat is
   * where you talk to him — see `start-chat.ts` for why a task has nowhere of its own to send to.
   * The text names the task (`chatAbout`), so he knows which one it is about. */
  const send = (event: FormEvent) => {
    event.preventDefault();
    if (!reply.trim()) return;
    const text = chatAbout(task, reply, changing);
    if (onNewConversation && sendInNewChat(() => onNewConversation(task.project_id ?? null), text, task.project_id ?? null)) {
      setReply("");
      setChanging(false);
      return;
    }
    // Nowhere to put it: the words go on the clipboard rather than nowhere, and the page says so.
    void copyText(text).then((ok) =>
      setReplyNote(ok ? "Copied with the task's number — paste it into a chat with him." : "Couldn't start a chat from here."),
    );
  };

  /** Deliverables are work he produced — always ask first. */
  const delDeliverable = async (d: Deliverable) => {
    const ok = await confirm({
      title: "Delete this deliverable?",
      description: "It disappears from the task. The file on his computer stays.",
      subject: d.title,
      destructive: true,
    });
    if (!ok) return;
    await deleteBrainItem("deliverable", d.id);
    refresh();
  };
  /* Download the file, rather than a description of it.
   *
   * This read the deliverable through `fetchWorkspaceFile`, which returns *text*, and wrote that
   * into a `text/plain` blob — so a `.docx` was decoded, re-encoded as UTF-8 and handed back as a
   * file Word refused, and a file it could not read was saved as a .txt containing its own path.
   * `/api/workspace/raw` serves the bytes with the real content type (it is what the PDF viewer
   * reads); the global `fetch` wrapper attaches the API token. A failure now says so. */
  const downloadDeliverable = async (d: Deliverable) => {
    let blob: Blob;
    let name: string;
    if (d.kind === "file") {
      const url =
        `/api/workspace/raw?path=${encodeURIComponent(d.content)}` +
        (task.project_id ? `&projectId=${task.project_id}` : "");
      const response = await fetch(url).catch(() => null);
      if (!response?.ok) {
        setRefused(`Couldn't read ${d.content} — it may have been moved or renamed.`);
        return;
      }
      blob = await response.blob();
      // The file's own name, so the extension survives.
      name = d.content.split("/").pop() || d.title || "deliverable";
    } else {
      blob = new Blob([d.content], { type: "text/plain;charset=utf-8" });
      name = (d.title || "deliverable").replace(/\s+/g, "_") + ".txt";
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  };

  const notes = (task.comments ?? []).length;
  const events = taskActivity(task, isDesign);
  const named = plan
    ? namedTasks(plan, task.id).flatMap((ref) => {
        const other = tasks.find((one) => one.id === ref.id);
        return other ? [{ ...ref, task: other }] : [];
      })
    : [];

  return (
    <div className="flex h-full flex-col px-10 pt-7">
      {/* Where you are: the project, the task's number, and the way through its other plans. */}
      <nav aria-label="Breadcrumb" className="flex h-[18px] shrink-0 items-center gap-1.5 text-[12.5px]">
        <button type="button" onClick={openProject} className="text-muted-foreground hover:text-foreground transition-colors">
          {projectName}
        </button>
        <ChevronRight className="text-muted-foreground/50 size-3" aria-hidden />
        <span aria-current="page" className="text-foreground/80 font-mono text-[12px]">
          #{task.id}
        </span>
        <span className="flex-1" />
        {onOpenTask && othersWaiting > 0 ? (
          <span className="text-muted-foreground/80 flex items-center gap-1.5 text-[12px]">
            <Kbd>J</Kbd>
            <Kbd>K</Kbd>
            move through {projectName}'s {waiting.length} waiting plan{waiting.length === 1 ? "" : "s"}
          </span>
        ) : null}
      </nav>

      {/* The goal is the title, renamed in place.
          The draft lives outside `task`, and that is the whole fix for a rename that never
          saved: `onChange` used to write straight into `task`, so by `onBlur` the two were always
          equal and the patch never fired. */}
      <h1 className="m-0 mt-2.5 shrink-0">
        <input
          value={goalDraft ?? task.goal}
          onChange={(e) => setGoalDraft(e.target.value)}
          onBlur={() => {
            const next = goalDraft?.trim();
            setGoalDraft(null);
            // Escape blurs the field, and that blur must not be read as a commit. The blur fires
            // synchronously from inside the keydown handler, before React flushes state, so a ref
            // is the only thing this can trust.
            if (goalCancelled.current) {
              goalCancelled.current = false;
              return;
            }
            // A blank title is a slip, not an instruction.
            if (!next || next === task.goal) return;
            optimistic({ goal: next });
            void patch({ goal: next });
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              e.currentTarget.blur();
            }
            if (e.key === "Escape") {
              goalCancelled.current = true;
              setGoalDraft(null);
              e.currentTarget.blur();
            }
          }}
          aria-label="Task title"
          title={goalDraft ?? task.goal}
          className="hover:bg-muted/50 focus:bg-card focus:ring-ring/25 -mx-1.5 block h-[29px] w-[calc(100%+12px)] truncate rounded-md bg-transparent px-1.5 text-[22px] leading-[29px] font-[650] tracking-[-0.01em] outline-none transition-colors focus:ring-[3px]"
        />
      </h1>

      <Stepper status={task.status} hasPlan={hasPlan} updatedAt={task.updated_at} />

      {task.status === "planning" ? (
        hasPlan ? (
          <section
            aria-label="Plan waiting for your OK"
            className="border-kith/[0.22] bg-kith/[0.08] mt-3.5 flex h-14 shrink-0 items-center gap-3 rounded-xl border pr-3 pl-3.5"
          >
            <span className="bg-kith/[0.14] text-kith flex size-7 shrink-0 items-center justify-center rounded-lg">
              <ClipboardCheck className="size-3.5" aria-hidden />
            </span>
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="text-[13.5px] font-semibold">His plan is ready for your OK</span>
              <span className={cn("truncate text-[12px]", moveError ? "text-destructive" : "text-kith")}>
                {moveError ||
                  [
                    `Waiting ${waited(task.plan_modified ?? task.created_at)}`,
                    task.checklist.length ? `${task.checklist.length} step${task.checklist.length === 1 ? "" : "s"}` : null,
                    "nothing starts until you approve",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
              </span>
            </span>
            <button type="button" className={BUTTON.primary} disabled={moving !== null} onClick={() => void move("approved")}>
              <Check className="size-3.5" strokeWidth={2.4} aria-hidden />
              {moving === "approved" ? "Approving…" : "Approve plan"}
            </button>
            <button
              type="button"
              className={cn(BUTTON.secondary, "aria-pressed:bg-muted aria-pressed:border-ring/60")}
              aria-pressed={changing}
              onClick={requestChanges}
            >
              Request changes
            </button>
            <button type="button" className={cn(BUTTON.danger, "px-2.5")} disabled={moving !== null} onClick={() => void drop()}>
              Drop
            </button>
          </section>
        ) : (
          /* Planning with nothing filed: no Approve, because there is nothing to approve — and
             saying so beats a bar that offers a decision on a plan nobody can read. */
          <section
            aria-label="No plan yet"
            className="border-border bg-card mt-3.5 flex h-14 shrink-0 items-center gap-3 rounded-xl border pr-3 pl-3.5"
          >
            <span className="bg-muted/70 text-muted-foreground flex size-7 shrink-0 items-center justify-center rounded-lg">
              <FileClock className="size-3.5" aria-hidden />
            </span>
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="text-[13.5px] font-semibold">He hasn't filed the plan yet</span>
              <span className="text-muted-foreground truncate text-[12px]">
                Nothing to approve until he does · it goes to{" "}
                <span className="font-mono text-[11.5px]">.kith/work/task-{task.id}.md</span>
              </span>
            </span>
            <button
              type="button"
              className={BUTTON.secondary}
              onClick={() => {
                // The ask, written for you — Enter sends it to him in a new chat about this task.
                setChanging(false);
                setReply((was) => was || "Can you write the plan for this one?");
                replyBox.current?.focus();
              }}
            >
              Ask him for it
            </button>
            <button type="button" className={cn(BUTTON.danger, "px-2.5")} disabled={moving !== null} onClick={() => void drop()}>
              Drop
            </button>
          </section>
        )
      ) : null}

      <div className="mt-[22px] mb-5 flex min-h-0 flex-1 gap-14">
        {/* The main column: a scrolling body over a pinned composer. A flex footer rather than
            `sticky`, which anchors to the container's padding edge and let the log show through
            under it when the column came out a few pixels taller than the viewport. */}
        <div className="flex min-h-0 min-w-0 flex-[0_1_760px] flex-col">
          <div className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto">
            <section aria-label="Description" className="shrink-0">
              <SectionHead label="Description">
                <span className="flex-1" />
                {descClipped || descOpen ? (
                  <button type="button" className={LINK} onClick={() => setDescOpen((open) => !open)}>
                    {descOpen ? "Show less" : "Show all"}
                  </button>
                ) : null}
              </SectionHead>
              {/* Two lines until asked, edited in place. The pencil sits in the gutter on hover;
                  the raw Markdown is what you edit, the rendered brief is what you read. */}
              <div className="text-foreground/80 mt-1.5 text-[13.5px] leading-5">
                <EditableText
                  value={task.description}
                  multiline
                  placeholder="What this task is, and what 'done' looks like…"
                  render={(value) =>
                    descOpen ? (
                      <div className={PROSE}>
                        <Markdown>{value}</Markdown>
                      </div>
                    ) : (
                      <p ref={descClamp} className={cn("m-0 line-clamp-2", INLINE_CODE)}>
                        <MarkdownInline>{value}</MarkdownInline>
                      </p>
                    )
                  }
                  onSave={(value) => {
                    optimistic({ description: value });
                    void patch({ description: value });
                  }}
                />
              </div>
            </section>

            {plan && task.plan ? (
              <PlanSection
                taskId={task.id}
                projectId={task.project_id}
                source={task.plan}
                plan={plan}
                modified={task.plan_modified}
              />
            ) : null /* No plan: a planning task's bar above already says so, and where it will go. */}

            <Checklist
              task={task}
              onToggle={async (id, done) => {
                optimistic({ checklist: task.checklist.map((c) => (c.id === id ? { ...c, done: done ? 1 : 0 } : c)) });
                await updateBrainItem("checklist_item", id, { done });
                refresh();
              }}
              onAdd={async (text) => {
                await createBrainItem("checklist_item", { task_id: taskId, text });
                refresh();
              }}
              onDelete={async (id) => {
                await deleteBrainItem("checklist_item", id);
                refresh();
              }}
            />

            <section aria-label="Deliverables" className="shrink-0">
              <SectionHead label="Deliverables" count={task.deliverables.length || undefined}>
                {task.deliverables.length ? (
                  <span className="text-muted-foreground/80 text-[11.5px]">· filed {filedSpan(task.deliverables)}</span>
                ) : null}
                <span className="flex-1" />
                {/* The board, offered once rather than per row: comparing is the thing you do with
                    several designs, so the way in belongs to the set, not to any one of them. */}
                {task.deliverables.some(isDesign) ? (
                  <button type="button" className={LINK} onClick={() => setOnBoard(true)}>
                    Open all in canvas
                  </button>
                ) : null}
              </SectionHead>
              {refused ? (
                <p className="text-destructive border-destructive/30 bg-destructive/5 mt-1.5 rounded-lg border px-3 py-2 text-[12.5px]">
                  {refused}
                </p>
              ) : null}
              {task.deliverables.length ? (
                <ul className="m-0 mt-1.5 grid list-none grid-cols-2 gap-x-2 gap-y-1.5 p-0">
                  {task.deliverables.map((d) => (
                    <DeliverableTile
                      key={d.id}
                      d={d}
                      projectId={task.project_id}
                      onDelete={() => void delDeliverable(d)}
                      onDownload={() => void downloadDeliverable(d)}
                      onOpenDesign={isDesign(d) ? () => setDesigning({ path: d.content, title: d.title }) : undefined}
                    />
                  ))}
                </ul>
              ) : (
                <p className="text-muted-foreground/80 mt-1.5 text-[12.5px]">Nothing filed yet.</p>
              )}
            </section>

            {/* Activity, from what the rows can prove — see `taskActivity`.
                Not the old comment thread, and that is deliberate: 620 notes across the board,
                every one raising an alert, and the one that wanted an answer somewhere underneath.
                Progress lives in the working file (the plan above), questions in the chat where
                they can be answered, evidence in the checklist and the deliverables. */}
            <section aria-label="Activity" className="shrink-0 pb-1">
              <SectionHead label="Activity">
                <span className="flex-1" />
                <span className="text-muted-foreground/80 text-[11.5px]">
                  {notes ? `${notes} old note${notes === 1 ? "" : "s"} on the task, from before the thread was retired` : "No notes on the task itself yet"}
                </span>
              </SectionHead>
              <ol className="m-0 mt-1.5 flex list-none flex-col gap-1 p-0">
                {events.map((event, index) => (
                  <ActivityRow key={`${event.at}-${index}`} event={event} />
                ))}
              </ol>
            </section>
          </div>

          {/* Pinned. "Request changes" lands the cursor here. */}
          <form onSubmit={send} className="mt-2.5 flex shrink-0 flex-col gap-1">
            <label htmlFor={`task-${task.id}-reply`} className="sr-only">
              Reply to him about this task
            </label>
            <span className="border-input bg-card focus-within:border-ring flex h-10 items-center gap-2 rounded-[10px] border pr-1.5 pl-3 transition-colors">
              <input
                id={`task-${task.id}-reply`}
                ref={replyBox}
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                onKeyDown={(e) => {
                  // Escape backs out of "request changes" before it backs out of anything else.
                  if (e.key === "Escape" && changing) {
                    e.preventDefault();
                    setChanging(false);
                  }
                }}
                placeholder={
                  changing ? "Say what should change in the plan…" : "Reply to him — or say what to change in the plan…"
                }
                title="Sends this to him in a new chat about the task, and takes you there"
                className="placeholder:text-muted-foreground/80 h-[38px] min-w-0 flex-1 bg-transparent text-[13px] outline-none"
              />
              <Kbd>↵</Kbd>
              <button
                type="submit"
                aria-label="Send to him in a new chat about this task"
                disabled={!reply.trim()}
                className="bg-muted/60 text-muted-foreground enabled:hover:bg-muted enabled:hover:text-foreground flex size-7 shrink-0 items-center justify-center rounded-[7px] transition-colors"
              >
                <ArrowUp className="size-3.5" aria-hidden />
              </button>
            </span>
            {replyNote ? <span className="text-muted-foreground/80 text-[11.5px]">{replyNote}</span> : null}
          </form>
        </div>

        {/* The attributes: read far more often than they are changed, so a quiet column of
            label-over-value rather than a band of form controls above the content. */}
        <aside
          aria-label="Task details"
          className="border-border ml-auto flex w-[320px] shrink-0 flex-col gap-[18px] overflow-y-auto border-l pl-6"
        >
          <Prop label="Status">
            <StatusLine status={task.status} hasPlan={hasPlan} />
          </Prop>

          <Prop label="Priority">
            <div className="self-start">
              <Segmented
                label="Priority"
                options={PRIORITY_OPTIONS}
                value={(PRIORITY_OPTIONS.some((one) => one.value === task.priority) ? task.priority : "normal") as "low" | "normal" | "high"}
                onChange={(priority) => {
                  optimistic({ priority });
                  void patch({ priority });
                }}
              />
            </div>
          </Prop>

          {/* Shown, not set: the task edit has no project_id parameter, so a picker here would
              choose, update optimistically, and be ignored by the server. */}
          <Prop label="Project">
            <button
              type="button"
              onClick={openProject}
              className="hover:text-foreground flex min-w-0 items-center gap-2 text-left text-[13px] transition-colors"
            >
              <Folder className="text-muted-foreground/80 size-3.5 shrink-0" aria-hidden />
              <span className="truncate">{projectName}</span>
              {othersWaiting ? (
                <span className="text-muted-foreground/80 shrink-0 text-[12px]">
                  · {othersWaiting} {task.status === "planning" ? "other " : ""}plan{othersWaiting === 1 ? "" : "s"} waiting
                </span>
              ) : null}
            </button>
          </Prop>

          <Prop label="Raised by" gap="gap-1">
            <span className="text-[13px]">{task.created_by === "user" ? "You" : "Himself"}</span>
          </Prop>

          <div className="grid grid-cols-2 gap-3">
            <Prop label="Created">
              <span className="text-foreground/80 text-[13px] tabular-nums">{stamp(task.created_at)}</span>
            </Prop>
            <Prop label="Updated">
              <span className="text-foreground/80 text-[13px] tabular-nums">{stamp(task.updated_at)}</span>
            </Prop>
          </div>

          {folder ? <FolderLine path={folder} /> : null}

          {named.length ? (
            <div className="border-border/80 flex flex-col gap-1.5 border-t pt-[18px]">
              <RailLabel>Named in the plan</RailLabel>
              <div className="-mx-2 flex flex-col gap-0.5">
                {named.map((ref) => (
                  <button
                    key={ref.id}
                    type="button"
                    disabled={!onOpenTask}
                    onClick={() => onOpenTask?.(ref.id)}
                    className="enabled:hover:bg-muted/60 flex items-start gap-2.5 rounded-lg px-2 py-[7px] text-left transition-colors"
                  >
                    <span className="text-muted-foreground/80 w-8 shrink-0 pt-px font-mono text-[11px]">#{ref.id}</span>
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="truncate text-[12.5px]">{ref.task.goal}</span>
                      <span className="text-muted-foreground/80 truncate text-[11.5px]">
                        {STATUS_WORD[ref.task.status] ?? ref.task.status} · in {shortTitle(ref.section)}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

/* ── Header pieces ──────────────────────────────────────────────────────── */

/** Planning → Up next → Working → Done, with where the task is lit and the line beside it
 *  saying what happens next and whose move it is. Dropped is none of the four, so it is shown
 *  as its own quiet pill after them. */
function Stepper({ status, hasPlan, updatedAt }: { status: string; hasPlan: boolean; updatedAt: string }) {
  const current = STEPS.findIndex((step) => step.status === status);
  const dropped = status === "dropped";
  const hint: Record<string, string> = {
    planning: hasPlan
      ? "Approving is the one move that's yours — he carries it from Up next to Done."
      : "He writes the plan first; then approving it is yours.",
    approved: "Approved — he picks it up from Up next and carries it to Done.",
    working: "He's on it — the checklist below is where he is.",
    done: `Finished ${finishedOn(updatedAt)}.`,
    dropped: "Dropped — it stays here, out of his way.",
  };
  const tone: Record<string, string> = {
    planning: "border-kith/30 bg-kith/10 text-kith",
    approved: cn("border-sky-500/30 bg-sky-500/10", BLUE_TEXT),
    working: "border-roam/25 bg-roam/10 text-roam",
    done: "border-roam/25 bg-roam/10 text-roam",
  };
  return (
    <div className="mt-3.5 flex h-7 shrink-0 items-center gap-4">
      <ol aria-label="Status" className="m-0 flex list-none items-center p-0">
        {STEPS.map((step, index) => (
          <StepItem
            key={step.status}
            first={index === 0}
            label={step.label}
            state={index === current ? "current" : current > index ? "past" : "future"}
            tone={tone[step.status]}
            status={step.status}
            dim={dropped}
          />
        ))}
        {dropped ? (
          <>
            <li aria-hidden className="h-px w-7 bg-[color-mix(in_oklab,var(--foreground)_14%,transparent)]" />
            <li
              aria-current="step"
              className="border-border bg-muted/60 text-muted-foreground flex h-7 items-center gap-2 rounded-full border pr-3 pl-2.5 text-[12.5px] font-semibold"
            >
              <span className="bg-muted-foreground/50 size-[7px] rounded-full" aria-hidden />
              Dropped
            </li>
          </>
        ) : null}
      </ol>
      <span className="text-muted-foreground/80 truncate text-[12px]">{hint[status] ?? ""}</span>
    </div>
  );
}

function StepItem({
  first,
  label,
  state,
  tone,
  status,
  dim,
}: {
  first: boolean;
  label: string;
  state: "past" | "current" | "future";
  tone: string;
  status: string;
  dim: boolean;
}) {
  return (
    <>
      {first ? null : <li aria-hidden className="h-px w-7 bg-[color-mix(in_oklab,var(--foreground)_14%,transparent)]" />}
      {state === "current" ? (
        <li
          aria-current="step"
          className={cn("flex h-7 items-center gap-2 rounded-full border pr-3 pl-2.5 text-[12.5px] font-semibold", tone)}
        >
          {status === "working" ? (
            <LiveDot />
          ) : (
            <span
              aria-hidden
              className={cn(
                "size-[7px] rounded-full",
                status === "planning" ? "bg-kith" : status === "approved" ? BLUE_DOT : "bg-roam",
              )}
            />
          )}
          {label}
        </li>
      ) : (
        <li
          className={cn(
            "text-muted-foreground/80 flex h-7 items-center gap-2 px-2 text-[12.5px]",
            dim && "opacity-60",
          )}
        >
          {state === "past" ? (
            <Check className="text-muted-foreground size-[11px]" strokeWidth={2.6} aria-hidden />
          ) : (
            <span aria-hidden className="border-muted-foreground/50 size-[9px] rounded-full border-[1.5px]" />
          )}
          <span>
            {label}
            {state === "past" ? <span className="sr-only"> (done)</span> : null}
          </span>
        </li>
      )}
    </>
  );
}

/** The rail's one-line status, in the stepper's colours. */
function StatusLine({ status, hasPlan }: { status: string; hasPlan: boolean }) {
  const line: Record<string, { dot: ReactNode; text: string; className: string }> = {
    planning: hasPlan
      ? { dot: <Dot className="bg-kith" />, text: "Plan ready for your OK", className: "text-kith" }
      : { dot: <Dot className="bg-muted-foreground/50" />, text: "Planning — no plan yet", className: "text-foreground/80" },
    approved: { dot: <Dot className={BLUE_DOT} />, text: "Up next — approved", className: BLUE_TEXT },
    working: { dot: <LiveDot />, text: "Working — he's on it", className: "text-roam" },
    done: { dot: <Dot className="bg-roam" />, text: "Done", className: "text-roam" },
    dropped: { dot: <Dot className="bg-muted-foreground/50" />, text: "Dropped", className: "text-muted-foreground" },
  };
  const one = line[status] ?? { dot: <Dot className="bg-muted-foreground/50" />, text: status, className: "" };
  return (
    <span className={cn("flex items-center gap-2 text-[13px]", one.className)}>
      {one.dot}
      {one.text}
    </span>
  );
}

function Dot({ className }: { className: string }) {
  return <span aria-hidden className={cn("size-[7px] shrink-0 rounded-full", className)} />;
}

/* ── Main-column pieces ─────────────────────────────────────────────────── */

/** Section label row: the uppercase label, a dim count, then whatever the section adds. */
function SectionHead({ label, count, children }: { label: string; count?: number; children?: ReactNode }) {
  return (
    <div className="flex h-[18px] items-center gap-2">
      <h2 className="text-muted-foreground/80 m-0 text-[10.5px] font-semibold tracking-[0.1em] uppercase">{label}</h2>
      {count !== undefined ? <span className="text-muted-foreground/50 text-[11px] tabular-nums">{count}</span> : null}
      {children}
    </div>
  );
}

/** Inline code at the design's size, inside text this page renders through `MarkdownInline`. */
const INLINE_CODE =
  "[&_code]:bg-muted/60 [&_code]:text-foreground [&_code]:rounded-[4px] [&_code]:px-1 [&_code]:py-0 [&_code]:text-[0.89em] [&_strong]:text-foreground [&_strong]:font-semibold";

/** `Markdown`, brought down to this page's 13px measure: its own type scale is a document's. */
const PROSE = cn(
  "[&>div]:text-[13px] [&>div]:leading-5 [&_p]:my-2 [&_ul]:my-2 [&_ol]:my-2 [&_ul]:ms-4 [&_ol]:ms-4 [&_li]:leading-5",
  "[&_h1]:mt-3 [&_h1]:mb-1 [&_h1]:text-[13.5px] [&_h2]:mt-3 [&_h2]:mb-1 [&_h2]:border-0 [&_h2]:pb-0 [&_h2]:text-[13.5px]",
  "[&_h3]:mt-3 [&_h3]:mb-1 [&_h3]:text-[13px] [&_h4]:text-[13px] [&_h1]:text-foreground [&_h2]:text-foreground [&_h3]:text-foreground",
  "[&_blockquote]:my-2 [&_blockquote]:ps-3",
  INLINE_CODE,
);

function PlanSection({
  taskId,
  projectId,
  source,
  plan,
  modified,
}: {
  taskId: number;
  projectId: number | null;
  source: string;
  plan: Plan;
  modified?: string;
}) {
  const path = `.kith/work/task-${taskId}.md`;
  /* Collapsed to what fits, then the whole thing on request. A plan is several hundred words
   * above the checklist, and a task in `planning` is the one case where it is the reason you are
   * here — so the preview is the plan's own first sections, not a teaser line, and everything
   * else it covers is named in the footer and one click away. */
  const [whole, setWhole] = useState(false);
  const [openTables, setOpenTables] = useState<Set<string>>(() => new Set());
  const [goTo, setGoTo] = useState<number | null>(null);
  const [viewing, setViewing] = useState(false);
  const sections = useRef<(HTMLElement | null)[]>([]);
  const card = useRef<HTMLDivElement>(null);
  const preview = useMemo(() => previewPlan(plan), [plan]);
  /* "Also in the plan" is one line, and the names are whole or absent: the row wraps into a
   * clipped second line, so a name that does not fit drops out entirely instead of being cut to
   * "Gen", and the count of those that dropped is what "+N more" says. Measured, because the
   * question is whether the words fit, and only the browser knows how wide they are. */
  const linkRow = useRef<HTMLSpanElement>(null);
  const [hiddenLinks, setHiddenLinks] = useState(0);
  useLayoutEffect(() => {
    const row = linkRow.current;
    if (!row) return;
    const count = () => {
      const items = [...row.children] as HTMLElement[];
      const top = items[0]?.offsetTop ?? 0;
      setHiddenLinks(items.filter((item) => item.offsetTop > top).length);
    };
    count();
    const watch = new ResizeObserver(count);
    watch.observe(row);
    return () => watch.disconnect();
  }, [preview, whole]);
  const bytes = useMemo(() => new TextEncoder().encode(source).length, [source]);

  useEffect(() => {
    if (!whole || goTo === null) return;
    sections.current[goTo]?.scrollIntoView({ block: "start", behavior: "smooth" });
    setGoTo(null);
  }, [whole, goTo]);

  /** Open the whole plan, at a section — and with its table unfolded, when that is what was
   *  clicked: the summary row in the preview is a promise of the table behind it. */
  const open = (index: number | null, table = false) => {
    if (table && index !== null) {
      const at = plan.sections[index].blocks.findIndex((block) => block.kind === "table");
      if (at >= 0) setOpenTables((was) => new Set(was).add(`${index}:${at}`));
    }
    setWhole(true);
    setGoTo(index);
  };
  const fold = () => {
    setWhole(false);
    setOpenTables(new Set());
    card.current?.scrollIntoView({ block: "nearest" });
  };
  const toggleTable = (key: string) =>
    setOpenTables((was) => {
      const next = new Set(was);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section aria-label="Plan" className="shrink-0">
      <SectionHead label="Plan">
        <span className="text-muted-foreground truncate font-mono text-[11px]">{path}</span>
        <span className="text-muted-foreground/80 shrink-0 text-[11.5px]">
          · {size(bytes)}
          {modified ? ` · written ${shortDay(modified)}` : ""}
        </span>
        <span className="flex-1" />
        <button type="button" className={LINK} onClick={() => setViewing(true)}>
          Open file
        </button>
      </SectionHead>
      <div
        ref={card}
        className={cn(
          "border-border bg-card mt-1.5 flex flex-col overflow-hidden rounded-[14px] border",
          whole ? "" : "h-[284px]",
        )}
      >
        {whole ? (
          <div className="px-5 pt-3 pb-4">
            {plan.preamble ? (
              <div className={cn(PROSE, "text-muted-foreground/80 [&>div]:text-[12px] mb-2")}>
                <Markdown>{plan.preamble}</Markdown>
              </div>
            ) : null}
            {plan.sections.map((section, index) => (
              <section
                key={index}
                ref={(el) => {
                  sections.current[index] = el;
                }}
                className="scroll-mt-2 [&:not(:first-of-type)]:mt-3"
              >
                <h3 className="m-0 text-[13.5px] leading-[18px] font-semibold">{section.title}</h3>
                <SectionBody
                  section={section}
                  isOpen={(at) => openTables.has(`${index}:${at}`)}
                  onToggle={(at) => toggleTable(`${index}:${at}`)}
                />
              </section>
            ))}
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-hidden px-5 pt-3">
            {preview.shown.map((one, at) => (
              <PreviewBlock
                key={one.index}
                one={one}
                first={at === 0}
                onOpen={(table) => open(one.index, table)}
              />
            ))}
          </div>
        )}
        <div className="border-border/80 flex h-[34px] shrink-0 items-center gap-2 border-t px-5 text-[12px]">
          {!whole && preview.rest.length ? (
            <span className="flex min-w-0 flex-1 items-center gap-2 whitespace-nowrap">
              <span className="text-muted-foreground/80 shrink-0">Also in the plan</span>
              <span ref={linkRow} className="flex h-[18px] min-w-0 flex-1 flex-wrap items-center gap-x-2 overflow-hidden">
                {preview.rest.map((index, at) => (
                  <span key={index} className="flex h-[18px] shrink-0 items-center gap-2">
                    {at ? <span className="text-muted-foreground/50" aria-hidden>·</span> : null}
                    <button type="button" className={LINK} onClick={() => open(index)}>
                      {shortTitle(plan.sections[index].title)}
                    </button>
                  </span>
                ))}
              </span>
              {hiddenLinks ? (
                <button
                  type="button"
                  className={cn(LINK, "text-muted-foreground/80 shrink-0")}
                  onClick={() => open(preview.rest[preview.rest.length - hiddenLinks] ?? null)}
                >
                  +{hiddenLinks} more
                </button>
              ) : null}
            </span>
          ) : (
            <span className="flex-1" />
          )}
          <button
            type="button"
            aria-expanded={whole}
            className={cn(LINK, "flex shrink-0 items-center gap-1")}
            onClick={() => (whole ? fold() : open(null))}
          >
            {whole ? "Show less" : "Read the whole plan"}
            {whole ? <ChevronUp className="size-3" aria-hidden /> : <ChevronDown className="size-3" aria-hidden />}
          </button>
        </div>
      </div>
      {viewing ? (
        <FileViewer
          open={viewing}
          onOpenChange={setViewing}
          name={path}
          title={`Plan for #${taskId}`}
          content={source}
          projectId={projectId}
          onOpenOnHost={(reveal) => openWorkspaceFile(path, reveal, projectId)}
        />
      ) : null}
    </section>
  );
}

/** One section of the folded plan, at its most telling — see `previewPlan`. */
function PreviewBlock({
  one,
  first,
  onOpen,
}: {
  one: PreviewSection;
  first: boolean;
  /** Open the whole plan at this section — with its table unfolded when `table`. */
  onOpen: (table: boolean) => void;
}) {
  if (one.kind === "table") {
    return (
      <div className={first ? "" : "mt-2.5"}>
        <TableRow title={one.title} summary={one.summary} open={false} onToggle={() => onOpen(true)} />
      </div>
    );
  }
  const clamp = one.leadLines >= 3 ? "line-clamp-3" : one.leadLines === 2 ? "line-clamp-2" : "line-clamp-1";
  return (
    <div className={first ? "" : "mt-3"}>
      <h3 className="m-0 text-[13.5px] leading-[18px] font-semibold">{one.title}</h3>
      {one.lead ? (
        <p className={cn("text-foreground/80 m-0 mt-1 text-[13px] leading-5", clamp, INLINE_CODE)}>
          <MarkdownInline>{one.lead}</MarkdownInline>
        </p>
      ) : null}
      {one.items.length ? (
        <ul className={cn("text-foreground/80 m-0 mt-1 list-disc ps-4 text-[13px] leading-5 marker:text-muted-foreground/80", INLINE_CODE)}>
          {one.items.map((text, index) => (
            <li key={index} className="truncate">
              <MarkdownInline>{text}</MarkdownInline>
            </li>
          ))}
          {one.more ? (
            <li className="list-none">
              <button type="button" className={cn(LINK, "text-muted-foreground/80 text-[12.5px]")} onClick={() => onOpen(false)}>
                +{one.more} more
              </button>
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

/** A section of the open plan: its Markdown, with any long table folded to its summary row. */
function SectionBody({
  section,
  isOpen,
  onToggle,
}: {
  section: Plan["sections"][number];
  isOpen: (at: number) => boolean;
  onToggle: (at: number) => void;
}) {
  // Runs of ordinary blocks go back through `Markdown` as one piece; a long table stands apart.
  const parts: ({ kind: "md"; raw: string } | { kind: "table"; at: number; block: Extract<PlanBlock, { kind: "table" }> })[] = [];
  section.blocks.forEach((block, at) => {
    if (block.kind === "table" && block.rows.length >= LONG_TABLE) {
      parts.push({ kind: "table", at, block });
      return;
    }
    const last = parts[parts.length - 1];
    if (last?.kind === "md") last.raw += `\n\n${block.raw}`;
    else parts.push({ kind: "md", raw: block.raw });
  });
  return (
    <div className="text-foreground/80 mt-1">
      {parts.map((part, index) =>
        part.kind === "md" ? (
          <div key={index} className={PROSE}>
            <Markdown>{part.raw}</Markdown>
          </div>
        ) : (
          <div key={index} className="my-2">
            <TableRow
              title={section.title}
              titled={false}
              summary={summariseTable(section, part.block)}
              open={isOpen(part.at)}
              onToggle={() => onToggle(part.at)}
            />
            {isOpen(part.at) ? (
              <div className={cn(PROSE, "mt-2")}>
                <Markdown>{part.block.raw}</Markdown>
              </div>
            ) : null}
          </div>
        ),
      )}
    </div>
  );
}

/** A long evidence table as one line: what it is, how many facts, how they came out, and what
 *  he concluded — "What I checked · 6 facts, verified this turn · 4 built · 2 missing". */
function TableRow({
  title,
  titled = true,
  summary,
  open,
  onToggle,
}: {
  title: string;
  /** Off in the open plan, where the section's own heading already sits above the row. */
  titled?: boolean;
  summary: TableSummary;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={onToggle}
      className="border-border bg-sidebar hover:bg-muted/50 flex h-7 w-full items-center gap-2 rounded-lg border px-2.5 text-left text-[12.5px] transition-colors"
    >
      <ChevronRight
        className={cn("text-muted-foreground/80 size-3 shrink-0 transition-transform", open && "rotate-90")}
        aria-hidden
      />
      {titled ? <span className="shrink-0 font-semibold">{shortTitle(title)}</span> : null}
      <span className="text-muted-foreground/80 shrink-0">
        {summary.rows} {summary.rows === 1 ? summary.noun.replace(/s$/, "") : summary.noun}
        {summary.note ? `, ${summary.note}` : ""}
      </span>
      <span className="flex-1" />
      {summary.marks.map((mark) => (
        <span
          key={mark.tone}
          className={cn(
            "flex shrink-0 items-center gap-1 text-[12px]",
            mark.tone === "yes" ? "text-roam" : mark.tone === "no" ? "text-destructive" : "text-kith",
          )}
        >
          {mark.tone === "yes" ? (
            <Check className="size-3" strokeWidth={2.4} aria-hidden />
          ) : mark.tone === "no" ? (
            <span aria-hidden className="text-[11px] leading-none">✕</span>
          ) : (
            <span aria-hidden className="text-[11px] leading-none">!</span>
          )}
          {mark.count} {mark.label}
        </span>
      ))}
      {summary.conclusion ? (
        <>
          <span className="text-muted-foreground/50 shrink-0" aria-hidden>
            ·
          </span>
          <span className="text-foreground/80 min-w-0 truncate text-[12px]">{summary.conclusion}</span>
        </>
      ) : null}
    </button>
  );
}

function Checklist({
  task,
  onToggle,
  onAdd,
  onDelete,
}: {
  task: Detail;
  onToggle: (id: number, done: boolean) => Promise<void>;
  onAdd: (text: string) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  const [all, setAll] = useState(false);
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState("");
  const items = task.checklist;
  const done = items.filter((one) => one.done).length;
  const { start, end } = stepWindow(items);
  const folded = !all && end - start < items.length;
  const shown = folded ? items.slice(start, end) : items;
  const hidden = folded ? [...items.slice(0, start), ...items.slice(end)] : [];
  const hint: Record<string, string> =
    items.length && done === items.length
      ? { planning: "All ticked", approved: "All ticked", working: "All ticked", done: "All done", dropped: "" }
      : {
          planning: "His steps once you approve",
          approved: "His steps — he starts at the top",
          working: "Where he is",
          done: "",
          dropped: "",
        };

  const add = async () => {
    const value = text.trim();
    if (!value) return;
    setText("");
    await onAdd(value);
  };

  return (
    <section aria-label="Checklist" className="shrink-0">
      <SectionHead label="Checklist">
        {items.length ? (
          <>
            <span className="bg-muted ml-0.5 h-1 w-16 overflow-hidden rounded-full" aria-hidden>
              <span
                className="bg-roam block h-full rounded-full transition-all"
                style={{ width: `${Math.round((done / items.length) * 100)}%` }}
              />
            </span>
            <span className="text-muted-foreground text-[12px] tabular-nums">
              {done} of {items.length} done
            </span>
          </>
        ) : null}
        <span className="flex-1" />
        {items.length && hint[task.status] ? (
          <span className="text-muted-foreground/80 text-[11.5px]">{hint[task.status]}</span>
        ) : null}
        {items.length && hint[task.status] ? (
          <span className="text-muted-foreground/50 text-[11.5px]" aria-hidden>
            ·
          </span>
        ) : null}
        {/* In the head, with the section's other actions, rather than as a row under the list:
            adding a step is occasional, and the list is what the section is for. */}
        <button type="button" className={cn(LINK, "flex items-center gap-1")} onClick={() => setAdding(true)}>
          <Plus className="size-3" aria-hidden />
          Add a step
        </button>
      </SectionHead>
      <div className="mt-1.5 flex flex-col">
        {items.length === 0 ? (
          <p className="text-muted-foreground/80 m-0 py-1 text-[12.5px]">No steps yet — he adds them with the plan.</p>
        ) : null}
        {shown.map((item) => {
          const position = items.indexOf(item) + 1;
          return (
            <div
              key={item.id}
              className={cn(
                "group text-foreground/80 flex gap-2.5 text-[13px]",
                folded ? "h-[26px] items-center" : "min-h-[26px] items-start py-[3px]",
              )}
            >
              <button
                type="button"
                role="checkbox"
                aria-checked={Boolean(item.done)}
                aria-label={`Step ${position}: ${plain(item.text)}`}
                onClick={() => void onToggle(item.id, !item.done)}
                className={cn(
                  "flex size-3.5 shrink-0 items-center justify-center rounded-[4px] border-[1.5px] transition-colors",
                  !folded && "mt-[3px]",
                  item.done
                    ? "border-roam bg-roam text-background"
                    : "hover:border-kith border-[color-mix(in_oklab,var(--foreground)_22%,transparent)]",
                )}
              >
                {item.done ? <Check className="size-2.5" strokeWidth={3} aria-hidden /> : null}
              </button>
              <span className={cn("text-muted-foreground/50 w-3 shrink-0 font-mono text-[11px]", !folded && "pt-px")}>
                {position}
              </span>
              {/* Muted, not struck through: the tick already says it is done, and a rule through
                  a wrapped sentence stops being decoration and starts being unreadable. */}
              <span
                title={folded ? plain(item.text) : undefined}
                className={cn("min-w-0 flex-1", folded && "truncate", item.done && "text-muted-foreground/80", INLINE_CODE)}
              >
                <MarkdownInline>{item.text}</MarkdownInline>
              </span>
              <button
                type="button"
                onClick={() => void onDelete(item.id)}
                aria-label={`Delete step ${position}`}
                className="text-muted-foreground hover:text-destructive shrink-0 opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
              >
                <Trash2 className="size-3.5" aria-hidden />
              </button>
            </div>
          );
        })}
        {folded ? (
          <button
            type="button"
            onClick={() => setAll(true)}
            className="text-muted-foreground/80 hover:text-foreground flex h-6 items-center gap-2 overflow-hidden pl-6 text-left text-[12px] whitespace-nowrap transition-colors"
          >
            <span className="text-foreground/80 shrink-0">{hidden.length} more</span>
            <span className="truncate">{stepsGist(hidden.map((one) => one.text))}</span>
          </button>
        ) : all && end - start < items.length ? (
          <button type="button" onClick={() => setAll(false)} className={cn(LINK, "text-muted-foreground/80 flex h-6 items-center pl-6")}>
            Show fewer
          </button>
        ) : null}
        {adding ? (
          <div className="flex h-[30px] items-center gap-2.5 pl-6">
            <input
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void add();
                if (e.key === "Escape") {
                  e.preventDefault();
                  setText("");
                  setAdding(false);
                }
              }}
              onBlur={() => {
                if (!text.trim()) setAdding(false);
              }}
              aria-label="New step"
              placeholder="Add a step, then Enter…"
              className="border-input bg-card focus:border-ring placeholder:text-muted-foreground/80 h-7 min-w-0 flex-1 rounded-lg border px-2.5 text-[13px] outline-none"
            />
          </div>
        ) : null}
      </div>
    </section>
  );
}

/** One produced thing, as a tile. Clicking it opens it — the design editor for a design, the
 *  preview for a file or text, the page for a link — and the rest of what you can do with it
 *  appears on hover, where the size was. */
function DeliverableTile({
  d,
  projectId,
  onDelete,
  onDownload,
  onOpenDesign,
}: {
  d: Deliverable;
  projectId: number | null;
  onDelete: () => void;
  onDownload: () => void;
  /** Present only when this deliverable is something the design editor can open. */
  onOpenDesign?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState<string | null>(d.kind === "text" ? d.content : null);
  const [err, setErr] = useState("");
  const isLink = d.kind === "link";
  const isFile = d.kind === "file";
  // A text deliverable is his prose — read it as Markdown, like a file would be.
  const name = isFile ? d.content : `${(d.title || "deliverable").replace(/\s+/g, "-")}.md`;
  const Icon = onOpenDesign ? PanelsTopLeft : isLink ? Link2 : FileText;
  const right = isLink ? "link" : isFile ? (d.bytes !== undefined ? size(d.bytes) : "missing") : `${d.content.length.toLocaleString()} chars`;

  /* What the tile cannot fit, in its tooltip: the path, and the four facts in the order you would
   * ask for them — each omitted rather than faked when it is not known, and "edited" only when the
   * file really moved on since it was filed (they differ by milliseconds on most). */
  const facts = [
    isLink ? "link" : isFile ? extensionOf(d.content) : "text",
    d.bytes !== undefined ? size(d.bytes) : null,
    d.created_at ? `filed ${dayLabel(d.created_at)} ${time(d.created_at)}` : null,
    d.modified && changedSince(d.created_at, d.modified) ? `edited ${dayLabel(d.modified)} ${time(d.modified)}` : null,
  ].filter(Boolean);
  const tooltip = [d.title, isLink || isFile ? d.content : null, facts.join(" · ")].filter(Boolean).join("\n");

  const preview = () => {
    setOpen(true);
    // A screenshot he attached is shown by the viewer from its own bytes, so it is not read as
    // text here — that only ever produced a decode error.
    if (body == null && isFile && !skipTextRead(d.content)) {
      fetchWorkspaceFile(d.content, projectId)
        .then((f) => setBody(f.content))
        .catch((e) => setErr(e instanceof Error ? e.message : "couldn't read that file"));
    }
  };
  const primary = () => {
    if (isLink) window.open(d.content, "_blank", "noreferrer");
    else if (onOpenDesign) onOpenDesign();
    else preview();
  };

  return (
    <li className="group border-border/80 hover:bg-muted/60 focus-within:bg-muted/60 relative flex h-[34px] min-w-0 items-center rounded-[10px] border transition-colors">
      <button
        type="button"
        onClick={primary}
        title={tooltip}
        className="flex h-full min-w-0 flex-1 items-center gap-2.5 rounded-[10px] pr-2.5 pl-[5px] text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/25"
      >
        <span className="bg-muted/60 text-muted-foreground flex size-6 shrink-0 items-center justify-center rounded-[7px]">
          <Icon className="size-3" aria-hidden />
        </span>
        <span className="min-w-0 flex-1 truncate text-[12.5px]">{d.title || d.content}</span>
        <span className="text-muted-foreground/80 shrink-0 text-[11px] tabular-nums group-focus-within:invisible group-hover:invisible">
          {right}
        </span>
      </button>
      <span className="absolute inset-y-0 right-1 flex items-center gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
        {isLink ? (
          <TileAction label="Open link" onClick={primary} icon={<ArrowUpRight />} />
        ) : (
          <>
            {onOpenDesign ? <TileAction label="Open preview" onClick={preview} icon={<Expand />} /> : null}
            <TileAction label="Download" onClick={onDownload} icon={<Download />} />
          </>
        )}
        <TileAction label="Delete" onClick={onDelete} icon={<Trash2 />} danger />
      </span>
      {open ? (
        <FileViewer
          open={open}
          onOpenChange={setOpen}
          name={name}
          title={d.title || name}
          content={body}
          error={err || undefined}
          badge={<span className="bg-kith-soft text-kith shrink-0 rounded px-1.5 py-0.5 font-medium">deliverable</span>}
          onDownload={onDownload}
          projectId={projectId}
          onOpenOnHost={isFile ? (reveal) => openWorkspaceFile(d.content, reveal, projectId) : undefined}
        />
      ) : null}
    </li>
  );
}

function TileAction({
  label,
  onClick,
  icon,
  danger,
}: {
  label: string;
  onClick: () => void;
  icon: ReactNode;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "text-muted-foreground hover:bg-muted hover:text-foreground flex size-6 items-center justify-center rounded-md transition-colors [&_svg]:size-3.5",
        danger && "hover:bg-destructive/10 hover:text-destructive",
      )}
    >
      {icon}
    </button>
  );
}

/** One line of what happened. Yours read as your words, on a card; his as actions, bare. */
function ActivityRow({ event }: { event: ActivityEvent }) {
  const you = event.who === "you";
  const kith = event.who === "kith";
  return (
    <li
      className={cn(
        "flex h-6 min-w-0 items-center gap-2 rounded-lg border pr-2 pl-[3px]",
        you ? "bg-card border-border/80" : "border-transparent",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "flex size-[18px] shrink-0 items-center justify-center rounded-full text-[10px] font-bold",
          you ? "bg-muted text-foreground" : kith ? "bg-kith/15 text-kith" : "bg-muted/60 text-muted-foreground/80",
        )}
      >
        {you ? "Y" : kith ? "K" : "·"}
      </span>
      {event.who ? (
        <span className={cn("shrink-0 text-[12.5px] font-semibold", you ? "text-foreground" : "text-kith")}>
          {you ? "You" : "Kith"}
        </span>
      ) : null}
      <span className={cn("min-w-0 truncate text-[12.5px]", you ? "text-foreground" : "text-foreground/80")} title={event.text}>
        {event.text}
      </span>
      <span className="flex-1" />
      <span className="text-muted-foreground/80 shrink-0 text-[11px] whitespace-nowrap tabular-nums">{stamp(event.at)}</span>
    </li>
  );
}

/* ── Rail pieces ────────────────────────────────────────────────────────── */

function RailLabel({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <span id={id} className="text-muted-foreground/80 text-[10.5px] leading-[13px] font-semibold tracking-[0.1em] uppercase">
      {children}
    </span>
  );
}

/** One attribute in the rail: a quiet label above its value. */
function Prop({ label, id, gap = "gap-1.5", children }: { label: string; id?: string; gap?: string; children: ReactNode }) {
  return (
    <div className={cn("flex min-w-0 flex-col leading-4", gap)}>
      <RailLabel id={id}>{label}</RailLabel>
      {children}
    </div>
  );
}

/** The project's folder — the one fact that decides where every file he writes lands — with a
 *  way to copy it. */
function FolderLine({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Prop label="Folder">
      <span className="border-border bg-card flex h-[30px] items-center gap-2 rounded-lg border pr-1 pl-2.5">
        <span className="text-foreground/80 min-w-0 flex-1 truncate font-mono text-[11.5px]" title={path}>
          {shortPath(path, 40)}
        </span>
        <button
          type="button"
          aria-label={copied ? "Copied" : "Copy folder path"}
          title={copied ? "Copied" : "Copy folder path"}
          onClick={() =>
            void copyText(path).then((ok) => {
              if (!ok) return;
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            })
          }
          className="text-muted-foreground/80 hover:bg-muted/60 hover:text-foreground flex size-6 shrink-0 items-center justify-center rounded-md transition-colors"
        >
          {copied ? <Check className="text-roam size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
        </button>
      </span>
    </Prop>
  );
}

/* ── Helpers ────────────────────────────────────────────────────────────── */

/** "today", "yesterday", "on Monday", "on 17 September". */
function finishedOn(iso: string): string {
  const day = dayLabel(iso);
  return day === "Today" || day === "Yesterday" ? day.toLowerCase() : `on ${day}`;
}

/** When the deliverables were filed: "17 Sep", or "17 Sep – 2 Oct" across days. */
function filedSpan(deliverables: Deliverable[]): string {
  const days = deliverables.map((d) => d.created_at).filter(Boolean).sort();
  if (!days.length) return "";
  const first = shortDay(days[0]);
  const last = shortDay(days[days.length - 1]);
  return first === last ? first : `${first} – ${last}`;
}

/** `docs/a/b/Thing.docx` → `docx`. What identifies what a file *is*, and the reason a download
 *  has to keep the extension. */
function extensionOf(path: string): string {
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "file";
}

/** Whether the file has really moved on since it was filed. A minute of slack, because the file
 *  is written and the deliverable filed in the same breath. */
function changedSince(filed: string, modified: string): boolean {
  const a = new Date(filed).getTime();
  const b = new Date(modified).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return false;
  return b - a > 60_000;
}
