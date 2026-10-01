/**
 * Files: his folder on this machine — what is in it, and what each thing is.
 *
 * Drawn from the "Files" board of the 2026-09-30 control panel design: a toolbar (where you are,
 * a filter, the sort, new folder, list or grid), then three panes — the folder tree, the folder's
 * contents, and a preview of the selected file. The preview is the screen's answer to "what is
 * this file", which the old screen could only give by opening a full-window viewer per click.
 *
 * Every listing, in the tree and the list alike, is the same `keys.workspace(path)` query, so a
 * folder the tree has open and the list is showing is one request and one answer.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DropdownMenu } from "radix-ui";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Folder,
  FolderOpen,
  FolderPlus,
  LayoutGrid,
  List,
  Maximize2,
  PanelRightOpen,
  Pencil,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { FileViewer, skipTextRead } from "@/components/files";
import { classify } from "@/components/files/kinds";
import { useConfirm } from "@/components/ui/confirm";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { fetchPermissions } from "@/lib/backend";
import { fetchWorkspace, fetchWorkspaceFile } from "@/lib/backend/brain";
import type { WorkspaceEntry } from "@/lib/backend/brain";
import {
  copyText,
  formatSize,
  makeFolder,
  openWorkspaceFile,
  rawFileUrl,
  remove as removeFile,
  rename as renameEntry,
} from "@/lib/files";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import { BUTTON, Kbd, PageTitle } from "./chrome";
import { EntryIcon, Preview } from "./files-preview";
import { FolderTree } from "./files-tree";
import { joinPath, modifiedLabel, parentOf, sortEntries, visibleEntries, type SortBy } from "./library";

/* ── Small preferences, kept per viewer ─────────────────────────────────── */

/** A choice that should still be there next time — the sort, the view, hidden files. Browser
 *  storage, and allowed to fail: a private window simply starts from the default. */
function useStored<T>(key: string, initial: T, valid: (value: unknown) => value is T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      const parsed: unknown = raw ? JSON.parse(raw) : undefined;
      return valid(parsed) ? parsed : initial;
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (next: T) => {
      setValue(next);
      try {
        localStorage.setItem(key, JSON.stringify(next));
      } catch {
        /* the choice holds for this visit, which is all a failed write can cost */
      }
    },
    [key],
  );
  return [value, set] as const;
}

const SORTS: { value: SortBy; label: string; order: string }[] = [
  { value: "modified", label: "Last changed", order: "newest first" },
  { value: "name", label: "Name", order: "A to Z" },
  { value: "size", label: "Size", order: "largest first" },
];
const isSort = (v: unknown): v is SortBy => SORTS.some((s) => s.value === v);
const isView = (v: unknown): v is "list" | "grid" => v === "list" || v === "grid";
const isBool = (v: unknown): v is boolean => typeof v === "boolean";

/* ── The screen ─────────────────────────────────────────────────────────── */

