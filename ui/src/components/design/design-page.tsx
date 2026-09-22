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
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Code2,
  ImageDown,
  Maximize2,
  MessageSquarePlus,
  Minus,
  Plus,
  RefreshCw,
  Save,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { DesignInspector } from "@/components/design/design-inspector";
import { DesignLayers } from "@/components/design/design-layers";
import { DesignView } from "@/components/design/design-view";
import { Button } from "@/components/ui/button";
import { describeFrame } from "@/lib/design/frame";
import { asFile, pictureName, pngOfDesign, savePicture } from "@/lib/design/export";
import { attachPicture, referenceInChat } from "@/lib/design/reference";
import { fetchWorkspaceFile, saveWorkspaceFile } from "@/lib/backend/brain";
import { applyStyle, materialiseIds, moveNode } from "@/lib/design/edit";
import { type DesignNode, parseDesign } from "@/lib/design/parse";
import { useDesignSystem } from "@/lib/design/use-system";
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
  const system = useDesignSystem(projectId);
  /* Zoom, because an artboard is a fixed rectangle and the panel is whatever the window is.
   *
   * Fixing the size was the right fix and it created this one: a 1600x1000 screen simply did
   * not fit, and with no zoom the only thing to do about it was scroll a design you were trying
   * to judge as a whole. `null` means "fit", recomputed when the panel resizes; a number is a
   * zoom the person chose and is left alone. */
  const viewport = useRef<HTMLDivElement>(null);
  const artboard = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState<number | null>(null);
  const [fitted, setFitted] = useState(1);
  const [referenced, setReferenced] = useState<"" | "sent" | "nowhere">("");


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
  const frame = parsed.frame;

  /* What "fit" is, measured rather than assumed, and re-measured when the panel changes size —
   * opening the inspector or the layers rail is a resize, and a fit computed once would be
   * wrong the moment either moved. Never enlarges: a phone shown at 3x because there is room
   * is not what anybody means by fit. */
  useEffect(() => {
    const box = viewport.current;
    if (!box) return;
    const measure = () => {
      const room = { w: box.clientWidth - 80, h: box.clientHeight - 80 };
      setFitted(Math.min(1, room.w / frame.width, room.h / frame.height) || 1);
    };
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(box);
    return () => watch.disconnect();
  }, [frame.width, frame.height]);
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

  const scale = zoom ?? fitted;

  /* The board's gestures, here too — ⌘/ctrl-wheel to zoom, ⌘0 to fit, ⌘+ / ⌘- to step.
   *
   * Non-passive and `preventDefault`ed for the reason the board already records: without it,
   * ⌘-wheel reaches Chromium's own page zoom and the whole app scales instead of the design.
   * Bound to the panel rather than the window, so scrolling the layers rail is still scrolling. */
  useEffect(() => {
    const box = viewport.current;
    if (!box) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom((held) => Math.min(8, Math.max(0.1, (held ?? fitted) * Math.exp(-event.deltaY / 240))));
    };
    box.addEventListener("wheel", onWheel, { passive: false });
    return () => box.removeEventListener("wheel", onWheel);
  }, [fitted]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!event.metaKey && !event.ctrlKey) return;
      const target = event.target as HTMLElement | null;
      // Not while they are typing a value into the inspector.
      if (target && /^(input|textarea)$/i.test(target.tagName)) return;
      if (event.key === "0") {
        event.preventDefault();
        setZoom(null);
      } else if (event.key === "=" || event.key === "+") {
        event.preventDefault();
        setZoom((held) => Math.min(8, (held ?? fitted) * 1.25));
      } else if (event.key === "-") {
        event.preventDefault();
        setZoom((held) => Math.max(0.1, (held ?? fitted) / 1.25));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fitted]);

  /* A picture of the artboard, at its own size whatever the zoom is. Same capture the board
   * uses, so a design exported from either surface is the same file. */
  const picture = async () => {
    const node = artboard.current;
    if (!node) return null;
    const blob = await pngOfDesign(node, frame);
    return blob ? { blob, name: pictureName(title || path, frame) } : null;
  };

  const ask = async () => {
    const landed = referenceInChat(path, title);
    const shot = await picture();
    if (shot) await attachPicture(asFile(shot.blob, shot.name));
    setReferenced(landed ? "sent" : "nowhere");
  };

  const download = async () => {
    const shot = await picture();
    if (shot) savePicture(shot.blob, shot.name);
  };
  const step = (by: number) => setZoom(Math.min(8, Math.max(0.1, Number((scale * by).toFixed(3)))));

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-border/70 px-6 py-3">
        <Button variant="ghost" size="sm" onClick={onBack} className="gap-1.5">
          <ArrowLeft className="size-4" />
          Back
        </Button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{title || path.split("/").pop()}</div>
          <div className="truncate text-xs text-muted-foreground">
            {path} · {describeFrame(parsed.frame)}
          </div>
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

        {/* Zoom. An artboard has a size, so the panel needs a way to meet it. */}
        <div className="flex items-center gap-0.5 rounded-lg border border-border/60 px-1 py-0.5">
          <Button variant="ghost" size="sm" className="size-7 p-0" onClick={() => step(1 / 1.25)} title="Zoom out">
            <Minus className="size-3.5" />
          </Button>
          <button
            type="button"
            onClick={() => setZoom(null)}
            title="Fit the artboard to the panel"
            className="w-14 text-center text-xs tabular-nums text-muted-foreground hover:text-foreground"
          >
            {Math.round(scale * 100)}%
          </button>
          <Button variant="ghost" size="sm" className="size-7 p-0" onClick={() => step(1.25)} title="Zoom in">
            <Plus className="size-3.5" />
          </Button>
          <Button variant="ghost" size="sm" className="size-7 p-0" onClick={() => setZoom(null)} title="Fit">
            <Maximize2 className="size-3.5" />
          </Button>
        </div>

        {/* Pointing at the screen you are looking at. The path, never the markup — he reads the
            file when he gets to it, which is the only way the answer reflects your edits. */}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void ask()}
          className="gap-1.5"
          title="Put this design's path in the chat you were typing in"
        >
          <MessageSquarePlus className="size-4" />
          {referenced === "nowhere" ? "No chat open" : referenced === "sent" ? "In the chat" : "Ask about this"}
        </Button>

        <Button variant="ghost" size="sm" onClick={() => void download()} className="gap-1.5" title="Save a PNG">
          <ImageDown className="size-4" />
          PNG
        </Button>

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

        <div ref={viewport} className="min-w-0 flex-1 overflow-auto bg-muted/30">
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
            <div className="flex min-h-full items-start justify-center p-10">
              {/* The scaled box reserves the artboard's *scaled* size, so the scroll area is
                  the size of what you can see rather than of the unscaled design underneath —
                  a transform alone leaves the parent believing the old dimensions. */}
              <div
                className="shrink-0"
                style={{ width: frame.width * scale, height: frame.height * scale }}
              >
                <div ref={artboard} style={{ transform: `scale(${scale})`, transformOrigin: "top left" }}>
              <DesignView
                html={html}
                selected={selected}
                onSelect={setSelected}
                onMove={move}
                system={system}
                projectId={projectId}
                className={cn(
                  /* No width, no radius, no background of ours.
                   *
                   * `w-full max-w-[1200px]` overrode the frame the design declares, so every
                   * screen was laid out at the panel's width whatever it said it was. The
                   * radius and the background were worse than cosmetic: a rounded corner and
                   * an app-coloured backdrop are chrome the design never asked for, shown to
                   * somebody judging that design. A hairline and a soft shadow are all an
                   * artboard needs to read as a surface. */
                  "shrink-0 border border-border/60 shadow-md",
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
              </div>
            </div>
          )}
        </div>

        <aside className="hidden w-64 shrink-0 overflow-y-auto border-s border-border/70 xl:block">
          <DesignInspector node={node} onChange={change} system={system} />
        </aside>
      </div>
    </div>
  );
}
