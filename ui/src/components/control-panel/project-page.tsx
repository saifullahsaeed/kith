import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { DropdownMenu } from "radix-ui";
import { Check, ChevronDown, ChevronRight, Copy, Ellipsis, Folder, FolderKanban, MessageSquarePlus, Pencil, Trash2 } from "lucide-react";
import { Markdown } from "@/components/files";
import { fetchConversations } from "@/lib/backend";
import { fetchProjectOutputs, type BrainSnapshot } from "@/lib/backend/brain";
import { copyText } from "@/lib/files";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import { Board } from "./board";
import { BUTTON, EmptyState } from "./chrome";
import { matches, shortPath, tally, type Tally } from "./format";
import { SegmentBar } from "./progress";
import { ProjectChats } from "./project-chats";
import { dayAndClock, plainText, shortDay } from "./project-page-model";
import { ProjectOutputs } from "./project-outputs";
import { SharedBoardBanner } from "./shared-board";
import { LOOSE, PROJECT_STATUSES } from "./types";
import type { Handlers, ProjectRef } from "./types";

/** The project's pages. The board is the page; conversations and outputs are pages of their own
 *  beside it, rather than sections stacked underneath it — a kanban with a list under it is a
 *  kanban you scroll past to reach the list. */
type Section = "board" | "conversations" | "outputs";

/** The same ceiling the sidebar uses. Past that, search is the way in. */
const CHAT_LIMIT = 500;

const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  done: "Done",
  paused: "Paused",
  archived: "Archived",
};

/**
 * One project: its header, and three tabs under it.
 *
 * **The page is the window's height, not the content's.** The header is a fixed 144px on every
 * tab so the tabs never move when you switch between them, and what is under the tabs fills the
 * rest — each column, list and grid scrolls inside itself. A page that scrolled as a whole put
 * the board's columns, a conversation's summary and an output's preview at the mercy of whatever
 * was longest on the page, and the preview scrolled away from the grid it was previewing.
 *
 * The height is the window less the bar above (44), the wrapper's top padding (28) and the
 * 24px the design leaves under the board; `-mb-1` takes back the four pixels by which the
 * wrapper's 28px bottom padding exceeds that, so nothing scrolls by a hair.
 */