export function WorkspaceFiles() {
  const [path, setPath] = useState(".");
  const cache = useQueryClient();
  const confirm = useConfirm();
  // Selection is by name, not by row number: the listing is live, and a file he writes while you
  // are looking would otherwise move the selection to whatever slid into its row.
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [sortBy, setSortBy] = useStored<SortBy>("kith.control-panel.files.sort", "modified", isSort);
  const [view, setView] = useStored<"list" | "grid">("kith.control-panel.files.view", "list", isView);
  const [showHidden, setShowHidden] = useStored<boolean>("kith.control-panel.files.hidden", false, isBool);
  const [previewOpen, setPreviewOpen] = useStored<boolean>("kith.control-panel.files.preview", true, isBool);
  const [filter, setFilter] = useState("");
  // The row a right-click is on. It gets a ring rather than the selection, the way Finder does
  // it, so the preview does not jump to a file you only wanted to copy the path of.
  const [menuFor, setMenuFor] = useState<string | null>(null);
  // An inline editor rather than a dialog: renaming is a small correction, and a modal for it
  // loses the context of the folder you're renaming inside.
  const [editing, setEditing] = useState<{ name: string; draft: string } | null>(null);
  const [creating, setCreating] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  // A failed *write* — a rename refused, a delete that could not — reports separately from a
  // failed read, which the query owns.
  const [refused, setRefused] = useState("");
  // The open file lives in the full-window viewer, so the folder listing stays put.
  const [file, setFile] = useState<{ path: string; content: string | null; error?: string } | null>(null);
  const list = useRef<HTMLDivElement>(null);

  /* One query per folder, and it pauses while you are typing into the listing.
   *
   * He writes files while you are looking at the folder they land in, so this has to follow —
   * `workspace` events invalidate it through `STALE_ON`. But an inline rename is anchored to the
   * row it is on, and a new-folder input sits *in* the list: replacing the listing under either is
   * how a typed name gets thrown away. So `enabled` is off while one is open — in the tree too,
   * which observes the same keys and would otherwise refetch them under the list. A paused query
   * still takes the invalidation; it is marked stale and refetches the moment the rename is done. */
  const holding = Boolean(editing || creating !== null || busy);
  const listing = useQuery({
    queryKey: keys.workspace(path),
    queryFn: async (): Promise<WorkspaceEntry[]> => (await fetchWorkspace(path)).entries,
    enabled: !holding,
  });
  const entries = useMemo(() => listing.data ?? [], [listing.data]);
  const failed = listing.error;
  const error = refused || (failed ? (failed instanceof Error ? failed.message : "failed") : "");

  // Where his folder is on this machine, for the paths the preview prints and copies.
  const root = useQuery({ queryKey: keys.permissions(), queryFn: fetchPermissions }).data?.workspace.root ?? "";

  const visible = useMemo(
    () => visibleEntries(sortEntries(entries, sortBy), { showHidden, filter }),
    [entries, sortBy, showHidden, filter],
  );
  const current = visible.find((e) => e.name === selectedName) ?? visible[0] ?? null;
  const at = current ? visible.indexOf(current) : -1;

  const join = (name: string) => joinPath(path, name);
  const hostPath = (relative: string) => (root ? `${root}/${relative}` : relative);
  const crumbs = path === "." ? [] : path.split("/");
  const here = crumbs[crumbs.length - 1] ?? "his folder";

  /** Go to a folder — with `focus` selected in it, the folder you came up out of — or re-read
   *  the one you are in. */
  const go = useCallback(
    (to: string, focus: string | null = null) => {
      if (to === path) {
        void cache.invalidateQueries({ queryKey: keys.workspace(to) });
        return;
      }
      setPath(to);
      setSelectedName(focus);
      setFilter("");
      setEditing(null);
      setCreating(null);
    },
    [cache, path],
  );
  const up = () => {
    if (path !== ".") go(parentOf(path), crumbs[crumbs.length - 1] ?? null);
  };

  // ⌘R is the panel's refresh, and the panel's handler only knows about the board. A shell
  // command he runs does not announce what it wrote, so this is the way to see it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "r") {
        void cache.invalidateQueries({ queryKey: ["workspace"] });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cache]);

  // The selected row stays in view while the keys move it. By name: a live refetch is a new
  // object for the same file, and following that would scroll you back whenever he saved anything.
  const currentName = current?.name;
  useEffect(() => {
    if (currentName == null) return;
    list.current?.querySelector(`[data-name="${CSS.escape(currentName)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [currentName]);

  const openFile = (p: string) => {
    setFile({ path: p, content: null });
    // The viewer shows a picture or a PDF from its own bytes; reading it as text here would only
    // produce a decode error for it to display.
    if (skipTextRead(p)) return;
    const stale = (cur: typeof file) => cur?.path !== p;
    fetchWorkspaceFile(p)
      .then((f) => setFile((cur) => (stale(cur) ? cur : { path: p, content: f.content })))
      .catch((e) =>
        setFile((cur) =>
          stale(cur)
            ? cur
            : { path: p, content: null, error: e instanceof Error ? e.message : "couldn't read that file" },
        ),
      );
  };
  const openEntry = (entry: WorkspaceEntry) =>
    entry.type === "dir" ? go(join(entry.name)) : openFile(join(entry.name));

  /** Run one change, then re-read the folder so what's on screen is what's there. */
  const apply = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setRefused("");
    try {
      await action();
      void cache.invalidateQueries({ queryKey: keys.workspace(path) });
    } catch (e) {
      setRefused(e instanceof Error ? e.message : "that didn't work");
    } finally {
      setBusy("");
    }
  };
  /** A hand-off to this machine (open, reveal), whose failure is said where the list is. */
  const handOff = (relative: string, reveal: boolean) =>
    openWorkspaceFile(relative, reveal).catch((e: unknown) =>
      setRefused(e instanceof Error ? e.message : "couldn't hand that over"),
    );

  const submitRename = () => {
    if (!editing) return;
    const to = editing.draft.trim();
    const from = editing.name;
    setEditing(null);
    if (!to || to === from || to.includes("/")) return;
    setSelectedName(to);
    void apply("rename", () => renameEntry(join(from), join(to)));
  };

  const submitFolder = () => {
    const name = (creating || "").trim();
    setCreating(null);
    if (!name || name.includes("/")) return;
    setSelectedName(name);
    void apply("folder", () => makeFolder(join(name)));
  };

  const removeEntry = async (entry: WorkspaceEntry) => {
    const ok = await confirm({
      title: entry.type === "dir" ? "Delete this folder?" : "Delete this file?",
      subject: entry.name,
      description:
        entry.type === "dir"
          ? "Everything inside goes with it, and there's no undo on his machine."
          : "There's no undo — it's gone from his machine.",
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    const next = visible[at + 1] ?? visible[at - 1];
    setSelectedName(next?.name ?? null);
    void apply("delete", () => removeFile(join(entry.name)));
  };

  /* Download the file, not a description of it.
   *
   * This saved `file.content` — the text the viewer had decoded — into a `text/plain` blob, so the
   * only files it could save were the ones that survive being decoded and re-encoded as UTF-8, and
   * anything else had no download at all. `/api/workspace/raw` serves the bytes with their own
   * type (it is what the picture and PDF views read), so this saves exactly that — the same fix
   * `task-detail.tsx` made for deliverables. The global `fetch` wrapper carries the API token. */
  const download = async (relative: string) => {
    const response = await fetch(rawFileUrl(relative)).catch(() => null);
    if (!response?.ok) {
      setRefused(`Couldn't read ${relative} — it may have been moved or renamed.`);
      return;
    }
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = relative.split("/").pop() || "file";
    a.click();
    URL.revokeObjectURL(url);
  };

  const copyPath = (relative: string) => copyText(hostPath(relative));

  // Keyboard, because a file list you can only mouse through isn't one. Bound to the listing
  // rather than the window so it can't fight the chat composer.
  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (editing || creating !== null) return;
    const move = (to: number) => {
      event.preventDefault();
      const next = visible[Math.max(0, Math.min(visible.length - 1, to))];
      if (next) setSelectedName(next.name);
    };
    if (event.key === "ArrowDown") return move(at + 1);
    if (event.key === "ArrowUp") return move(at - 1);
    if (event.key === "Home") return move(0);
    if (event.key === "End") return move(visible.length - 1);
    if (event.key === "Enter" && current) {
      event.preventDefault();
      return openEntry(current);
    }
    if (event.key === "ArrowLeft" && path !== ".") {
      event.preventDefault();
      return up();
    }
    if (event.key === "ArrowRight" && current?.type === "dir") {
      event.preventDefault();
      return go(join(current.name));
    }
    // The menu's shortcuts. By `code`, because Option turns the R of ⌥⌘R into "®".
    if (current && event.metaKey) {
      const target = join(current.name);
      if (event.code === "KeyO" && !event.altKey && current.type === "file") {
        event.preventDefault();
        return void handOff(target, false);
      }
      if (event.code === "KeyR" && event.altKey) {
        event.preventDefault();
        return void handOff(target, true);
      }
      if (event.code === "KeyC" && event.altKey) {
        event.preventDefault();
        return void copyPath(target);
      }
    }
    if ((event.key === "Backspace" || event.key === "Delete") && current) {
      // ⌫ deletes, as the design's hint row says; ← is "up a folder". It used to be the other way
      // round at any depth below the top, which left the hint and the key disagreeing. The confirm
      // is what makes a stray press cost nothing.
      event.preventDefault();
      void removeEntry(current);
    }
  };

  const fileCount = visible.filter((e) => e.type === "file").length;
  const dirCount = visible.length - fileCount;
  const bytes = visible.reduce((sum, e) => sum + (e.type === "file" ? e.size : 0), 0);
  const tally = [
    dirCount ? `${dirCount} folder${dirCount === 1 ? "" : "s"}` : "",
    `${fileCount} file${fileCount === 1 ? "" : "s"}`,
    fileCount ? formatSize(bytes) : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const menu = (entry: WorkspaceEntry) => {
    const full = join(entry.name);
    const isDir = entry.type === "dir";
    const item = "h-8 gap-2.5 rounded-[7px] px-2.5 py-0 text-[13px] data-[highlighted]:bg-muted data-[highlighted]:text-foreground";
    return (
      <ContextMenuContent className="border-input w-[236px] min-w-0 rounded-[10px] bg-[color-mix(in_oklab,var(--muted)_62%,var(--card))] p-1 shadow-[0_14px_36px_rgba(0,0,0,0.5)] backdrop-blur-none">
        <ContextMenuLabel className="text-muted-foreground/80 flex h-[26px] items-center px-2.5 py-0 font-mono text-[11px]">
          {entry.name}
        </ContextMenuLabel>
        <ContextMenuItem className={item} icon={<Maximize2 className="text-muted-foreground size-3.5" />} hint="↵" onSelect={() => openEntry(entry)}>
          Open
        </ContextMenuItem>
        {!isDir ? (
          <ContextMenuItem
            className={item}
            icon={<ExternalLink className="text-muted-foreground size-3.5" />}
            hint="⌘O"
            onSelect={() => void handOff(full, false)}
          >
            Open in another app
          </ContextMenuItem>
        ) : null}
        <ContextMenuItem
          className={item}
          icon={<FolderOpen className="text-muted-foreground size-3.5" />}
          hint="⌥⌘R"
          onSelect={() => void handOff(full, true)}
        >
          Show in Finder
        </ContextMenuItem>
        <ContextMenuSeparator className="bg-border" />
        <ContextMenuItem className={item} icon={<Copy className="text-muted-foreground size-3.5" />} hint="⌥⌘C" onSelect={() => void copyPath(full)}>
          Copy path
        </ContextMenuItem>
        <ContextMenuItem
          className={item}
          icon={<Pencil className="text-muted-foreground size-3.5" />}
          onSelect={() => setEditing({ name: entry.name, draft: entry.name })}
        >
          Rename
        </ContextMenuItem>
        <ContextMenuSeparator className="bg-border" />
        <ContextMenuItem
          className={cn(item, "text-destructive data-[highlighted]:bg-destructive/10 data-[highlighted]:text-destructive")}
          icon={<Trash2 className="size-3.5" />}
          hint="⌫"
          danger
          onSelect={() => void removeEntry(entry)}
        >
          Delete
        </ContextMenuItem>
      </ContextMenuContent>
    );
  };

  /** The name, or the field that renames it. */
  const nameOf = (entry: WorkspaceEntry, className: string) =>
    editing?.name === entry.name ? (
      // Renaming happens where the name is, so you can still see the folder you're renaming inside.
      <input
        autoFocus
        value={editing.draft}
        onChange={(ev) => setEditing({ name: entry.name, draft: ev.target.value })}
        onClick={(ev) => ev.stopPropagation()}
        onBlur={submitRename}
        onKeyDown={(ev) => {
          if (ev.key === "Enter") submitRename();
          if (ev.key === "Escape") {
            ev.preventDefault();
            setEditing(null);
          }
          ev.stopPropagation();
        }}
        className="border-ring bg-background min-w-0 flex-1 rounded-md border px-1.5 py-0.5 text-[13px] outline-none"
        aria-label={`Rename ${entry.name}`}
      />
    ) : (
      <span className={className}>{entry.name}</span>
    );

  const rowProps = (entry: WorkspaceEntry, i: number) => ({
    id: `file-row-${i}`,
    "data-name": entry.name,
    role: "option" as const,
    "aria-selected": current?.name === entry.name,
    onClick: () => {
      setSelectedName(entry.name);
      list.current?.focus();
    },
    onDoubleClick: () => openEntry(entry),
  });

  return (
    <div className="mb-[-4px] flex h-[calc(100vh-96px)] min-h-[620px] flex-col gap-5">
      <PageTitle title="Files" subtitle="His folder on this machine. Everything he makes lands here." />

      {/* Toolbar */}
      <div className="flex h-8 shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={up}
          disabled={path === "."}
          aria-label="Up a folder"
          title="Up a folder (←)"
          className={cn(BUTTON.secondary, "text-foreground/80 w-8 px-0 [&_svg]:size-[15px]")}
        >
          <ArrowUp />
        </button>
        <nav aria-label="Folder path" className="flex min-w-0 items-center gap-0.5 font-mono text-[13px]">
          <button
            type="button"
            onClick={() => go(".")}
            aria-current={path === "." ? "page" : undefined}
            className={cn(
              "hover:bg-muted/50 flex h-7 shrink-0 items-center gap-1.5 rounded-md px-1.5 transition-colors",
              path === "." ? "text-foreground font-semibold" : "text-muted-foreground",
            )}
          >
            <Folder className="size-3.5" />~
          </button>
          {crumbs.map((seg, i) => {
            const last = i === crumbs.length - 1;
            return (
              <span key={i} className="flex min-w-0 items-center gap-0.5">
                <ChevronRight aria-hidden className="text-muted-foreground/50 size-[13px] shrink-0" />
                <button
                  type="button"
                  onClick={() => go(crumbs.slice(0, i + 1).join("/"))}
                  aria-current={last ? "page" : undefined}
                  className={cn(
                    "hover:bg-muted/50 flex h-7 min-w-0 items-center rounded-md px-1.5 whitespace-nowrap transition-colors",
                    last ? "text-foreground font-semibold" : "text-muted-foreground",
                  )}
                >
                  <span className="truncate">{seg}</span>
                </button>
              </span>
            );
          })}
        </nav>
        <span className="flex-1" />
        <label className="border-input bg-card text-muted-foreground/80 focus-within:border-ring flex h-8 w-[232px] shrink-0 items-center gap-2 rounded-lg border px-2.5 transition-colors">
          <FilterIcon />
          <span className="sr-only">Filter this folder</span>
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape" && filter) {
                event.preventDefault();
                setFilter("");
              }
            }}
            placeholder={`Filter in ${here}`}
            className="text-foreground placeholder:text-muted-foreground/80 h-7 min-w-0 flex-1 bg-transparent text-[13px] outline-none"
          />
        </label>
        <SortMenu value={sortBy} onChange={setSortBy} />
        <button type="button" onClick={() => setCreating("")} className={cn(BUTTON.secondary, "gap-[7px]")}>
          <FolderPlus className="text-muted-foreground size-3.5" />
          New folder
        </button>
        {/* Kept although the design has no refresh: his file tools announce what they write, but
            a shell command he runs does not, and neither does anything you do in Finder — so
            without this the only way to see those would be leaving the screen. ⌘R does the same. */}
        <button
          type="button"
          onClick={() => void cache.invalidateQueries({ queryKey: ["workspace"] })}
          aria-label="Re-read this folder"
          title="Re-read this folder (⌘R)"
          className={cn(BUTTON.secondary, "text-foreground/80 w-8 px-0")}
        >
          <RefreshCw className={cn("size-3.5", (listing.isFetching || busy) && "animate-spin")} />
        </button>
        <div role="group" aria-label="View" className="bg-sidebar border-border flex h-8 shrink-0 gap-0.5 rounded-lg border p-0.5">
          {(
            [
              ["list", "List view", <List key="l" />],
              ["grid", "Grid view", <LayoutGrid key="g" />],
            ] as const
          ).map(([value, label, icon]) => (
            <button
              key={value}
              type="button"
              aria-label={label}
              aria-pressed={view === value}
              onClick={() => setView(value)}
              className={cn(
                "flex h-[26px] w-7 items-center justify-center rounded-md transition-colors [&_svg]:size-3.5",
                view === value ? "bg-muted text-foreground" : "text-muted-foreground/80 hover:text-foreground",
              )}
            >
              {icon}
            </button>
          ))}
        </div>
      </div>

      {/* Three panes: tree · list · preview */}
      <div
        className={cn(
          "bg-card border-border grid min-h-0 flex-1 overflow-hidden rounded-[14px] border",
          previewOpen ? "grid-cols-[240px_minmax(0,1fr)_480px]" : "grid-cols-[240px_minmax(0,1fr)]",
        )}
      >
        <FolderTree
          path={path}
          root={root}
          showHidden={showHidden}
          onShowHidden={setShowHidden}
          holding={holding}
          onGo={(to) => go(to)}
        />

        <section aria-label={`Files in ${here}`} className="relative flex min-h-0 min-w-0 flex-col">
          {view === "list" ? (
            <div className="border-border/80 text-muted-foreground/80 flex h-9 shrink-0 items-center gap-3 border-b px-4 text-[10.5px] font-semibold tracking-[0.1em] uppercase">
              <span className="w-7 shrink-0" />
              <ColumnHead sortBy={sortBy} value="name" onSort={setSortBy} className="flex-1">
                Name
              </ColumnHead>
              <span className="w-[92px] shrink-0">Kind</span>
              <ColumnHead sortBy={sortBy} value="size" onSort={setSortBy} className="w-16 justify-end">
                Size
              </ColumnHead>
              <ColumnHead sortBy={sortBy} value="modified" onSort={setSortBy} className="w-32 justify-end">
                Modified
              </ColumnHead>
              {!previewOpen ? <ShowPreview onClick={() => setPreviewOpen(true)} /> : null}
            </div>
          ) : !previewOpen ? (
            <div className="border-border/80 flex h-9 shrink-0 items-center justify-end border-b px-4">
              <ShowPreview onClick={() => setPreviewOpen(true)} />
            </div>
          ) : null}

          {error ? <p role="alert" className="text-destructive px-4 pt-3 text-[12.5px]">{error}</p> : null}

          {creating !== null ? (
            <div className="mx-2 mt-1.5 flex h-11 shrink-0 items-center gap-3 rounded-[10px] px-2">
              <span className="bg-muted/60 text-muted-foreground flex size-7 shrink-0 items-center justify-center rounded-lg">
                <FolderPlus className="size-3.5" />
              </span>
              <input
                autoFocus
                value={creating}
                placeholder="Folder name…"
                onChange={(e) => setCreating(e.target.value)}
                onBlur={submitFolder}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitFolder();
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setCreating(null);
                  }
                }}
                className="border-ring bg-background placeholder:text-muted-foreground/80 min-w-0 flex-1 rounded-md border px-2 py-1 text-[13px] outline-none"
                aria-label="New folder name"
              />
            </div>
          ) : null}

          {visible.length === 0 ? (
            <div className="flex min-h-0 flex-1 items-center justify-center px-6">
              <p className="text-muted-foreground/80 text-[13px]">
                {listing.isPending
                  ? "Looking…"
                  : filter
                    ? `Nothing in ${here} matches “${filter}”.`
                    : entries.length
                      ? "Only hidden files here — Show hidden to see them."
                      : "This folder is empty."}
              </p>
            </div>
          ) : (
            <div
              ref={list}
              role="listbox"
              tabIndex={0}
              aria-label={`Files in ${here}`}
              aria-activedescendant={at >= 0 ? `file-row-${at}` : undefined}
              onKeyDown={onListKeyDown}
              className={cn(
                "focus-visible:ring-ring/40 min-h-0 flex-1 overflow-y-auto outline-none focus-visible:ring-2 focus-visible:ring-inset",
                view === "list"
                  ? "flex flex-col gap-0.5 px-2 py-1.5"
                  : "grid content-start gap-1.5 p-3 [grid-template-columns:repeat(auto-fill,minmax(124px,1fr))]",
              )}
            >
              {visible.map((entry, i) => {
                const isDir = entry.type === "dir";
                const on = current?.name === entry.name;
                const ringed = menuFor === entry.name && !on;
                const kind = isDir ? "Folder" : classify(entry.name).label;
                return (
                  <ContextMenu key={entry.name} onOpenChange={(open) => setMenuFor(open ? entry.name : null)}>
                    <ContextMenuTrigger asChild>
                      {view === "list" ? (
                        <div
                          {...rowProps(entry, i)}
                          className={cn(
                            "text-foreground flex h-11 shrink-0 cursor-default items-center gap-3 rounded-[10px] px-2 transition-colors",
                            on ? "bg-muted" : "hover:bg-muted/50",
                            ringed && "shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--foreground)_22%,transparent)]",
                          )}
                        >
                          <EntryTile entry={entry} />
                          {nameOf(entry, "min-w-0 flex-1 truncate text-[13px]")}
                          <span className="text-muted-foreground w-[92px] shrink-0 truncate text-[12.5px]">{kind}</span>
                          <span className="text-muted-foreground w-16 shrink-0 text-right text-xs tabular-nums">
                            {isDir ? "—" : formatSize(entry.size)}
                          </span>
                          <span className="text-muted-foreground/80 w-32 shrink-0 text-right text-xs whitespace-nowrap tabular-nums">
                            {modifiedLabel(entry.modified ?? 0)}
                          </span>
                        </div>
                      ) : (
                        <div
                          {...rowProps(entry, i)}
                          className={cn(
                            "flex cursor-default flex-col items-center gap-2 rounded-[10px] px-2 pt-3.5 pb-2.5 text-center transition-colors",
                            on ? "bg-muted" : "hover:bg-muted/50",
                            ringed && "shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--foreground)_22%,transparent)]",
                          )}
                        >
                          <EntryTile entry={entry} large />
                          {nameOf(entry, "text-foreground line-clamp-2 w-full text-[12.5px] leading-[1.35] break-all")}
                          <span className="text-muted-foreground/80 text-[11px] tabular-nums">
                            {isDir ? "Folder" : formatSize(entry.size)}
                          </span>
                        </div>
                      )}
                    </ContextMenuTrigger>
                    {menu(entry)}
                  </ContextMenu>
                );
              })}
            </div>
          )}

          <div className="border-border/80 text-muted-foreground/80 flex h-10 shrink-0 items-center gap-3 border-t px-4 text-xs">
            <span className="tabular-nums">{tally}</span>
            <span className="flex-1" />
            <span className="flex items-center gap-1.5 text-[11.5px] whitespace-nowrap">
              <Kbd>↑↓</Kbd>move
              <span className="text-muted-foreground/50">·</span>
              <Kbd>↵</Kbd>open
              <span className="text-muted-foreground/50">·</span>
              <Kbd>←</Kbd>up a folder
              <span className="text-muted-foreground/50">·</span>
              <Kbd>⌫</Kbd>delete
            </span>
          </div>
        </section>

        {previewOpen ? (
          <Preview
            entry={current}
            path={current ? join(current.name) : ""}
            hostPath={current ? hostPath(join(current.name)) : ""}
            onOpen={() => current && openEntry(current)}
            onReveal={() => current && void handOff(join(current.name), true)}
            onCopy={() => (current ? copyPath(join(current.name)) : Promise.resolve(false))}
            onHide={() => setPreviewOpen(false)}
          />
        ) : null}
      </div>

      {file ? (
        <FileViewer
          open
          onOpenChange={(o) => !o && setFile(null)}
          name={file.path}
          content={file.content}
          error={file.error}
          onDownload={() => void download(file.path)}
          onOpenOnHost={(reveal) => openWorkspaceFile(file.path, reveal)}
        />
      ) : null}
    </div>
  );
}

