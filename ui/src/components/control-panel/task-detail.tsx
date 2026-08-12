import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Lock,
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronRight,
  Download,
  Expand,
  FileCode2,
  FileText,
  Link2,
  ListChecks,
  MessageCircle,
  Plus,
  Send,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Dropdown } from "@/components/ui/dropdown";
import { useConfirm } from "@/components/ui/confirm";
import { FileViewer, Markdown, MarkdownInline, skipTextRead } from "@/components/files";
import { EditableText } from "@/components/ui/editable-text";
import { openWorkspaceFile } from "@/lib/files";
import { cn } from "@/lib/utils";
import {
  createBrainItem,
  deleteBrainItem,
  fetchTaskDetail,
  fetchWorkspaceFile,
  updateBrainItem,
  type TaskDetail as Detail,
} from "@/lib/backend/brain";

const STATUS_LABEL: Record<string, string> = {
  // Not "Planning" on its own — it should read as where the *plan* is, not as an instruction.
  planning: "Plan ready for your look",
  approved: "Approved",
  working: "Working",
  done: "Done",
  dropped: "Dropped",
};
const PRIORITIES = ["high", "normal", "low"];

/**
 * When a note was written, at the precision that is actually useful.
 *
 * Time alone today, weekday plus time this week, a date beyond it. The same shape
 * `formatModified` uses for files — it takes epoch seconds and these are ISO strings, which is
 * the only reason this is not a call to it.
 *
 * It matters more here than on a file: a run leaves a dozen notes on one task, and "which of
 * these happened before I told him to stop" is unanswerable without the clock.
 */
