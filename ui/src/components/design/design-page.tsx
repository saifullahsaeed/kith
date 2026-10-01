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
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { DesignInspector } from "@/components/design/design-inspector";
import { DesignLayers } from "@/components/design/design-layers";
import { DesignView } from "@/components/design/design-view";
import { Button } from "@/components/ui/button";
import { describeFrame } from "@/lib/design/frame";
import { asFile, pictureName, pngOfDesign, savePicture } from "@/lib/design/export";
import { attachPicture, referenceInChat } from "@/lib/design/reference";
import { fetchWorkspaceFile, saveWorkspaceFile } from "@/lib/backend/brain";
import { previewHoles } from "@/lib/design/compose";
import { applyStyle, detachInstance, materialiseIds, moveNode } from "@/lib/design/edit";
import { type DesignNode, parseDesign } from "@/lib/design/parse";
import { partsOf } from "@/lib/design/system";
import { useDesignSystem } from "@/lib/design/use-system";
import { cn } from "@/lib/utils";

export interface DesignPageProps {
  /** The deliverable's path, as it was filed. */
  path: string;
  /** Which project the path is anchored to — the same answer the file viewer needs. */
  projectId: number | null;
  title?: string;
  onBack: () => void;
  /**
   * Set when this page is a part's own file, opened from an instance on a screen: the part's
   * name, the instance's props to show its holes with, and the screen's width to lay it out at.
   */
  part?: { name: string; props: Record<string, string>; width: number; context: string };
}

/** A part being edited on its own page, opened from one of its instances. */
interface OpenPart {
  name: string;
  path: string;
  props: Record<string, string>;
}

/** A part's file from `.kith/` down: the absolute path is the same for every part and says nothing. */
function shortPath(path: string): string {
  const at = path.indexOf(".kith/");
  return at >= 0 ? path.slice(at) : path;
}

/**
 * The instance that owns a node: the nearest one, at or above it, whose part the node came from.
 *
 * Nearest by *name*, not just nearest, because parts nest. A button inside a card is an
 * instance of the button and a part of the card at once; selecting it means the card's file is
 * where its bytes are, so "edit the part" has to open the card. A node that is not from any part
 * is its own owner when it is an instance, and has none otherwise.
 */
