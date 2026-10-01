/** The Files screen's folder tree — every folder in his, loaded a level at a time. */
import { useEffect, useRef, useState } from "react";
import { ChevronRight, Folder, FolderOpen } from "lucide-react";

import { cn } from "@/lib/utils";
import { Label } from "./chrome";
import { useFolder } from "./files-data";
import { ancestorsOf, homeShort, joinPath, sortEntries, visibleEntries } from "./library";

/**
 * Every folder in his, loaded one level at a time as it is opened.
 *
 * The folders above where you are stay open, so the tree always shows the way back up; anything
 * else opens when you ask it to. Each level is the same listing query the file list uses, which is
 * what lazy costs here: opening a folder you then click into is one request, not two.
 */
export function FolderTree({
  path,
  root,
  showHidden,
  onShowHidden,
  holding,
  onGo,
}: {
  path: string;
  root: string;
  showHidden: boolean;
  onShowHidden: (value: boolean) => void;
  holding: boolean;
  onGo: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(ancestorsOf(path)));
  const box = useRef<HTMLDivElement>(null);

  // Wherever you go — the list, the breadcrumb, the tree itself — the way there opens.
  useEffect(() => {
    setExpanded((was) => {
      const want = ancestorsOf(path).filter((p) => !was.has(p));
      return want.length ? new Set([...was, ...want]) : was;
    });
    const row = box.current?.querySelector(`[data-tree="${CSS.escape(path)}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [path]);

  const toggle = (p: string) =>
    setExpanded((was) => {
      const next = new Set(was);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });

  return (
    <section aria-label="Folders in his folder" className="bg-sidebar border-border flex min-h-0 flex-col overflow-hidden border-r">
      <div className="border-border/80 flex h-10 shrink-0 items-center gap-2 border-b px-3.5">
        <Label>His folder</Label>
        <span className="flex-1" />
        <span className="text-muted-foreground/80 truncate font-mono text-[11px]" title={root}>
          {homeShort(root)}
        </span>
      </div>
      <div ref={box} className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
        <TreeLevel
          dir="."
          depth={0}
          current={path}
          expanded={expanded}
          onToggle={toggle}
          onGo={onGo}
          showHidden={showHidden}
          holding={holding}
        />
      </div>
      <div className="border-border/80 flex h-[38px] shrink-0 items-center border-t px-3.5">
        <button
          type="button"
          role="switch"
          aria-checked={showHidden}
          onClick={() => onShowHidden(!showHidden)}
          className="text-muted-foreground/80 hover:text-foreground flex items-center gap-2 text-[11.5px] transition-colors"
        >
          <span
            aria-hidden
            className={cn(
              "border-input relative h-3 w-[22px] shrink-0 rounded-full border transition-colors",
              showHidden ? "bg-foreground/25" : "bg-muted",
            )}
          >
            <span
              className={cn(
                "absolute top-px left-px size-2 rounded-full transition-transform",
                showHidden ? "bg-foreground translate-x-[10px]" : "bg-muted-foreground/80",
              )}
            />
          </span>
          <span>
            Show hidden <span className="text-muted-foreground/50 font-mono">.kith .git</span>
          </span>
        </button>
      </div>
    </section>
  );
}

type TreeProps = {
  current: string;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  onGo: (path: string) => void;
  showHidden: boolean;
  holding: boolean;
};

function TreeLevel({ dir, depth, ...tree }: TreeProps & { dir: string; depth: number }) {
  const { data, isPending } = useFolder(dir, !tree.holding);
  const folders = visibleEntries(
    sortEntries((data ?? []).filter((e) => e.type === "dir"), "name"),
    { showHidden: tree.showHidden, filter: "" },
  );
  if (isPending && depth === 0) return <p className="text-muted-foreground/80 px-2 py-1 text-[12.5px]">Looking…</p>;
  return (
    <>
      {folders.map((folder) => (
        <TreeNode key={folder.name} path={joinPath(dir, folder.name)} name={folder.name} depth={depth} {...tree} />
      ))}
    </>
  );
}

function TreeNode({ path, name, depth, ...tree }: TreeProps & { path: string; name: string; depth: number }) {
  const open = tree.expanded.has(path);
  const here = tree.current === path;
  // Whether it has folders of its own is only known once it has been opened; until then it is
  // assumed to, so the chevron is there to find out with.
  const { data } = useFolder(path, open && !tree.holding);
  const empty =
    data !== undefined &&
    !data.some((e) => e.type === "dir" && (tree.showHidden || !e.name.startsWith(".")));
  const Icon = open || here ? FolderOpen : Folder;

  return (
    <>
      <div
        data-tree={path}
        className={cn(
          "flex h-[25px] shrink-0 items-center gap-[5px] rounded-md pr-2 text-[12.5px] transition-colors",
          here ? "bg-muted/60 text-foreground font-medium" : open ? "text-foreground hover:bg-muted/40" : "text-foreground/80 hover:bg-muted/40",
        )}
        style={{ paddingLeft: 4 + depth * 16 }}
      >
        <button
          type="button"
          tabIndex={-1}
          onClick={() => tree.onToggle(path)}
          aria-label={`${open ? "Collapse" : "Expand"} ${name}`}
          aria-expanded={open}
          className={cn(
            "text-muted-foreground/50 hover:text-foreground flex size-3.5 shrink-0 items-center justify-center transition-transform",
            open && "rotate-90",
            empty && "invisible",
          )}
        >
          <ChevronRight className="size-[11px]" strokeWidth={2.4} />
        </button>
        <button
          type="button"
          onClick={() => tree.onGo(path)}
          aria-current={here ? "page" : undefined}
          className="flex min-w-0 flex-1 items-center gap-[5px] text-left"
        >
          <Icon className={cn("size-3.5 shrink-0", here ? "text-foreground/80" : "text-muted-foreground/80")} />
          <span className="min-w-0 flex-1 truncate">{name}</span>
        </button>
      </div>
      {open ? <TreeLevel dir={path} depth={depth + 1} {...tree} /> : null}
    </>
  );
}

