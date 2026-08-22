import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp,
  ChevronRight,
  Copy,
  Expand,
  ExternalLink,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  FolderTree,
  Pencil,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { FileViewer, skipTextRead } from "@/components/files";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { Dropdown } from "@/components/ui/dropdown";
import { fetchWorkspace, fetchWorkspaceFile } from "@/lib/backend/brain";
import type { WorkspaceEntry } from "@/lib/backend/brain";
import {
  copyText,
  formatModified,
  formatSize,
  makeFolder,
  openWorkspaceFile,
  remove as removeFile,
  rename as renameEntry,
} from "@/lib/files";
import { cn } from "@/lib/utils";
import { keys } from "@/lib/query-keys";
import { EmptyState, PageHeader } from "./chrome";
import { FIELD } from "./types";

/* ── Workspace (his file sandbox) ───────────────────────────────────────── */

export function WorkspaceFiles() {
  const [path, setPath] = useState(".");
  const cache = useQueryClient();
  // The open file lives in a preview dialog, so the folder listing stays put.
  const [file, setFile] = useState<{ path: string; content: string | null; error?: string } | null>(
    null,
  );
  const [sortBy, setSortBy] = useState<"name" | "size" | "modified">("name");
  // Which row the keyboard is on. Folders first is the browsing order, so arrow keys
  // follow what's on screen rather than the order the container listed things in.
  const [cursor, setCursor] = useState(0);
  // An inline editor rather than a dialog: renaming is a small correction, and a modal
  // for it loses the context of the folder you're renaming inside.
  const [editing, setEditing] = useState<{ name: string; draft: string } | null>(null);
  const [creating, setCreating] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const confirm = useConfirm();

  /* One query per folder, and it pauses while you are typing into the listing.
   *
   * He writes files while you are looking at the folder they land in, so this has to follow —
   * `workspace` events invalidate it through `STALE_ON`. But an inline rename is anchored to the
   * row it is on, and a new-folder input sits *in* the list: replacing the listing under either is
   * how a typed name gets thrown away. That was a hand-rolled guard around an interval; here it is
   * `enabled`, which is the same rule expressed where the fetching happens. A paused query still
   * takes the invalidation — it is marked stale and refetches the moment the rename is done, so
   * nothing is missed, only deferred.
   *
   * Navigation is `setPath`. The listing follows the key rather than being fetched by hand, which
   * also means going back to a folder you were just in is instant. */
  // A failed *write* — a rename refused, a delete that could not — reports separately from a
  // failed read, which the query owns.
  const [refused, setRefused] = useState("");
  const holding = Boolean(editing || creating !== null || busy);
  const {
    data: entries = [],
    error: failed,
    isFetching: loading,
  } = useQuery({
    queryKey: keys.workspace(path),
    queryFn: async (): Promise<WorkspaceEntry[]> => (await fetchWorkspace(path)).entries,
    enabled: !holding,
  });
  const error = refused || (failed ? (failed instanceof Error ? failed.message : "failed") : "");

  /** Go to a folder, or re-read the one you are in. */
  const load = useCallback(
    (p: string) => {
      if (p === path) void cache.invalidateQueries({ queryKey: keys.workspace(p) });
      else setPath(p);
    },
    [cache, path],
  );

  const join = (name: string) => (path === "." ? name : `${path}/${name}`);
  const crumbs = path === "." ? [] : path.split("/");
  const goTo = (i: number) => load(crumbs.slice(0, i + 1).join("/") || ".");
  const openFile = (name: string) => {
    const p = join(name);
    setFile({ path: p, content: null });
    // The viewer shows a picture or a PDF from its own bytes; reading it as text here
    // would only produce a decode error for it to display.
    if (skipTextRead(p)) return;
    const stale = (cur: typeof file) => cur?.path !== p;
    fetchWorkspaceFile(p)
      .then((f) => setFile((cur) => (stale(cur) ? cur : { path: p, content: f.content })))
      .catch((e) =>
        setFile((cur) =>
          stale(cur)
            ? cur
            : {
                path: p,
                content: null,
                error: e instanceof Error ? e.message : "couldn't read that file",
              },
        ),
      );
  };
  /** Run one change, then re-read the folder so what's on screen is what's there. */
  const apply = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setRefused("");
    try {
      await action();
      load(path);
    } catch (e) {
      setRefused(e instanceof Error ? e.message : "that didn't work");
    } finally {
      setBusy("");
    }
  };

  const submitRename = () => {
    if (!editing) return;
    const to = editing.draft.trim();
    const from = editing.name;
    setEditing(null);
    if (!to || to === from || to.includes("/")) return;
    void apply("rename", () => renameEntry(join(from), join(to)));
  };

  const submitFolder = () => {
    const name = (creating || "").trim();
    setCreating(null);
    if (!name || name.includes("/")) return;
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
    if (ok) void apply("delete", () => removeFile(join(entry.name)));
  };

  const download = () => {
    if (!file?.content) return;
    const blob = new Blob([file.content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = file.path.split("/").pop() || "file.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  // Folders always first, then whichever column was chosen. Size and date descend,
  // because "biggest" and "most recent" are what those questions mean.
  const sorted = [...entries].sort((a, b) => {
    if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
    if (sortBy === "size") return b.size - a.size;
    if (sortBy === "modified") return (b.modified ?? 0) - (a.modified ?? 0);
    return a.name.localeCompare(b.name);
  });

  // Keyboard navigation, because a file list you can only mouse through isn't one.
  // Bound to the listing rather than the window so it can't fight the chat composer.
  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (editing || creating !== null) return;
    const here = sorted[cursor];
    const move = (delta: number) => {
      event.preventDefault();
      setCursor((c) => Math.max(0, Math.min(sorted.length - 1, c + delta)));
    };
    if (event.key === "ArrowDown") return move(1);
    if (event.key === "ArrowUp") return move(-1);
    if (event.key === "Home") return move(-sorted.length);
    if (event.key === "End") return move(sorted.length);
    if (event.key === "Enter" && here) {
      event.preventDefault();
      return here.type === "dir" ? load(join(here.name)) : openFile(here.name);
    }
    if ((event.key === "Backspace" || event.key === "ArrowLeft") && path !== ".") {
      event.preventDefault();
      return goTo(crumbs.length - 2);
    }
    if (event.key === "ArrowRight" && here?.type === "dir") {
      event.preventDefault();
      return load(join(here.name));
    }
    if ((event.key === "Delete" || event.key === "Backspace") && here && path === ".") {
      // Only when there's nowhere to go up to, so Backspace keeps meaning "back" first.
      event.preventDefault();
      void removeEntry(here);
    }
  };

  return (
    <>
      <PageHeader
        icon={<FolderTree className="size-5" />}
        color="lime"
        title="Workspace"
        subtitle="His folder on this machine. Everything he makes lands here."
      >
        <Dropdown
          value={sortBy}
          onChange={(v) => setSortBy(v as typeof sortBy)}
          ariaLabel="Sort by"
          align="end"
          variant="bare"
          options={[
            { value: "name", label: "Name" },
            { value: "modified", label: "Last changed" },
            { value: "size", label: "Size" },
          ]}
        />
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setCreating("")}
          aria-label="New folder"
          title="New folder"
        >
          <FolderPlus className="size-4" />
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={() => load(path)} aria-label="Refresh">
          <RefreshCw className={`size-4 ${loading || busy ? "animate-spin" : ""}`} />
        </Button>
      </PageHeader>

      {/* breadcrumb */}
      <div className="mb-4 flex items-center gap-1 rounded-xl border border-border/70 bg-card/40 px-2.5 py-2 text-sm">
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => crumbs.length && goTo(crumbs.length - 2)}
          disabled={path === "."}
          aria-label="Up"
        >
          <ArrowUp className="size-3.5" />
        </Button>
        <button
          onClick={() => load(".")}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <Folder className="size-3.5 text-lime-500" />~
        </button>
        {crumbs.map((seg, i) => (
          <span key={i} className="flex items-center gap-1">
            <ChevronRight className="size-3.5 text-muted-foreground/50" />
            <button
              onClick={() => goTo(i)}
              className="rounded px-1.5 py-0.5 font-mono transition-colors hover:bg-accent"
            >
              {seg}
            </button>
          </span>
        ))}
      </div>

      {error ? <p className="mb-3 text-sm text-destructive">{error}</p> : null}

      {creating !== null ? (
        <div className="mb-3 flex items-center gap-2 rounded-xl border border-kith/40 bg-card/40 px-3 py-2">
          <FolderPlus className="size-4 shrink-0 text-lime-500" />
          <input
            autoFocus
            value={creating}
            placeholder="Folder name…"
            onChange={(e) => setCreating(e.target.value)}
            onBlur={submitFolder}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitFolder();
              if (e.key === "Escape") setCreating(null);
            }}
            className={`${FIELD} flex-1 text-sm`}
            aria-label="New folder name"
          />
        </div>
      ) : null}

      {sorted.length === 0 ? (
        <EmptyState icon={<Folder className="size-5" />}>
          {loading ? "Looking…" : "This folder is empty."}
        </EmptyState>
      ) : (
        <div
          role="listbox"
          tabIndex={0}
          aria-label="His files"
          onKeyDown={onListKeyDown}
          className="overflow-hidden rounded-xl border border-border/70 bg-card/40 shadow-sm outline-none focus-visible:border-ring/60"
        >
          {sorted.map((e, i) => {
            const full = join(e.name);
            const isDir = e.type === "dir";
            const open = () => (isDir ? load(full) : openFile(e.name));
            return (
              <ContextMenu key={e.name}>
                <ContextMenuTrigger asChild>
                  <div
                    onClick={() => setCursor(i)}
                    onDoubleClick={open}
                    onContextMenu={() => setCursor(i)}
                    className={cn(
                      "group flex w-full items-center gap-3 pr-2 text-left text-sm transition-colors",
                      i > 0 && "border-t border-border/50",
                      cursor === i ? "bg-accent/70" : "hover:bg-accent/40",
                    )}
                  >
                    <button
                      onClick={open}
                      className="flex min-w-0 flex-1 items-center gap-3 py-2.5 pl-4 text-left"
                    >
                      <span
                        className={cn(
                          "flex size-7 shrink-0 items-center justify-center rounded-lg",
                          isDir
                            ? "bg-lime-500/12 text-lime-500"
                            : "bg-muted/60 text-muted-foreground",
                        )}
                      >
                        {isDir ? <Folder className="size-4" /> : <FileText className="size-4" />}
                      </span>
                      {editing?.name === e.name ? (
                        // Renaming happens where the name is, so you can still see the
                        // folder you're renaming inside.
                        <input
                          autoFocus
                          value={editing.draft}
                          onChange={(ev) => setEditing({ name: e.name, draft: ev.target.value })}
                          onClick={(ev) => ev.stopPropagation()}
                          onBlur={submitRename}
                          onKeyDown={(ev) => {
                            if (ev.key === "Enter") submitRename();
                            if (ev.key === "Escape") setEditing(null);
                            ev.stopPropagation();
                          }}
                          className={`${FIELD} min-w-0 flex-1 py-0.5 text-sm`}
                          aria-label={`Rename ${e.name}`}
                        />
                      ) : (
                        <span className="min-w-0 flex-1 truncate">{e.name}</span>
                      )}
                    </button>
                    <span className="hidden w-16 shrink-0 text-right text-[11px] text-muted-foreground/70 tabular-nums sm:block">
                      {formatModified(e.modified ?? 0)}
                    </span>
                    <span className="w-16 shrink-0 text-right text-[11px] text-muted-foreground tabular-nums">
                      {isDir ? "—" : formatSize(e.size)}
                    </span>
                    {isDir ? (
                      <ChevronRight className="size-4 shrink-0 text-muted-foreground/50" />
                    ) : (
                      <Expand className="size-3.5 shrink-0 text-muted-foreground/60 opacity-0 transition-opacity group-hover:opacity-100" />
                    )}
                  </div>
                </ContextMenuTrigger>

                <ContextMenuContent>
                  <ContextMenuLabel>{e.name}</ContextMenuLabel>
                  <ContextMenuItem icon={<Expand className="size-3.5" />} onSelect={open}>
                    {isDir ? "Open folder" : "Open here"}
                  </ContextMenuItem>
                  {!isDir ? (
                    <ContextMenuItem
                      icon={<ExternalLink className="size-3.5" />}
                      onSelect={() => void openWorkspaceFile(full)}
                    >
                      Open in another app
                    </ContextMenuItem>
                  ) : null}
                  <ContextMenuItem
                    icon={<FolderOpen className="size-3.5" />}
                    onSelect={() => void openWorkspaceFile(full, true)}
                  >
                    Show on your machine
                  </ContextMenuItem>
                  <ContextMenuSeparator />
                  <ContextMenuItem
                    icon={<Copy className="size-3.5" />}
                    onSelect={() => void copyText(full)}
                  >
                    Copy path
                  </ContextMenuItem>
                  <ContextMenuItem
                    icon={<Pencil className="size-3.5" />}
                    onSelect={() => setEditing({ name: e.name, draft: e.name })}
                    hint="↩"
                  >
                    Rename
                  </ContextMenuItem>
                  <ContextMenuSeparator />
                  <ContextMenuItem
                    icon={<Trash2 className="size-3.5" />}
                    danger
                    onSelect={() => void removeEntry(e)}
                  >
                    Delete
                  </ContextMenuItem>
                </ContextMenuContent>
              </ContextMenu>
            );
          })}
        </div>
      )}

      {file ? (
        <FileViewer
          open
          onOpenChange={(o) => !o && setFile(null)}
          name={file.path}
          content={file.content}
          error={file.error}
          onDownload={file.content ? download : undefined}
          onOpenOnHost={(reveal) => openWorkspaceFile(file.path, reveal)}
        />
      ) : null}
    </>
  );
}
