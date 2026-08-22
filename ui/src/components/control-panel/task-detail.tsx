import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
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
  Plus,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Dropdown } from "@/components/ui/dropdown";
import { useConfirm } from "@/components/ui/confirm";
import { FileViewer, Markdown, MarkdownInline, skipTextRead } from "@/components/files";
import { EditableText } from "@/components/ui/editable-text";
import { dayLabel, time } from "@/lib/dates";
import { keys } from "@/lib/query-keys";
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

/** The full-page view of one task: its plan, description, checklist and deliverables.
 * Rendered in place of the tab content, not a panel. */
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
  const cache = useQueryClient();
  const [item, setItem] = useState("");
  /** A download that could not be read. Said out loud, because the alternative — which this
   *  page shipped with — was handing you a .txt containing the path and calling it done. */
  const [refused, setRefused] = useState("");
  // The title being typed, held apart from the loaded task — see the input below for why.
  const [goalDraft, setGoalDraft] = useState<string | null>(null);
  const goalCancelled = useRef(false);
  // Collapsed until asked for. A task in `planning` is the exception — the plan is the whole
  // reason you opened it, and being asked to approve something you have to click to see is the
  // shape of the bug this section exists to fix.
  const [planOpen, setPlanOpen] = useState(false);

  const { data: task = null } = useQuery({
    queryKey: keys.task(taskId),
    queryFn: () => fetchTaskDetail(taskId),
  });
  const load = useCallback(
    () => void cache.invalidateQueries({ queryKey: keys.task(taskId) }),
    [cache, taskId],
  );
  // A task awaiting approval opens with its plan already showing: being asked to approve
  // something you have to click to read is the same failure this section was added to fix, one
  // click further in. Only ever opens — it never slams shut on a status change you did not make.
  useEffect(() => {
    if (task?.status === "planning") setPlanOpen(true);
  }, [task?.status]);

  /* Kept current by `task` events, through `STALE_ON` — he ticks a checklist item, adds a comment,
   * attaches a deliverable, and it appears.
   *
   * What was here: a 30-second interval, a `focus` listener, a DOM event to bridge them, and a
   * guard that skipped any refresh while a field on the page had something typed in it. All four
   * are gone, and the guard is worth a sentence because deleting a guard should never be quiet.
   *
   * It was protecting against server data replacing what someone was typing — and none of these
   * fields is written from server data while it is being edited. `EditableText` copies `value` into
   * its own draft when you click the pencil and never reads it again until you are done; the title
   * renders `goalDraft ?? task.goal`, where the draft wins; the new-checklist-item box is local.
   * The two controls that *are* driven by the task — the priority and milestone selects — hold no
   * typed text to lose. So the guard was covering a case the components already handle, at the cost
   * of a page that stopped updating while you had a comment box focused.
   */
  const refresh = () => {
    load();
    onChanged();
  };
  const patch = async (data: Record<string, unknown>) => {
    await updateBrainItem("task", taskId, data);
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
  /* Download the file, rather than a description of it.
   *
   * This read the deliverable through `fetchWorkspaceFile`, which returns *text*, and wrote that
   * into a `text/plain` blob. Two things were wrong with it and the comment it carried only
   * admitted the smaller one.
   *
   * The `catch` fell back to `d.content` — the path — so a file it could not read was saved as a
   * text file containing its own path, and the download looked like it had worked.
   *
   * And on this page it could not have worked anyway: five of the seven deliverables here are
   * `.docx` and `.pdf`. Decoding bytes as text and re-encoding them as UTF-8 corrupts them, so
   * even the success path produced a file Word would refuse. Nothing said so, because a
   * corrupted download is a file you find out about later.
   *
   * `/api/workspace/raw` already serves the bytes with the real content type — it is what the
   * PDF viewer reads — so this takes the blob it returns and saves that. The global `fetch`
   * wrapper attaches the API token, so no headers are needed here. And a failure is now a
   * failure: it says so instead of handing you a path in a .txt.
   */
  const downloadDeliverable = async (d: Detail["deliverables"][number]) => {
    let blob: Blob;
    let name: string;

    if (d.kind === "file") {
      const project = task?.project_id;
      const url =
        `/api/workspace/raw?path=${encodeURIComponent(d.content)}` +
        (project ? `&projectId=${project}` : "");
      const response = await fetch(url).catch(() => null);
      if (!response?.ok) {
        setRefused(`Couldn't read ${d.content} — it may have been moved or renamed.`);
        return;
      }
      blob = await response.blob();
      // The file's own name, so the extension survives. A title with the spaces replaced was
      // what it used before, which is how `…Specification.docx` arrived as `…Specification`
      // and opened in nothing.
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
            <span
              title={STATUS_LABEL[task.status] ?? task.status}
              className={cn(
                "bg-muted/70 mt-1 flex size-9 shrink-0 items-center justify-center rounded-lg",
                task.status === "done" ? "text-roam" : task.status === "working" ? "text-kith" : "text-muted-foreground",
              )}
            >
              <ListChecks className="size-4" />
            </span>
            {/* The draft lives outside `task`, and that is the whole fix.
                `onChange` used to write the typed text straight into `task`, so by the time
                `onBlur` ran its `e.target.value !== task.goal` guard the two were always equal
                and `patch` never fired — you could rename a task, click away, and watch the
                30-second poll put the old title back. Held here, the comparison has something
                to compare against. */}
            <input
              value={goalDraft ?? task.goal}
              onChange={(e) => setGoalDraft(e.target.value)}
              onBlur={() => {
                const next = goalDraft?.trim();
                setGoalDraft(null);
                // Escape blurs the field, and that blur must not be read as a commit. The blur
                // fires synchronously from inside the keydown handler, before React flushes
                // state, so a ref is the only thing this can trust.
                if (goalCancelled.current) {
                  goalCancelled.current = false;
                  return;
                }
                // A blank title is a slip, not an instruction.
                if (!next || next === task.goal) return;
                // Straight into the cache: the field it came from is a draft, and the rendered
                // title should not wait a round trip to catch up with it.
                cache.setQueryData(keys.task(taskId), { ...task, goal: next });
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
              title="Click to rename"
              className="focus:ring-ring/25 hover:bg-accent/40 focus:bg-card/70 -mx-2 min-w-0 flex-1 rounded-lg bg-transparent px-2 py-1 text-xl font-semibold tracking-tight outline-none transition-colors focus:ring-[3px]"
            />
          </div>

          {/* Whether this is actually workable, stated first, because a status of "planned" on a
            task the roadmap is holding back is the page telling you something untrue about
            the most important thing on it. */}
          {held.length > 0 ? (
            <div className="border-kith/25 bg-kith-soft mt-3 ml-12 flex items-start gap-2.5 rounded-xl border px-3 py-2.5">
              <Lock className="mt-0.5 size-3.5 shrink-0 text-kith" />
              <p className="text-xs leading-relaxed">
                <span className="text-kith">Not available yet.</span>{" "}
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
            {/* "Plan ready for your look" is a claim about there being a plan, and #93 on the real
                board says it with none filed. The label tells the truth about which of the two
                situations you are in, because the difference decides whether there is anything
                for you to do. */}
            <span className="block py-1.5 text-sm">
              {task.status === "planning" && !task.plan
                ? "Planning — no plan yet"
                : (STATUS_LABEL[task.status] ?? task.status)}
            </span>
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
                // A milestone id with no milestone behind it. Without an option to match, the
                // dropdown renders the bare number as though it were a name — #91, #92 and #93 all
                // showed "30", and there is no milestone 30. Naming it says what is wrong and
                // leaves "No milestone" selectable, which is the fix.
                ...(task.milestone_id != null && !milestones.some((m) => m.id === task.milestone_id)
                  ? [{ value: String(task.milestone_id), label: `#${task.milestone_id} — no longer exists` }]
                  : []),
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
                  cache.setQueryData(keys.task(taskId), { ...task, description: v });
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
                {/* The chevron goes *inside* `H`, the way the checklist's progress bar does, and
                    the trigger is a plain block. `H` is a block with a bottom border, so as a flex
                    *item* it shrank to the word — every other section got a full-width rule and
                    this one got a stub underline under "Plan". */}
                <CollapsibleTrigger className="group w-full text-left">
                  <H>
                    Plan
                    <ChevronRight
                      className="text-muted-foreground ml-1.5 size-3.5 transition-transform group-data-[state=open]:rotate-90"
                      aria-hidden
                    />
                  </H>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="max-w-[68ch] text-[15px] leading-relaxed">
                    <Markdown>{task.plan}</Markdown>
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </section>
          ) : task.status === "planning" ? (
            /* A task whose status says "plan ready for your look" and has no plan is the exact
               thing the four-status change is meant to stop being possible — and on the real board
               #91, #92 and #93 all look like this. Saying so beats rendering nothing: an absent
               section reads as "no plan section on this page", not as "there is no plan". */
            <section>
              <H>Plan</H>
              <p className="text-muted-foreground max-w-[68ch] text-sm">
                No plan filed yet, so there is nothing to approve. He writes one to{" "}
                <code className="text-[13px]">.kith/work/task-{task.id}.md</code>.
              </p>
            </section>
          ) : null}

          <section>
            <H>
              Checklist
              {task.checklist.length ? (
                <span className="ml-2 inline-flex items-center gap-2 font-normal normal-case tracking-normal text-muted-foreground">
                  <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full rounded-full bg-roam transition-all"
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
                        ? "border-roam bg-roam text-background"
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
            {refused ? (
              <p className="text-destructive/90 border-destructive/30 bg-destructive/5 mb-2 rounded-lg border px-3 py-2 text-[12.5px]">
                {refused}
              </p>
            ) : null}
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

          {/* No activity thread, and no box to write in one.

              620 comments across the board, every one of them raising a notification — "New note
              on …", four inside twenty minutes on one task — with the message that actually wanted
              an answer somewhere underneath. Each thing the thread was used for has a better home
              that already existed: progress in the task's working file (which is where the plan
              above is read from), a question through `ask` in the conversation where it can be
              answered, and evidence in the checklist and the deliverables.

              The rows are still in the database. Nothing here reads them. */}
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

  /* What the row can say beyond its name and its path.
   *
   * It said neither the date it was filed nor anything about the file, which for a page whose
   * whole job is "what did this task produce" leaves out most of the answer — seven rows that
   * differ only in a filename, with no way to tell the one he wrote this morning from the one
   * from three weeks ago, or the finished document from the 2KB stub.
   *
   * Four facts, in the order you would ask for them, and each omitted rather than faked when it
   * is not known. `modified` only appears when it genuinely differs from `created_at` by more
   * than a minute — on most deliverables they are the same moment, and printing both would be
   * two timestamps saying one thing. */
  const kindLabel = isLink ? "link" : isFile ? extensionOf(d.content) : "text";
  const facts = [
    kindLabel,
    d.bytes !== undefined ? formatBytes(d.bytes) : null,
    d.created_at ? `filed ${dayLabel(d.created_at)}` : null,
    d.modified && changedSince(d.created_at, d.modified)
      ? `edited ${dayLabel(d.modified)}`
      : null,
  ].filter(Boolean) as string[];

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
      <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-muted/70 text-muted-foreground">
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
          <span className="block truncate text-sm font-medium transition-colors group-hover:text-foreground">
            {d.title}
          </span>
          <span className="block truncate font-mono text-[11px] text-muted-foreground">{sub}</span>
        </a>
      ) : (
        <button onClick={preview} className="min-w-0 flex-1 text-left" title="Open preview">
          <span className="block truncate text-sm font-medium transition-colors group-hover:text-foreground">
            {d.title}
          </span>
          <span className="block truncate font-mono text-[11px] text-muted-foreground">{sub}</span>
          {facts.length ? (
            <span
              className="text-muted-foreground/60 mt-0.5 block truncate text-[11px]"
              title={
                d.created_at
                  ? `Filed ${dayLabel(d.created_at)} ${time(d.created_at)}` +
                    (d.modified ? `\nFile last changed ${dayLabel(d.modified)} ${time(d.modified)}` : "")
                  : undefined
              }
            >
              {facts.join(" · ")}
            </span>
          ) : null}
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

/** `docs/a/b/Thing.docx` → `docx`. The last thing on the row that identifies what it *is*, and
 *  the reason a download has to keep the extension. Empty for a file with none. */
function extensionOf(path: string): string {
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "file";
}

/** Bytes, in the shortest form that is still honest. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/** Whether the file has really moved on since it was filed.
 *
 * A minute of slack, because the file is written and the deliverable filed in the same breath —
 * so they differ by milliseconds on almost everything, and showing both would be two timestamps
 * for one event. What is worth saying is the case where he came back and rewrote it. */
function changedSince(filed: string, modified: string): boolean {
  const a = new Date(filed).getTime();
  const b = new Date(modified).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return false;
  return b - a > 60_000;
}
