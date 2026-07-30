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
import { DatePicker } from "@/components/ui/date-picker";
import { Dropdown } from "@/components/ui/dropdown";
import { useConfirm } from "@/components/ui/confirm";
import { PresenceOrb } from "@/components/presence";
import { FileViewer, Markdown, MarkdownInline } from "@/components/file-view";
import { EditableText } from "@/components/ui/editable-text";
import { handOffAndOpen } from "@/lib/files";
import { cn } from "@/lib/utils";
import {
  createBrainItem,
  deleteBrainItem,
  fetchTaskDetail,
  fetchWorkspaceFile,
  updateBrainItem,
  type TaskDetail as Detail,
} from "@/lib/backend/brain";

const STATUSES = ["backlog", "todo", "doing", "waiting", "done", "dropped"];
const STATUS_LABEL: Record<string, string> = {
  backlog: "Backlog",
  todo: "To do",
  doing: "Doing",
  waiting: "Waiting on you",
  done: "Done",
  dropped: "Dropped",
};
const PRIORITIES = ["high", "normal", "low"];

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

  const load = useCallback(() => {
    fetchTaskDetail(taskId)
      .then(setTask)
      .catch(() => {});
  }, [taskId]);
  useEffect(() => {
    load();
  }, [load]);

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
  const active = task?.status === "doing";
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
        text = (await fetchWorkspaceFile(d.content)).content;
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

          {/* Whether this is actually workable, stated first, because a status of "todo" on a
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

        {/* Properties as a panel, not a row of labelled form fields. Every task system worth
          copying does it this way, and for a reason: these are attributes of the thing, read
          far more often than they are changed, so they want to be scannable rather than
          prominent. */}
        <div className="border-border/60 bg-card/30 mt-4 grid gap-x-6 gap-y-3 rounded-xl border px-4 py-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <Prop label="Status">
            <Dropdown
              value={task.status}
              onChange={(v) => patch({ status: v })}
              className="w-full"
              options={STATUSES.map((s) => ({ value: s, label: STATUS_LABEL[s] ?? s }))}
              ariaLabel="Status"
            />
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
          <Prop label="Due">
            <DatePicker
              value={task.due_at ? task.due_at.slice(0, 10) : ""}
              onChange={(day) => patch({ due_at: day ? `${day}T00:00:00+00:00` : null })}
              placeholder="No due date"
              ariaLabel="Due date"
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
      </div>

      {/* Two panels that scroll independently, which is what every task manager worth
          copying does and what the previous layout was not: the conversation was a column in
          the page flow, so reading old comments scrolled the description and the checklist
          off the screen, and on a wide window everything sat in a 64rem gutter with the rest
          of the display empty. A desktop app should use the window it was given. */}
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 space-y-6 overflow-y-auto px-6 py-5 md:px-8">
          <section>
            <H>Description</H>
            {/* Click to edit, so the Markdown he writes here is actually seen. This was
                a permanent textarea, which made the field he writes most Markdown into
                the one place it could never render. */}
            <div className="rounded-xl border border-border/60 bg-card/30 px-3.5 py-2.5">
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
                  <span
                    className={cn("min-w-0 flex-1", c.done && "text-muted-foreground line-through")}
                  >
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
                    onDelete={() => delDeliverable(d)}
                    onDownload={() => downloadDeliverable(d)}
                  />
                ))}
              </ul>
            )}
          </section>
        </div>

        {/* Pinned. Fixed width, its own scroll, composer always at the bottom — the shape a
            conversation panel has everywhere, and the reason it works: the thread can be
            arbitrarily long without ever moving the thing you are discussing. */}
        <aside className="border-border/60 bg-card/20 hidden w-[24rem] shrink-0 flex-col border-s xl:flex 2xl:w-[28rem]">
          <div className="border-border/60 shrink-0 border-b px-4 py-3">
            <H>Conversation</H>
            <p className="text-muted-foreground text-xs">Talk to him about this task.</p>
          </div>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
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
              task.comments.map((c) =>
                c.author === "user" ? (
                  <div key={c.id} className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-sm border border-kith/20 bg-kith-soft px-3 py-2 text-sm">
                      <p className="break-words whitespace-pre-wrap">{c.body}</p>
                    </div>
                  </div>
                ) : (
                  <div key={c.id} className="flex items-start gap-2">
                    <span className="mt-1.5">
                      <PresenceOrb size={7} />
                    </span>
                    <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-border/70 bg-card px-3 py-2 text-sm">
                      {/* His progress notes are where he writes lists and links,
                            and this was the one place his Markdown showed raw. */}
                      <Markdown>{c.body}</Markdown>
                    </div>
                  </div>
                ),
              )
            )}
          </div>
          <div className="border-border/60 shrink-0 border-t p-3">
            <div className="flex items-end gap-2 rounded-xl border border-border/60 bg-background/60 p-1.5 focus-within:border-ring/60">
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
        </aside>
      </div>
    </div>
  );
}

/** One produced thing. Files and text open in the preview dialog; links just
 * go out to the web. */
function DeliverableRow({
  d,
  onDelete,
  onDownload,
}: {
  d: Detail["deliverables"][number];
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
    if (body == null && isFile) {
      fetchWorkspaceFile(d.content)
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
          onOpenOnHost={isFile ? (reveal) => handOffAndOpen(d.content, reveal) : undefined}
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

function H({ children }: { children: ReactNode }) {
  return (
    <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
      {children}
    </div>
  );
}

function Prop({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="min-w-0">
      <span className="text-muted-foreground/70 mb-1 block text-[10px] tracking-wide uppercase">
        {label}
      </span>
      {children}
    </label>
  );
}