export function ProjectPage({
  snap,
  query,
  projectRef,
  refresh,
  remove,
  update,
  create,
  onBack,
  onOpenTask,
  onNewConversation,
}: {
  snap: BrainSnapshot;
  query: string;
  projectRef: ProjectRef;
  onBack: () => void;
  onOpenTask: (id: number) => void;
  /** Start a chat that is already inside this project. Absent where the panel is not mounted in
   *  a window that can open one. */
  onNewConversation?: (projectId: number) => void;
} & Handlers) {
  const [section, setSection] = useState<Section>("board");
  const [renaming, setRenaming] = useState(false);
  const project =
    projectRef === LOOSE ? null : (snap.projects ?? []).find((p) => p.id === projectRef);
  const projectId = project ? project.id : null;
  const own = snap.tasks.filter((t) =>
    projectRef === LOOSE ? t.project_id == null : t.project_id === projectRef,
  );
  const tasks = own.filter((t) => matches(query, t.goal, t.status, t.description));

  // Loaded here, not in the tabs, so their counts are on the tab labels before you open them.
  const chats = useQuery({
    queryKey: keys.conversations(CHAT_LIMIT, ""),
    queryFn: async () => (await fetchConversations(CHAT_LIMIT)).conversations,
    enabled: projectId != null,
    staleTime: 30_000,
  });
  const outputs = useQuery({
    queryKey: keys.projectOutputs(projectId ?? 0),
    queryFn: () => fetchProjectOutputs(projectId ?? 0),
    enabled: projectId != null,
  });
  const mine = (chats.data ?? []).filter((one) => one.projectId === projectId);

  if (projectRef !== LOOSE && !project) {
    return (
      <>
        <Crumbs onBack={onBack} />
        <div className="mt-6">
          <EmptyState icon={<FolderKanban className="size-5" />}>That project is gone.</EmptyState>
        </div>
      </>
    );
  }

  const counts = tally(own);
  const startChat = project && onNewConversation ? () => onNewConversation(project.id) : undefined;
  // One amber thing per screen. An archived project is not asking to be worked in, and on
  // Outputs the preview's "Open in canvas" is the primary — so the way into a chat steps back.
  const quiet = project?.status === "archived" || (section === "outputs" && (outputs.data?.length ?? 0) > 0);

  return (
    <div className="-mb-1 flex h-[calc(100dvh-96px)] min-h-[640px] flex-col">
      <header className="flex min-h-[144px] shrink-0 flex-col gap-2.5">
        <Crumbs onBack={onBack} current={project ? project.name : "No project"} />

        <div className="flex h-8 shrink-0 items-center gap-2.5">
          {project ? (
            <>
              <Title
                value={project.name}
                editing={renaming}
                onEditing={setRenaming}
                onSave={(name) => update("project", project.id, { name })}
              />
              <StatusMenu
                value={project.status}
                onChange={(status) => update("project", project.id, { status })}
              />
              <MoreMenu
                directory={project.directory}
                onRename={() => setRenaming(true)}
                // Wait for the answer before leaving. Calling both on the same tick opened the
                // "are you sure?" over the projects list, about a project you were no longer
                // looking at — and cancelling still cost you your place.
                onDelete={async () => {
                  if (await remove("project", project.id, project.name)) onBack();
                }}
              />
            </>
          ) : (
            <h1 className="m-0 truncate text-2xl font-[650] tracking-[-0.01em]">No project</h1>
          )}
          <span className="flex-1" />
          {startChat ? (
            <button type="button" onClick={startChat} className={cn(quiet ? BUTTON.secondary : BUTTON.primary, "gap-[7px] ps-3")}>
              <MessageSquarePlus className={cn("size-3.5", quiet && "text-muted-foreground")} strokeWidth={2.2} />
              New chat in this project
            </button>
          ) : null}
        </div>

        {project ? (
          <Description
            name={project.name}
            value={project.description}
            onSave={(description) => update("project", project.id, { description })}
          />
        ) : (
          <div className="flex h-10 shrink-0 items-end">
            <p className="text-muted-foreground m-0 line-clamp-2 max-w-[1040px] text-[13px] leading-5">
              Loose work — errands that aren&apos;t part of anything bigger. Give one a project from
              inside the task.
            </p>
          </div>
        )}

        <div className="flex h-[26px] shrink-0 items-center gap-3">
          {project ? (
            <>
              {/* Where every file he writes for this project lands — the one fact that, when it
                  was invisible, let a project run against a deleted folder for a whole session.
                  Cut from the left, because the end of a path is the part that identifies it. */}
              {project.directory ? (
                <span
                  title={project.directory}
                  className="border-border bg-card text-foreground/80 flex h-6 max-w-[420px] items-center gap-1.5 rounded-[7px] border ps-2 pe-[9px] font-mono text-[11px]"
                >
                  <Folder className="text-muted-foreground/80 size-[13px] shrink-0" />
                  <span className="truncate">{shortPath(project.directory, 48)}</span>
                </span>
              ) : (
                <span className="text-muted-foreground/80 text-xs italic">No folder — nothing here writes files</span>
              )}
              <span className="text-muted-foreground/80 text-xs" title={dayAndClock(project.updated_at)}>
                Updated {shortDay(project.updated_at).replace(/^(Today|Yesterday)$/, (word) => word.toLowerCase())}
              </span>
            </>
          ) : null}
          <span className="flex-1" />
          {counts.total ? <SegmentBar counts={counts} className="w-[280px]" /> : null}
          <Standing counts={counts} />
        </div>
      </header>

      {project ? (
        <Tabs
          section={section}
          onChange={setSection}
          counts={{
            board: counts.total,
            conversations: chats.data ? mine.length : undefined,
            outputs: outputs.data?.length,
          }}
        />
      ) : null}

      {section === "board" || !project ? (
        <>
          {/* Above the board, because it changes what the board is showing. Renders nothing
              when nothing has come in. */}
          {project ? <SharedBoardBanner projectId={project.id} onTaken={refresh} /> : null}
          <Board
            tasks={tasks}
            projectId={projectId}
            create={create}
            update={update}
            onOpenTask={onOpenTask}
          />
        </>
      ) : section === "conversations" ? (
        <ProjectChats
          items={mine.filter((one) => matches(query, one.title, one.lastSaid))}
          total={mine.length}
          loading={chats.isPending}
          project={project}
          onNew={startChat}
        />
      ) : (
        <ProjectOutputs
          items={(outputs.data ?? []).filter((one) => matches(query, one.title, one.content, one.task_goal))}
          loading={outputs.isPending}
          project={project}
          onOpenTask={onOpenTask}
        />
      )}
    </div>
  );
}