function when(iso: string): string {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const time = at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (at.toDateString() === new Date().toDateString()) return time;
  const days = (Date.now() - at.getTime()) / 86_400_000;
  if (days < 7) return `${at.toLocaleDateString(undefined, { weekday: "short" })} ${time}`;
  return at.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** The full-page view of one task: description, checklist, deliverables, and the
 * comment thread (you + him). Rendered in place of the tab content, not a panel. */
export function TaskDetailPage({
  taskId,
  projects,
  trail,
  onBack,
  onChanged,
}: {
  taskId: number;
  /** For moving the task between projects, and for its milestones — the field that decides
   *  when he actually gets to it. */
  projects: {
    id: number;
    name: string;
    milestones?: { id: number; title: string; status: string }[];
  }[];
  /** Where "back" lands — the project you came in from, if any. */
  trail?: string;
  onBack: () => void;
  onChanged: () => void;
}) {
  const confirm = useConfirm();
  const [task, setTask] = useState<Detail | null>(null);
  const [reply, setReply] = useState("");
  const [item, setItem] = useState("");
  // Collapsed until asked for. A task in `planning` is the exception — the plan is the whole
  // reason you opened it, and being asked to approve something you have to click to see is the
  // shape of the bug this section exists to fix.
  const [planOpen, setPlanOpen] = useState(false);

  const load = useCallback(() => {
    fetchTaskDetail(taskId)
      .then(setTask)
      .catch(() => {});
  }, [taskId]);
  useEffect(() => {
    load();
  }, [load]);
  // A task awaiting approval opens with its plan already showing: being asked to approve
  // something you have to click to read is the same failure this section was added to fix, one
  // click further in. Only ever opens — it never slams shut on a status change you did not make.
  useEffect(() => {
    if (task?.status === "planning") setPlanOpen(true);
  }, [task?.status]);

  /**
   * Keep the page current while it is open.
   *
   * It loaded once and then never again, which is the wrong behaviour for the one screen you
   * are most likely to be watching *while he works*: he ticks a checklist item, adds a
   * comment, attaches a deliverable, and none of it appeared until you navigated away and
   * back. Faster while the task is his current one, because that is when things change.
   *
   * The guard matters as much as the poll. Replacing state underneath someone who is typing
   * in the description would throw their sentence away, so a refresh is skipped whenever a
   * field on this page has focus — you cannot lose an edit to a background fetch.
   */
  const active = task?.status === "working";
  useEffect(() => {
    const tick = () => {
      // Skip only when there is genuinely an edit in flight — a field with something typed in
      // it. The first version skipped whenever *any* field had focus, and the comment box is
      // a field people leave focused: one click on it and the page stopped updating
      // altogether, which is worse than the problem this guard exists to prevent.
      const editing = document.activeElement;
      const midEdit =
        editing instanceof HTMLElement &&
        (editing.isContentEditable ||
          ((editing instanceof HTMLInputElement || editing instanceof HTMLTextAreaElement) &&
            editing.value.trim().length > 0));
      if (!midEdit) load();
    };
    const timer = window.setInterval(tick, active ? 3_000 : 12_000);
    // Coming back to the window is the other moment you expect it to be current.
    window.addEventListener("focus", tick);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", tick);
    };
  }, [active, load]);

  const refresh = () => {
    load();
    onChanged();
  };
  const patch = async (data: Record<string, unknown>) => {
    await updateBrainItem("task", taskId, data);
    refresh();
  };
  const sendReply = async () => {
    if (!reply.trim()) return;
    await createBrainItem("task_comment", { task_id: taskId, body: reply.trim() });
    setReply("");
    refresh();
  };
  const addItem = async () => {
    if (!item.trim()) return;
    await createBrainItem("checklist_item", { task_id: taskId, text: item.trim() });
    setItem("");
    refresh();
  };
  const toggleItem = async (id: number, done: boolean) => {
    await updateBrainItem("checklist_item", id, { done });
    refresh();
  };
  const del = async (kind: string, id: number) => {
    await deleteBrainItem(kind, id);
    refresh();
  };
  /** Deliverables are work he produced — always ask first. */
  const delDeliverable = async (d: Detail["deliverables"][number]) => {
    const ok = await confirm({
      title: "Delete this deliverable?",
      description: "It disappears from the task. The file on his computer stays.",
      subject: d.title,
      destructive: true,
    });
    if (ok) await del("deliverable", d.id);
  };
  const downloadDeliverable = async (d: Detail["deliverables"][number]) => {
    let text = d.content;
    if (d.kind === "file") {
      try {
        // Anchored to the task's project, same as the viewer and Open on this machine. Without
        // it a relative path resolved against the global workspace root, which is not where he
        // wrote it — and the `catch` below turned that miss into a silent success: the download
        // arrived containing the path as its text instead of the file.
        text = (await fetchWorkspaceFile(d.content, task?.project_id ?? null)).content;
      } catch {
        /* fall back to the path */
      }
    }
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download =
      (d.title || "deliverable").replace(/\s+/g, "_") + (d.kind === "file" ? "" : ".txt");
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!task) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading task…
      </div>
    );
  }

  const done = task.checklist.filter((c) => c.done).length;
  const pct = task.checklist.length ? Math.round((done / task.checklist.length) * 100) : 0;

  // What holds this task back, and which milestones it could belong to. Both come from the
  // roadmap: a task's status says what someone intends, and only the graph says whether he
  // is allowed to act on it.
  const held = task.held_by ?? [];
  const milestones = projects.find((one) => one.id === task.project_id)?.milestones ?? [];

  return (
    <div className="flex h-full flex-col">
      {/* Header: pinned, so what the task IS never scrolls away from the discussion of it. */}
      <div className="border-border/60 shrink-0 border-b px-6 pt-5 pb-4 md:px-8">
        {/* breadcrumb + back */}
        <button
          onClick={onBack}
          className="text-muted-foreground hover:text-foreground mb-3 inline-flex items-center gap-1.5 text-sm transition-colors"
        >
          <ArrowLeft className="size-4" />
          {trail ?? "Projects"}
          <ChevronRight className="size-3.5 text-muted-foreground/50" />
          <span className="font-mono">#{task.id}</span>
        </button>

        {/* header: what it is, and whether it can be worked on */}
        <div>
          <div className="flex items-start gap-3">
            <span className="bg-muted/70 mt-1 flex size-9 shrink-0 items-center justify-center rounded-lg text-emerald-500">
              <ListChecks className="size-4" />
            </span>
            <input
              value={task.goal}
              onChange={(e) => setTask({ ...task, goal: e.target.value })}
              onBlur={(e) => e.target.value !== task.goal && patch({ goal: e.target.value })}
              title="Click to rename"
              className="focus:ring-ring/25 hover:bg-accent/40 focus:bg-card/70 -mx-2 min-w-0 flex-1 rounded-lg bg-transparent px-2 py-1 text-xl font-semibold tracking-tight outline-none transition-colors focus:ring-[3px]"
            />
          </div>

          {/* Whether this is actually workable, stated first, because a status of "planned" on a
            task the roadmap is holding back is the page telling you something untrue about
            the most important thing on it. */}
          {held.length > 0 ? (
            <div className="border-orange-400/30 bg-orange-400/5 mt-3 ml-12 flex items-start gap-2.5 rounded-xl border px-3 py-2.5">
              <Lock className="mt-0.5 size-3.5 shrink-0 text-orange-400/90" />
              <p className="text-xs leading-relaxed">
                <span className="text-orange-400/90">Not available yet.</span>{" "}
                <span className="text-muted-foreground">
                  It waits for {held.join(", ")} — he will not pick it up until that is done,
                  whatever its status says.
                </span>
              </p>
            </div>
          ) : null}
        </div>

      </div>

      {/*
        Content on the left, attributes on the right — which is what Linear, Jira, KissFlow and
        Asana all do, and this page had backwards.

        The properties used to be a six-across grid pinned in the header: a band of boxed form
        controls above the content, the loudest thing on the page, for six values that are read far
        more often than they are changed. Meanwhile the *sidebar* held the notes, so his dense
        technical log — paths, commands, test counts — was squeezed into a 24rem gutter while a
        dropdown for "Raised by: himself" got prime horizontal real estate.

        Swapped: attributes become a quiet vertical list in the sidebar, and the description,
        checklist, deliverables and activity get the width. It also means the header is now just
        what the task IS, which is the one thing that should never scroll away.
      */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row-reverse lg:overflow-hidden">
        <aside className="border-border/60 bg-card/20 shrink-0 border-b lg:w-[17rem] lg:border-b-0 lg:border-s xl:w-[19rem] lg:overflow-y-auto">
          <div className="grid grid-cols-2 gap-x-5 gap-y-3 px-5 py-4 sm:grid-cols-3 lg:grid-cols-1">
          {/* Shown, not set. Eight statuses were maintained by hand and were wrong often
              enough to be worth removing rather than fixing — 67 done, 4 waiting, 3 planned,
              1 working, and nothing ever in `review`. He owns every transition now; the one
              decision that is a person's is approving a plan, and that happens in chat where
              the plan can actually be discussed. */}
          <Prop label="Status">
            <span className="block py-1.5 text-sm">{STATUS_LABEL[task.status] ?? task.status}</span>
          </Prop>
          <Prop label="Priority">
            <Dropdown
              value={task.priority}
              onChange={(v) => patch({ priority: v })}
              options={PRIORITIES}
              className="w-full"
              ariaLabel="Priority"
            />
          </Prop>
          {/* Shown, not editable, and it was never really editable: `update_task` has no
              project_id parameter, so this was a dropdown that let you choose, updated
              optimistically, and was ignored by the server.

              It should not be editable either. A task's milestone belongs to a project, so
              changing the project underneath it would leave the task pointing at a milestone
              in a different one — which is the inconsistency set_task_milestone exists to
              prevent. The way to move work is the milestone, which is the field next to
              this one. */}
          <Prop label="Project">
            <span className="text-muted-foreground block truncate py-1.5 text-sm">
              {projects.find((one) => one.id === task.project_id)?.name ?? "No project"}
            </span>
          </Prop>
          {/* The field that decides when he gets to it. It was not on this page at all, which
            meant the one attribute that gates the work was the one you could not see. */}
          <Prop label="Milestone">
            <Dropdown
              value={task.milestone_id == null ? "none" : String(task.milestone_id)}
              onChange={(v) => patch({ milestone_id: v === "none" ? null : Number(v) })}
              options={[
                { value: "none", label: "No milestone" },
                ...milestones.map((m) => ({
                  value: String(m.id),
                  label: m.status === "done" ? `${m.title} ✓` : m.title,
                })),
              ]}
              className="w-full"
              ariaLabel="Milestone"
            />
          </Prop>
          <Prop label="Raised by">
            <span className="text-muted-foreground py-1.5 text-sm">
              {task.created_by === "user" ? "you" : "himself"}
            </span>
          </Prop>
          </div>
        </aside>

        {/*
          A column with a scrolling body and a fixed footer, rather than one long scroll with the
          composer somewhere at the end of it.

          Sticky was tried first and cannot win here: this container comes out 906px tall inside a
          900px viewport — a 6px overflow that predates any of this — and `sticky bottom-0` anchors
          to the *container's* padding edge, which lands 14px above the viewport with the log
          showing through the gap. A flex footer does not care how tall the column is; the body
          takes the remaining space and the bar is always the last thing on screen.
        */}
        <div className="flex min-w-0 flex-1 flex-col lg:overflow-hidden">
          <div className="min-h-0 flex-1 space-y-7 overflow-y-auto px-6 py-5 md:px-8">
          <section>
            <H>Description</H>
            {/* Click to edit, so the Markdown he writes here is actually seen. This was
                a permanent textarea, which made the field he writes most Markdown into
                the one place it could never render.

                No box around it any more. A bordered, tinted panel says "form field you are
                expected to fill in"; this is the brief, and it is read far more than it is
                edited. Capped near 70 characters a line for the same reason a book is — a
                300-word brief running the full width of a desktop window is a wall. */}
            <div className="max-w-[68ch] text-[15px] leading-relaxed">
              <EditableText
                value={task.description}
                multiline
                placeholder="What this task is, and what 'done' looks like…"
                render={(v) => <Markdown>{v}</Markdown>}
                onSave={(v) => {
                  setTask({ ...task, description: v });
                  patch({ description: v });
                }}
              />
            </div>
          </section>

          {/* The plan, on the task. It was written to `.kith/work/task-<id>.md` and shown on the
              approval bar, and nowhere on the page you open to read the task — which produced
              "where the fuck is plan on task do you not attach plan on tasks" four days after the
              planning gate shipped.

              Read-only, and collapsed by default. Read-only because the file is the source of
              truth and an editable copy here would be a second one; collapsed because a real plan
              is several hundred words and this section sits above the checklist, which is what you
              open a working task to see. */}
          {task.plan ? (
            <section>
              <Collapsible open={planOpen} onOpenChange={setPlanOpen}>
                <CollapsibleTrigger className="group flex w-full items-center gap-1.5 text-left">
                  <H>Plan</H>
                  <ChevronRight
                    className="mb-1.5 size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-90"
                    aria-hidden
                  />
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="max-w-[68ch] text-[15px] leading-relaxed">
                    <Markdown>{task.plan}</Markdown>
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </section>
          ) : null}

          <section>
            <H>
              Checklist
              {task.checklist.length ? (
                <span className="ml-2 inline-flex items-center gap-2 font-normal normal-case tracking-normal text-muted-foreground">
                  <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full rounded-full bg-kith transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </span>
                  {done}/{task.checklist.length}
                </span>
              ) : null}
            </H>
            <ul className="space-y-1">
              {task.checklist.map((c) => (
                <li
                  key={c.id}
                  className="group flex items-center gap-2.5 rounded-md px-1 py-1 text-sm hover:bg-accent/40"
                >
                  <button
                    onClick={() => toggleItem(c.id, !c.done)}
                    className={cn(
                      "flex size-4 shrink-0 items-center justify-center rounded border transition-colors",
                      c.done
                        ? "border-kith bg-kith text-primary-foreground"
                        : "border-muted-foreground/40 hover:border-kith",
                    )}
                    aria-label="Toggle"
                  >
                    {c.done ? <Check className="size-3" /> : null}
                  </button>
                  {/* Muted, not struck through. The tick already says it is done, so the line was
                      encoding the same fact twice — and these wrap to two and three lines, where a
                      rule drawn through the middle of a sentence about "Auto/model selection, and
                      thinking-only filtering" stops being decoration and starts being unreadable. */}
                  <span className={cn("min-w-0 flex-1", c.done && "text-muted-foreground/60")}>
                    <MarkdownInline>{c.text}</MarkdownInline>
                  </span>
                  <button
                    onClick={() => del("checklist_item", c.id)}
                    className="text-muted-foreground opacity-0 transition group-hover:opacity-100 hover:text-destructive"
                    aria-label="Delete"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-2 flex items-center gap-2 rounded-lg border border-border/60 bg-card/40 p-1.5 pl-3 focus-within:border-ring/60">
              <input
                value={item}
                onChange={(e) => setItem(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addItem()}
                placeholder="Add a step…"
                className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/55"
              />
              <Button size="xs" variant="outline" onClick={addItem}>
                <Plus className="size-3.5" />
                Add
              </Button>
            </div>
          </section>

          <section>
            <H>
              Deliverables
              {task.deliverables.length ? (
                <span className="ml-2 font-normal normal-case tracking-normal text-muted-foreground">
                  {task.deliverables.length}
                </span>
              ) : null}
            </H>
            {task.deliverables.length === 0 ? (
              <p className="rounded-xl border border-dashed border-border/70 px-3 py-4 text-center text-sm text-muted-foreground">
                Nothing produced yet.
              </p>
            ) : (
              <ul className="space-y-2">
                {task.deliverables.map((d) => (
                  <DeliverableRow
                    key={d.id}
                    d={d}
                    projectId={task.project_id}
                    onDelete={() => delDeliverable(d)}
                    onDownload={() => downloadDeliverable(d)}
                  />
                ))}
              </ul>
            )}
          </section>

          {/*
            Activity last, in the main column, at full width.

            It was a fixed 24rem sidebar with `hidden xl:flex` — so below 1280px the notes and the
            box to write one were not narrowed or collapsed, they were **gone**, with nothing behind
            them. On a laptop or an unmaximised window there was no way to read what he recorded on
            a task or to answer him on it.

            Below the content rather than beside it, because that is where a log belongs when the
            log is the longest thing on the page, and because his entries are dense technical prose
            — paths, commands, test counts — that a narrow gutter mangles.
          */}
          <section>
            <H>
              Activity
              {task.comments.length ? (
                <span className="text-muted-foreground ml-2 font-normal tracking-normal normal-case">
                  {task.comments.length}
                </span>
              ) : null}
            </H>
            <div className="max-w-[68ch] space-y-4">
            {task.comments.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center">
                <span className="flex size-9 items-center justify-center rounded-xl bg-muted/70 text-muted-foreground">
                  <MessageCircle className="size-4" />
                </span>
                <p className="max-w-[22ch] text-xs leading-relaxed text-muted-foreground">
                  No comments yet — write to him here and he'll pick it up.
                </p>
              </div>
            ) : (
              /*
                A work log, not a chat.

                These were chat bubbles — `rounded-2xl` with a tail, a presence dot, his side left
                and yours right. What actually goes in them is "Fresh frontend Vitest/build and
                backend regression verification passed, but audit found the required backend
                OpenRouter discovery/import routes are absent; recorded the gap in
                `work/task-48.md`". Dense engineering notes with paths and commands in them, dressed
                as text messages — which is most of why this page read as toy.

                So: a flat column of timestamped entries, full width, his Markdown intact. Yours are
                still distinguishable, because who said it changes what it means — an instruction
                from you outranks a note from him — but by a rule and a label rather than by being
                thrown to the other side of the panel.
              */
              task.comments.map((c) => {
                const theirs = c.author === "user";
                return (
                  <article
                    key={c.id}
                    className={cn(
                      "border-s-2 ps-3",
                      theirs ? "border-kith/50" : "border-border/70",
                    )}
                  >
                    <header className="mb-0.5 flex items-baseline gap-2">
                      <span
                        className={cn(
                          "text-[11px] font-medium",
                          theirs ? "text-kith" : "text-muted-foreground",
                        )}
                      >
                        {theirs ? "You" : "Kith"}
                      </span>
                      <time className="text-muted-foreground/50 font-mono text-[10px] tabular-nums">
                        {when(c.created_at)}
                      </time>
                    </header>
                    <div className="text-sm leading-relaxed">
                      {/* His progress notes are where he writes lists and links,
                            and this was the one place his Markdown showed raw. */}
                      <Markdown>{c.body}</Markdown>
                    </div>
                  </article>
                );
              })
            )}
            </div>
          </section>
          </div>

          {/*
            Always on screen. Moving activity into the main column took the composer with it, so
            writing to him meant scrolling past the description, the checklist, nine deliverables
            and every entry in the log to reach a box at the very bottom — worse than the sidebar it
            replaced, which at least kept it in view.

            A sibling of the scrolling body, inside the column — not a sibling of the *column*,
            which would have made it a third item in the `lg:flex-row-reverse` row and laid it out
            beside the sidebar.
          */}
          <div className="border-border/60 bg-background/95 shrink-0 border-t px-6 py-3 md:px-8">
            <div className="max-w-[68ch]">
              <div className="border-border/60 bg-card/40 focus-within:border-ring/60 flex items-end gap-2 rounded-lg border p-1.5">
                <textarea
                  rows={1}
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void sendReply();
                    }
                  }}
                  placeholder="Comment on this task…"
                  className="max-h-24 min-h-8 flex-1 resize-none bg-transparent px-2 py-1 text-sm outline-none"
                />
                <Button
                  size="icon"
                  className="size-8 shrink-0 rounded-full"
                  onClick={sendReply}
                  disabled={!reply.trim()}
                  aria-label="Send"
                >
                  <Send className="size-4" />
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** One produced thing. Files and text open in the preview dialog; links just
 * go out to the web. */
function DeliverableRow({
  d,
  projectId,
  onDelete,
  onDownload,
}: {
  d: Detail["deliverables"][number];
  projectId: number | null;
  onDelete: () => void;
  onDownload: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState<string | null>(d.kind === "text" ? d.content : null);
  const [err, setErr] = useState("");
  const isLink = d.kind === "link";
  const isFile = d.kind === "file";
  // A text deliverable is his prose — read it as Markdown, like a file would be.
  const name = isFile ? d.content : `${(d.title || "deliverable").replace(/\s+/g, "-")}.md`;
  const sub = isLink
    ? d.content
    : isFile
      ? d.content
      : `${d.content.length.toLocaleString()} chars`;

  const preview = () => {
    setOpen(true);
    // A screenshot he attached as a deliverable is shown by the viewer from its own
    // bytes, so it is not read as text here — that only ever produced a decode error.
    if (body == null && isFile && !skipTextRead(d.content)) {
      fetchWorkspaceFile(d.content, projectId)
        .then((f) => setBody(f.content))
        .catch((e) => setErr(e instanceof Error ? e.message : "couldn't read that file"));
    }
  };

  return (
    <li className="group flex items-center gap-3 rounded-xl border border-border/70 bg-card/50 p-3 shadow-sm transition-all hover:border-border hover:shadow-md">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-muted/70 text-sky-500">
        {isLink ? (
          <Link2 className="size-4" />
        ) : isFile ? (
          <FileCode2 className="size-4" />
        ) : (
          <FileText className="size-4" />
        )}
      </span>
      {isLink ? (
        <a href={d.content} target="_blank" rel="noreferrer" className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium transition-colors group-hover:text-kith">
            {d.title}
          </span>
          <span className="block truncate font-mono text-[11px] text-muted-foreground">{sub}</span>
        </a>
      ) : (
        <button onClick={preview} className="min-w-0 flex-1 text-left" title="Open preview">
          <span className="block truncate text-sm font-medium transition-colors group-hover:text-kith">
            {d.title}
          </span>
          <span className="block truncate font-mono text-[11px] text-muted-foreground">{sub}</span>
        </button>
      )}
      <div className="flex shrink-0 items-center gap-0.5">
        {isLink ? (
          <RowAction
            label="Open link"
            onClick={() => window.open(d.content, "_blank", "noreferrer")}
            icon={<ArrowUpRight className="size-4" />}
          />
        ) : (
          <>
            <RowAction
              label="Open preview"
              onClick={preview}
              icon={<Expand className="size-4" />}
            />
            <RowAction
              label="Download"
              onClick={onDownload}
              icon={<Download className="size-4" />}
            />
          </>
        )}
        <RowAction
          label="Delete"
          onClick={onDelete}
          icon={<Trash2 className="size-3.5" />}
          danger
        />
      </div>
      {open ? (
        <FileViewer
          open={open}
          onOpenChange={setOpen}
          name={name}
          title={d.title || name}
          content={body}
          error={err || undefined}
          badge={
            <span className="shrink-0 rounded bg-kith-soft px-1.5 py-0.5 font-medium text-kith">
              deliverable
            </span>
          }
          onDownload={onDownload}
          projectId={projectId}
          onOpenOnHost={
            isFile ? (reveal) => openWorkspaceFile(d.content, reveal, projectId) : undefined
          }
        />
      ) : null}
    </li>
  );
}

function RowAction({
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
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
        danger &&
          "opacity-0 group-hover:opacity-100 hover:bg-destructive/10 hover:text-destructive",
      )}
    >
      {icon}
    </button>
  );
}

/**
 * A section label.
 *
 * Was uppercase and letterspaced, which on a page with five of them (DESCRIPTION, CHECKLIST,
 * DELIVERABLES, ACTIVITY, plus every property) reads as shouting and flattens the hierarchy —
 * everything is emphasised, so nothing is. Sentence case with a hairline under it separates the
 * sections without competing with the content inside them.
 */
function H({ children }: { children: ReactNode }) {
  return (
    <div className="border-border/50 text-muted-foreground mb-2.5 flex items-center border-b pb-1.5 text-[13px] font-medium">
      {children}
    </div>
  );
}

/**
 * One attribute in the sidebar: a quiet label above its value.
 *
 * Sentence case rather than uppercase — six of these stacked in a narrow column was six lines of
 * letterspaced capitals, which is a lot of noise for "Status", "Priority", "Due".
 */
function Prop({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="min-w-0">
      <span className="text-muted-foreground/70 mb-1 block text-[11px]">{label}</span>
      {children}
    </label>
  );
}