/* ── Pieces of the list ─────────────────────────────────────────────────── */

/** The board's funnel, which lucide draws with a different stroke. */
function FilterIcon() {
  return (
    <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="shrink-0">
      <path d="M22 3H2l8 9.46V19l4 2v-8.54z" />
    </svg>
  );
}


function EntryTile({ entry, large = false }: { entry: WorkspaceEntry; large?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "bg-muted/60 text-muted-foreground flex shrink-0 items-center justify-center",
        large ? "size-10 rounded-[10px] [&_svg]:size-[18px]" : "size-7 rounded-lg [&_svg]:size-3.5",
      )}
    >
      <EntryIcon entry={entry} />
    </span>
  );
}

function ColumnHead({
  sortBy,
  value,
  onSort,
  className,
  children,
}: {
  sortBy: SortBy;
  value: SortBy;
  onSort: (value: SortBy) => void;
  className?: string;
  children: ReactNode;
}) {
  const on = sortBy === value;
  const order = SORTS.find((s) => s.value === value)?.order ?? "";
  return (
    <button
      type="button"
      onClick={() => onSort(value)}
      aria-label={`Sort by ${String(children).toLowerCase()}, ${order}`}
      className={cn(
        "hover:text-foreground flex shrink-0 items-center gap-1 uppercase transition-colors",
        on && "text-foreground/80",
        className,
      )}
    >
      {children}
      {on ? <ArrowDown aria-hidden className="size-[11px]" strokeWidth={2.4} /> : null}
    </button>
  );
}

