/**
 * The editor: his design in the middle, its structure on the left, its properties on the right.
 *
 * Deliberately the whole width. A design is a screen, and judging a screen inside a drawer beside
 * a comment thread is judging something else — the first question anyone asks of a mockup is
 * whether it breathes, and that is unanswerable at 380px.
 *
 * ── Why the file is normalised on open ──────────────────────────────────────────────────────
 *
 * Every node the parser sees has an id, but the ones it minted exist only in memory. A patch
 * naming `k7` would mean nothing to the next parse of a file that never received it, so
 * `materialiseIds` writes them in as soon as there is an edit to write. It happens on the first
 * change rather than on open, so looking at a design and leaving does not produce a diff.
 *
 * ── Why saving is explicit ──────────────────────────────────────────────────────────────────
 *
 * He may be working in this file. Autosaving into a repository he is mid-turn on is the
 * `two-writers-one-worktree` problem with a UI attached, and the honest version of this editor
 * says when it has changes and waits to be told. The file is re-read on save so a change made
 * underneath is a refusal rather than an overwrite.
 */
import { AlertTriangle, ArrowLeft, Check, Code2, RefreshCw, Save } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { DesignInspector } from "@/components/design/design-inspector";
import { DesignLayers } from "@/components/design/design-layers";
import { DesignView } from "@/components/design/design-view";
import { Button } from "@/components/ui/button";
import { fetchWorkspaceFile, saveWorkspaceFile } from "@/lib/backend/brain";
import { applyStyle, materialiseIds, moveNode } from "@/lib/design/edit";
import { type DesignNode, parseDesign } from "@/lib/design/parse";
import { cn } from "@/lib/utils";

export interface DesignPageProps {
  /** The deliverable's path, as it was filed. */
  path: string;
  /** Which project the path is anchored to — the same answer the file viewer needs. */
  projectId: number | null;
  title?: string;
  onBack: () => void;
}

/** The node carrying this id, anywhere in the tree. */
function find(node: DesignNode, id: string | null): DesignNode | null {
  if (!id) return null;
  if (node.id === id) return node;
  for (const child of node.children) {
    const hit = find(child, id);
    if (hit) return hit;
  }
  return null;
}

export function DesignPage({ path, projectId, title, onBack }: DesignPageProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);
  /** The edited document, or null while it is still exactly what was loaded. */
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failure, setFailure] = useState("");

  const file = useQuery({
    queryKey: ["design", path, projectId],
    queryFn: () => fetchWorkspaceFile(path, projectId),
  });

  const loaded = file.data?.content ?? "";
  const html = draft ?? loaded;
  const parsed = useMemo(() => parseDesign(html), [html]);
  const node = find(parsed.root, selected);

  // A design reopened, or reloaded underneath, is a different document — holding a draft against
  // it would apply edits to text that is no longer there.
  useEffect(() => {
    setDraft(null);
    setSelected(null);
    setSaved(false);
  }, [path, projectId]);

  const change = useCallback(
    (property: string, value: string | null) => {
      if (!selected) return;
      setFailure("");
      setSaved(false);
      setDraft((current) => {
        // Ids are written on the first edit, so a design that was only looked at stays untouched.
        const source = materialiseIds(current ?? loaded);
        return applyStyle(source, selected, { [property]: value });
      });
    },
    [selected, loaded],
  );

  const move = useCallback(
    (id: string, parentId: string, index: number) => {
      setFailure("");
      setSaved(false);
      setDraft((current) => moveNode(materialiseIds(current ?? loaded), id, parentId, index));
    },
    [loaded],
  );

  const save = async () => {
    if (draft == null) return;
    setSaving(true);
    setFailure("");
    try {
      // Re-read first: he may have written to this file while it was open, and overwriting his
      // work with a stale copy of it is the one failure this editor must not have.
      const now = await fetchWorkspaceFile(path, projectId);
      if (now.content !== loaded) {
        setFailure("That file changed on disk while you were editing. Reopen it to see his version.");
        return;
      }
      await saveWorkspaceFile(path, draft, projectId);
      setSaved(true);
      setDraft(null);
      await file.refetch();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "couldn't save that file");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-border/70 px-6 py-3">
        <Button variant="ghost" size="sm" onClick={onBack} className="gap-1.5">
          <ArrowLeft className="size-4" />
          Back
        </Button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{title || path.split("/").pop()}</div>
          <div className="truncate text-xs text-muted-foreground">{path}</div>
        </div>

        {parsed.dropped.length ? (
          <span
            className="flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-2.5 py-1 text-xs text-amber-600 dark:text-amber-400"
            title={parsed.dropped.slice(0, 12).join("\n")}
          >
            <AlertTriangle className="size-3.5" />
            {parsed.dropped.length} not editable
          </span>
        ) : null}

        <Button
          variant={showSource ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setShowSource((on) => !on)}
          className="gap-1.5"
        >
          <Code2 className="size-4" />
          Source
        </Button>

        <Button size="sm" onClick={save} disabled={draft == null || saving} className="gap-1.5">
          {saving ? (
            <RefreshCw className="size-4 animate-spin" />
          ) : saved && draft == null ? (
            <Check className="size-4" />
          ) : (
            <Save className="size-4" />
          )}
          {draft != null ? "Save" : saved ? "Saved" : "No changes"}
        </Button>
      </header>

      {failure ? (
        <div className="shrink-0 border-b border-border/70 bg-destructive/10 px-6 py-2 text-xs text-destructive">
          {failure}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-52 shrink-0 overflow-y-auto border-e border-border/70 lg:block">
          <DesignLayers root={parsed.root} selected={selected} onSelect={setSelected} />
        </aside>

        <div className="min-w-0 flex-1 overflow-auto bg-muted/30">
          {file.isLoading ? (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
              <RefreshCw className="size-4 animate-spin" />
              Opening…
            </div>
          ) : file.error ? (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
              {file.error instanceof Error ? file.error.message : "couldn't read that file"}
            </div>
          ) : showSource ? (
            <pre className="m-6 overflow-auto rounded-xl border border-border/70 bg-card p-4 text-xs leading-relaxed">
              {html}
            </pre>
          ) : (
            <div className="flex min-h-full justify-center p-6">
              <DesignView
                html={html}
                selected={selected}
                onSelect={setSelected}
                onMove={move}
                className={cn(
                  "w-full max-w-[1200px] rounded-xl border border-border/70 bg-background shadow-sm",
                  // All drawn from outside, so nothing in his document has to know it is being
                  // edited. The insertion line is an inset shadow on the edge the drop would land
                  // against — which edge is decided in the view, where the parent's flex direction
                  // is known, because CSS cannot tell a row from a column.
                  "[&_[data-selected]]:outline [&_[data-selected]]:outline-2 [&_[data-selected]]:outline-[var(--kith)]",
                  "[&_[data-dragging]]:opacity-40",
                  "[&_[data-drop=top]]:shadow-[inset_0_2px_0_0_var(--kith)]",
                  "[&_[data-drop=bottom]]:shadow-[inset_0_-2px_0_0_var(--kith)]",
                  "[&_[data-drop=left]]:shadow-[inset_2px_0_0_0_var(--kith)]",
                  "[&_[data-drop=right]]:shadow-[inset_-2px_0_0_0_var(--kith)]",
                )}
              />
            </div>
          )}
        </div>

        <aside className="hidden w-64 shrink-0 overflow-y-auto border-s border-border/70 xl:block">
          <DesignInspector node={node} onChange={change} />
        </aside>
      </div>
    </div>
  );
}
