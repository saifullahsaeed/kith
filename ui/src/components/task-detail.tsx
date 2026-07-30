import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
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
import { Dropdown } from "@/components/ui/dropdown";
import { useConfirm } from "@/components/ui/confirm";
import { PresenceOrb } from "@/components/presence";
import { FilePreviewDialog, Markdown } from "@/components/file-view";
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
const FIELD_INPUT =
  "rounded-lg border bg-card/40 px-3 py-1.5 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:bg-card focus-visible:ring-[3px] focus-visible:ring-ring/25";

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
  /** For moving the task between projects. */
  projects: { id: number; name: string }[];
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

  return (
    <div>
      {/* breadcrumb + back */}
      <button
        onClick={onBack}
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        {trail ?? "Projects"}
        <ChevronRight className="size-3.5 text-muted-foreground/50" />
        <span className="font-mono">#{task.id}</span>
      </button>

      {/* header: title + meta + controls */}
      <div className="mb-6 border-b border-border/60 pb-5">
        <div className="flex items-start gap-3">
          <span className="mt-1 flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted/70 text-emerald-500">
            <ListChecks className="size-4" />
          </span>
          <input
            value={task.goal}
            onChange={(e) => setTask({ ...task, goal: e.target.value })}
            onBlur={(e) => e.target.value !== task.goal && patch({ goal: e.target.value })}
            title="Click to rename"
            className="-mx-2 min-w-0 flex-1 rounded-lg bg-transparent px-2 py-1 text-xl font-semibold tracking-tight outline-none transition-colors hover:bg-accent/40 focus:bg-card/70 focus:ring-[3px] focus:ring-ring/25"
          />
        </div>
        <div className="mt-2 ml-12 text-[11px] text-muted-foreground">
          <span>{task.created_by === "user" ? "from you" : "his own"}</span>
        </div>
        <div className="mt-4 ml-12 flex flex-wrap gap-3">
          <Field label="Status">
            <Dropdown
              value={task.status}
              onChange={(v) => patch({ status: v })}
              className="w-36"
              options={STATUSES.map((s) => ({ value: s, label: STATUS_LABEL[s] ?? s }))}
              ariaLabel="Status"
            />
          </Field>
          <Field label="Priority">
            <Dropdown
              value={task.priority}
              onChange={(v) => patch({ priority: v })}
              options={PRIORITIES}
              className="w-28"
              ariaLabel="Priority"
            />
          </Field>
          <Field label="Project">
            {/* Tasks live under projects now, so this is how one moves house. */}
            <Dropdown
              value={task.project_id == null ? "none" : String(task.project_id)}
              onChange={(v) => patch({ project_id: v === "none" ? null : Number(v) })}
              options={[
                { value: "none", label: "No project" },
                ...projects.map((p) => ({ value: String(p.id), label: p.name })),
              ]}
              className="w-44"
              ariaLabel="Project"
            />
          </Field>
          <Field label="Due">
            <input
              type="date"
              value={task.due_at ? task.due_at.slice(0, 10) : ""}
              onChange={(e) =>
                patch({ due_at: e.target.value ? `${e.target.value}T00:00:00+00:00` : null })
              }
              className={FIELD_INPUT}
            />
          </Field>
        </div>
      </div>

      {/* two-column: work on the left, conversation on the right */}
      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          <section>
            <H>Description</H>
            <textarea
              value={task.description}
              onChange={(e) => setTask({ ...task, description: e.target.value })}
              onBlur={(e) =>
                e.target.value !== task.description && patch({ description: e.target.value })
              }
              placeholder="What this task is, and what 'done' looks like…"
              className={`${FIELD_INPUT} min-h-24 w-full resize-y rounded-xl px-3.5 py-2.5 leading-relaxed`}
            />
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
                    {c.text}
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

        {/* conversation */}
        <div className="lg:col-span-2">
          <div className="flex flex-col rounded-xl border border-border/70 bg-card/40 lg:sticky lg:top-2 lg:max-h-[calc(100dvh-9rem)]">
            <div className="border-b border-border/60 px-4 py-3">
              <H>Conversation</H>
              <p className="text-xs text-muted-foreground">Talk to him about this task.</p>
            </div>
            <div className="min-h-40 flex-1 space-y-3 overflow-y-auto px-4 py-4">
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
            <div className="border-t border-border/60 p-3">
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
        <FilePreviewDialog
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60">
        {label}
      </span>
      {children}
    </label>
  );
}