function ShowPreview({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Show preview"
      title="Show preview"
      className="text-muted-foreground/80 hover:bg-muted/50 hover:text-foreground flex size-7 shrink-0 items-center justify-center rounded-[7px] transition-colors"
    >
      <PanelRightOpen className="size-[15px]" />
    </button>
  );
}

/** "Sort: Last changed ▾" — Radix's menu, so arrows, type-ahead and Escape come with it. */
function SortMenu({ value, onChange }: { value: SortBy; onChange: (value: SortBy) => void }) {
  const current = SORTS.find((s) => s.value === value) ?? SORTS[0];
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" aria-label={`Sort by: ${current.label}`} className={cn(BUTTON.secondary, "gap-[7px] px-2.5")}>
          <ArrowUpDown className="text-muted-foreground size-3.5" />
          {current.label}
          <ChevronDown className="text-muted-foreground/80 size-[13px]" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="border-input animate-in fade-in-0 zoom-in-95 z-50 min-w-[188px] rounded-[10px] border bg-[color-mix(in_oklab,var(--muted)_62%,var(--card))] p-1 shadow-[0_14px_36px_rgba(0,0,0,0.5)] duration-100"
        >
          <DropdownMenu.RadioGroup value={value} onValueChange={(v) => isSort(v) && onChange(v)}>
            {SORTS.map((sort) => (
              <DropdownMenu.RadioItem
                key={sort.value}
                value={sort.value}
                className="data-[highlighted]:bg-muted flex h-8 cursor-default items-center gap-2.5 rounded-[7px] px-2.5 text-[13px] outline-none select-none"
              >
                <span className="flex size-3.5 items-center justify-center">
                  <DropdownMenu.ItemIndicator>
                    <Check className="text-kith size-3.5" />
                  </DropdownMenu.ItemIndicator>
                </span>
                <span className="flex-1">{sort.label}</span>
                <span className="text-muted-foreground/80 text-[11px]">{sort.order}</span>
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