function Crumbs({ onBack, current }: { onBack: () => void; current?: string }) {
  return (
    <nav aria-label="Breadcrumb" className="flex h-4 shrink-0 items-center gap-1.5 text-[12.5px]">
      <button
        type="button"
        onClick={onBack}
        className="text-muted-foreground hover:text-foreground rounded-sm transition-colors"
      >
        Projects
      </button>
      {current ? (
        <>
          <ChevronRight className="text-muted-foreground/50 size-3 shrink-0" aria-hidden />
          <span aria-current="page" className="text-foreground/80 truncate">
            {current}
          </span>
        </>
      ) : null}
    </nav>
  );
}

/** "11 of 17 done · 5 working · 1 to approve · 2 up next" — in the order you would ask. */
function Standing({ counts }: { counts: Tally }) {
  if (!counts.total) return <span className="text-muted-foreground/80 text-xs">No tasks yet</span>;
  const parts: ReactNode[] = [];
  if (counts.working) parts.push(<span className="text-roam">{counts.working} working</span>);
  if (counts.planning) parts.push(<span className="text-kith">{counts.planning} to approve</span>);
  if (counts.approved) parts.push(<span>{counts.approved} up next</span>);
  return (
    <span className="text-muted-foreground flex gap-1.5 text-xs whitespace-nowrap tabular-nums">
      <span className="text-foreground font-medium">
        {counts.done} of {counts.total} done
      </span>
      {parts.map((part, at) => (
        <span key={at} className="flex gap-1.5">
          <span className="text-muted-foreground/50">·</span>
          {part}
        </span>
      ))}
    </span>
  );
}

const TABS: { key: Section; label: string }[] = [
  { key: "board", label: "Board" },
  { key: "conversations", label: "Conversations" },
  { key: "outputs", label: "Outputs" },
];