function ownerOf(root: DesignNode, id: string | null): DesignNode | null {
  if (!id) return null;
  const trail: DesignNode[] = [];
  const walk = (node: DesignNode): DesignNode | null => {
    if (node.id === id) return node;
    trail.push(node);
    for (const child of node.children) {
      const hit = walk(child);
      if (hit) return hit;
    }
    trail.pop();
    return null;
  };
  const node = walk(root);
  if (!node) return null;
  const wanted = node.part?.component;
  if (!wanted) return node.instance ? node : null;
  for (let at = trail.length - 1; at >= 0; at--) {
    if (trail[at].instance?.name === wanted) return trail[at];
  }
  return null;
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

export function DesignPage({ path, projectId, title, onBack, part }: DesignPageProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const system = useDesignSystem(projectId);
  const parts = useMemo(() => partsOf(system), [system]);
  const queries = useQueryClient();
  /* The part a click asked to change everywhere, opened as its own page over this one.
   *
   * A page rather than an edit in place, because "everywhere" is a different file and a
   * different save: the screen you were on has its own draft, and a part's change should be
   * seen — alone, at the screen's width, with this instance's values in its holes — before it
   * lands on every screen that uses it. Back returns to the screen, which redraws with it. */
  const [opened, setOpened] = useState<OpenPart | null>(null);
  const [natural, setNatural] = useState(0);
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
  // The same inputs `DesignView` parses with, so a click's id is an id in this tree too.
  const parsed = useMemo(
    () => parseDesign(part ? previewHoles(html, part.props) : html, parts),
    [html, parts, part],
  );
  const frame = part ? { ...parsed.frame, width: part.width, height: Math.max(natural, 80) } : parsed.frame;

  /* A part has no frame height of its own — it is as tall as it is — so the box the zoom
   * reserves is measured from what was drawn rather than read from the file. */
  useEffect(() => {
    const box = artboard.current;
    if (!part || !box) return;
    const watch = new ResizeObserver(() => setNatural(box.offsetHeight));
    watch.observe(box);
    return () => watch.disconnect();
  }, [part, opened]);

  /* What "fit" is, measured rather than assumed, and re-measured when the panel changes size —
   * opening the inspector or the layers rail is a resize, and a fit computed once would be
   * wrong the moment either moved. Never enlarges: a phone shown at 3x because there is room
   * is not what anybody means by fit. */
  useEffect(() => {
    const box = viewport.current;
    if (!box) return;
    const measure = () => {
      const room = { w: box.clientWidth - 80, h: box.clientHeight - 80 };
      // A box with no room is a box not on screen; its measurement is not a zoom.
      if (room.w <= 0 || room.h <= 0) return;
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
      // A node from a part's file has no bytes in this one. The inspector offers the part's
      // own page or a detach instead; this is the backstop.
      if (!selected || node?.part || node?.synthetic) return;
      setFailure("");
      setSaved(false);
      setDraft((current) => {
        // Ids are written on the first edit, so a design that was only looked at stays untouched.
        const source = materialiseIds(current ?? loaded);
        return applyStyle(source, selected, { [property]: value });
      });
    },
    [selected, loaded, node],
  );

  /** Every screen's version of this part, opened as its own page. */
  const editPart = useCallback(
    (name: string, props: Record<string, string>) => {
      const found = parts[name];
      if (!found?.path) {
        setFailure(`${name} is an inline snippet, not a file — re-save it with update_design_system to edit it here.`);
        return;
      }
      setOpened({ name, path: found.path, props });
    },
    [parts],
  );

  /** This screen's copy of an instance, so it can differ from the others. */
  const detach = useCallback(
    (id: string) => {
      setFailure("");
      setSaved(false);
      setDraft((current) => detachInstance(current ?? loaded, id, parts));
      setSelected(id);
    },
    [loaded, parts],
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
      // A part is read through the system, not as a file, so every screen drawing it is waiting
      // on this query rather than on the one above.
      if (part) await queries.invalidateQueries({ queryKey: ["design-system"] });
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
    <div className="relative flex h-full flex-col">
      {/* Over the screen rather than instead of it. Swapped out, the screen's viewport was
          unmounted under its own ResizeObserver, measured zero wide, and came back at -21%
          zoom — and it would have lost an unsaved draft on the way. */}
      {opened ? (
        <div className="absolute inset-0 z-20 bg-background">
          <DesignPage
            path={opened.path}
            projectId={projectId}
            title={`Component · ${opened.name}`}
            onBack={() => setOpened(null)}
            part={{ name: opened.name, props: opened.props, width: frame.width, context: parsed.sheet }}
          />
        </div>
      ) : null}
      <header className="flex shrink-0 items-center gap-3 border-b border-border/70 px-6 py-3">
        <Button variant="ghost" size="sm" onClick={onBack} className="gap-1.5">
          <ArrowLeft className="size-4" />
          Back
        </Button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{title || path.split("/").pop()}</div>
          <div className="truncate text-xs text-muted-foreground">
            {part
              ? `${shortPath(path)} · changes here reach every screen that uses ${part.name}`
              : `${path} · ${describeFrame(parsed.frame)}`}
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
                fitContent={Boolean(part)}
                width={part?.width}
                preview={part?.props}
                context={part?.context}
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
          <DesignInspector
            node={node}
            onChange={change}
            system={system}
            onEditPart={editPart}
            onDetach={detach}
            owner={ownerOf(parsed.root, selected)}
          />
        </aside>
      </div>
    </div>
  );
}