function Tabs({
  section,
  onChange,
  counts,
}: {
  section: Section;
  onChange: (section: Section) => void;
  counts: Record<Section, number | undefined>;
}) {
  // ← → between tabs, the tablist convention — focus follows, and so does the tab.
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const at = TABS.findIndex((one) => one.key === section);
    const next = TABS[(at + (event.key === "ArrowLeft" ? TABS.length - 1 : 1)) % TABS.length];
    onChange(next.key);
    event.currentTarget.querySelector<HTMLButtonElement>(`[data-tab="${next.key}"]`)?.focus();
  };
  return (
    <div
      role="tablist"
      aria-label="Project sections"
      onKeyDown={onKey}
      className="border-border mt-4 flex h-10 shrink-0 items-stretch gap-1 border-b"
    >
      {TABS.map((one, at) => {
        const active = one.key === section;
        const count = counts[one.key];
        return (
          <button
            key={one.key}
            type="button"
            role="tab"
            data-tab={one.key}
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(one.key)}
            className={cn(
              "-mb-px flex items-center gap-2 border-b-2 text-[13px] transition-colors",
              at === 0 ? "ps-0.5 pe-3.5" : "px-3.5",
              active
                ? "border-foreground text-foreground font-semibold"
                : "text-muted-foreground hover:text-foreground border-transparent",
            )}
          >
            {one.label}
            {count !== undefined ? (
              <span
                className={cn(
                  "flex h-[18px] items-center rounded-full px-1.5 text-[11px] tabular-nums",
                  active ? "bg-muted text-foreground/80 font-medium" : "bg-card text-muted-foreground/80",
                )}
              >
                {count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** The project's name, renamed where it stands: a double-click, or Rename in the "…" menu, turns
 *  it into a field the same size. Enter or leaving the field saves; Escape puts it back. No pencil
 *  beside it — the status sits right after the name, and a hidden button there would push it over. */
function Title({
  value,
  editing,
  onEditing,
  onSave,
}: {
  value: string;
  editing: boolean;
  onEditing: (editing: boolean) => void;
  onSave: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const field = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => {
    if (!editing) return;
    setDraft(value);
    field.current?.focus();
    field.current?.select();
    // Only on entering the edit: a snapshot arriving mid-edit must not replace what is typed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  const commit = () => {
    const next = draft.trim();
    onEditing(false);
    if (next && next !== value) onSave(next);
  };

  if (editing) {
    return (
      <input
        ref={field}
        value={draft}
        aria-label="Project name"
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit();
          if (event.key === "Escape") {
            event.preventDefault();
            onEditing(false);
          }
        }}
        className="border-input bg-card focus-visible:border-ring m-0 h-8 min-w-0 flex-1 rounded-lg border px-2 text-2xl font-[650] tracking-[-0.01em] outline-none"
      />
    );
  }
  return (
    <h1
      onDoubleClick={() => onEditing(true)}
      title="Double-click to rename"
      className="m-0 min-w-0 truncate text-2xl leading-8 font-[650] tracking-[-0.01em]"
    >
      {value}
    </h1>
  );
}

const MENU =
  "bg-popover/95 text-popover-foreground border-border/70 animate-in fade-in-0 zoom-in-95 z-50 min-w-44 overflow-hidden rounded-xl border p-1 shadow-xl backdrop-blur-xl duration-100";
const MENU_ITEM =
  "data-[highlighted]:bg-accent flex cursor-default items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] outline-none select-none";

/** The project's status, as the board draws it: a small bordered select beside the title. */
function StatusMenu({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const label = STATUS_LABEL[value] ?? value;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={`Project status: ${label}`}
          className="border-input bg-muted/60 text-foreground hover:bg-muted ms-1 flex h-7 shrink-0 items-center gap-1.5 rounded-lg border ps-2.5 pe-2 text-[12.5px] transition-colors"
        >
          {label}
          <ChevronDown className="text-muted-foreground/80 size-[13px]" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="start" sideOffset={6} className={MENU}>
          <DropdownMenu.RadioGroup value={value} onValueChange={onChange}>
            {PROJECT_STATUSES.map((one) => (
              <DropdownMenu.RadioItem key={one} value={one} className={MENU_ITEM}>
                <span className="flex size-4 items-center justify-center">
                  <DropdownMenu.ItemIndicator>
                    <Check className="text-kith size-3.5" />
                  </DropdownMenu.ItemIndicator>
                </span>
                {STATUS_LABEL[one] ?? one}
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/** The project's other actions. Delete lives here, behind its own "are you sure?" — a red bin
 *  beside the title was one stray click from the most destructive thing on the page. */
function MoreMenu({
  directory,
  onRename,
  onDelete,
}: {
  directory: string | null;
  onRename: () => void;
  onDelete: () => void;
}) {
  const [copied, setCopied] = useState<"" | "yes" | "no">("");
  /* Rename starts once the menu has closed, not when the item is chosen. The menu holds the focus
   * while it is open and hands it back to its trigger as it closes — so a field opened from
   * `onSelect` was focused, pulled back into the menu, then left with nothing, and Escape meant
   * for the field closed the whole panel. */
  const renaming = useRef(false);
  return (
    <DropdownMenu.Root onOpenChange={(open) => open && setCopied("")}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label="More project actions"
          className="text-muted-foreground hover:text-foreground hover:bg-muted/60 flex size-7 shrink-0 items-center justify-center rounded-lg transition-colors"
        >
          <Ellipsis className="size-4" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={6}
          className={MENU}
          onCloseAutoFocus={(event) => {
            if (!renaming.current) return;
            renaming.current = false;
            event.preventDefault();
            onRename();
          }}
        >
          <DropdownMenu.Item
            className={MENU_ITEM}
            onSelect={() => {
              renaming.current = true;
            }}
          >
            <Pencil className="text-muted-foreground size-3.5" />
            Rename
          </DropdownMenu.Item>
          {directory ? (
            <DropdownMenu.Item
              className={MENU_ITEM}
              // Kept open so it can say whether the copy really happened — see `copyText`.
              onSelect={(event) => {
                event.preventDefault();
                void copyText(directory).then((ok) => setCopied(ok ? "yes" : "no"));
              }}
            >
              <Copy className="text-muted-foreground size-3.5" />
              <span className="flex-1">{copied === "yes" ? "Copied" : copied === "no" ? "Couldn't copy" : "Copy folder path"}</span>
            </DropdownMenu.Item>
          ) : null}
          <DropdownMenu.Separator className="bg-border my-1 h-px" />
          <DropdownMenu.Item
            className={cn(MENU_ITEM, "text-destructive data-[highlighted]:bg-destructive/10")}
            onSelect={onDelete}
          >
            <Trash2 className="size-3.5" />
            Delete project…
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/**
 * Two lines of the project's description, and the rest on request.
 *
 * A long brief used to push the whole page down by a screen before you reached anything you could
 * act on. Clamped, it is plain text — markdown marks inside a two-line clamp read as noise — and
 * opened it is his Markdown again. An empty one keeps its two-line slot and says what goes in it,
 * so the tabs stay where they are on a project nobody has described.
 */
function Description({ name, value, onSave }: { name: string; value: string; onSave: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [clamped, setClamped] = useState(false);
  const text = useRef<HTMLParagraphElement>(null);
  const flat = plainText(value);

  // Show more only when two lines did not hold it — measured, because characters per line
  // depend on the window.
  useLayoutEffect(() => {
    const el = text.current;
    if (!el) return;
    const check = () => setClamped(el.scrollHeight > el.clientHeight + 1);
    check();
    const watch = new ResizeObserver(check);
    watch.observe(el);
    return () => watch.disconnect();
  }, [flat, open, draft]);

  const link =
    "text-foreground/80 hover:text-foreground h-5 shrink-0 text-xs whitespace-nowrap underline decoration-[color-mix(in_oklab,var(--foreground)_25%,transparent)] underline-offset-[3px] transition-colors";

  if (draft !== null) {
    const commit = () => {
      onSave(draft);
      setDraft(null);
    };
    return (
      <div className="flex max-w-[1040px] flex-col gap-1.5">
        <textarea
          autoFocus
          value={draft}
          aria-label="Project description"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              setDraft(null);
            }
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              commit();
            }
          }}
          placeholder={`What ${name} is, and what done means`}
          className="border-input bg-card focus-visible:border-ring min-h-24 w-full resize-y rounded-lg border px-3 py-2 font-mono text-[12.5px] leading-relaxed outline-none"
        />
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground/80 me-auto text-[11px]">⌘↵ saves · Esc cancels</span>
          <button type="button" onClick={() => setDraft(null)} className={cn(BUTTON.ghost, BUTTON.small)}>
            Cancel
          </button>
          <button type="button" onClick={commit} className={cn(BUTTON.primary, BUTTON.small)}>
            Save
          </button>
        </div>
      </div>
    );
  }

  if (!value.trim()) {
    return (
      <div className="flex h-10 shrink-0 items-end gap-3.5">
        <p className="text-muted-foreground/80 m-0 line-clamp-2 max-w-[1040px] text-[13px] leading-5 italic">
          No description yet. Say what {name} is and what done means — he keeps it in front of him in
          every chat in this project.
        </p>
        <button type="button" onClick={() => setDraft("")} className={link}>
          Add description
        </button>
      </div>
    );
  }

  if (open) {
    return (
      <div className="flex max-w-[1040px] flex-col items-start gap-1">
        <div className="text-muted-foreground text-[13px] leading-5">
          <Markdown>{value}</Markdown>
        </div>
        <span className="flex gap-3.5">
          <button type="button" onClick={() => setOpen(false)} className={link}>
            Show less
          </button>
          <button type="button" onClick={() => setDraft(value)} className={link}>
            Edit
          </button>
        </span>
      </div>
    );
  }

  return (
    <div className="group/description flex h-10 shrink-0 items-end gap-3.5">
      <p
        ref={text}
        onDoubleClick={() => setDraft(value)}
        className="text-muted-foreground m-0 line-clamp-2 max-w-[1040px] text-[13px] leading-5"
      >
        {flat}
      </p>
      {clamped ? (
        <button type="button" aria-expanded={false} onClick={() => setOpen(true)} className={link}>
          Show more
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => setDraft(value)}
        aria-label="Edit description"
        className="text-muted-foreground hover:text-foreground hover:bg-muted/60 mb-px flex size-[18px] shrink-0 items-center justify-center rounded opacity-0 transition group-hover/description:opacity-100 focus-visible:opacity-100"
      >
        <Pencil className="size-3" />
      </button>
    </div>
  );
}
