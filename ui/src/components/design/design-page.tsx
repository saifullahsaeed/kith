/**
 * The page a design opens into, and for now the only thing you can do to one: look at it.
 *
 * Deliberately the whole width. A design is a screen, and judging a screen inside a drawer beside
 * a comment thread is judging something else — the first question anyone asks of a mockup is
 * whether it breathes, and that question is unanswerable at 380px.
 *
 * What is not here yet is the point of the file existing: selection state is wired through and
 * unused, because the next step is an inspector reading from it. Adding the chrome now and the
 * editing next keeps each step something you can look at and disagree with.
 */
import { AlertTriangle, ArrowLeft, Code2, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { DesignView } from "@/components/design/design-view";
import { Button } from "@/components/ui/button";
import { fetchWorkspaceFile } from "@/lib/backend/brain";
import { parseDesign } from "@/lib/design/parse";
import { cn } from "@/lib/utils";

export interface DesignPageProps {
  /** The deliverable's path, as it was filed. */
  path: string;
  /** Which project the path is anchored to — the same answer the file viewer needs. */
  projectId: number | null;
  title?: string;
  onBack: () => void;
}

export function DesignPage({ path, projectId, title, onBack }: DesignPageProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);

  const file = useQuery({
    queryKey: ["design", path, projectId],
    queryFn: () => fetchWorkspaceFile(path, projectId),
  });

  const html = file.data?.content ?? "";
  // Parsed twice on purpose — once here for the count, once inside the view. Both are memoised
  // against the same string and the parse is microseconds; threading it through would mean the
  // view could not be handed plain HTML, which is what every other caller will want.
  const dropped = html ? parseDesign(html).dropped : [];

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
        {dropped.length ? (
          <span
            className="flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-2.5 py-1 text-xs text-amber-600 dark:text-amber-400"
            title={dropped.slice(0, 12).join("\n")}
          >
            <AlertTriangle className="size-3.5" />
            {dropped.length} not editable
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
      </header>

      <div className="min-h-0 flex-1 overflow-auto bg-muted/30">
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
              className={cn(
                "w-full max-w-[1200px] rounded-xl border border-border/70 bg-background shadow-sm",
                // The one bit of chrome the view itself must not carry: a selected node is drawn
                // on from outside so nothing in his document has to know it is being edited.
                "[&_[data-selected]]:outline [&_[data-selected]]:outline-2 [&_[data-selected]]:outline-[var(--kith)]",
              )}
            />
          </div>
        )}
      </div>
    </div>
  );
}
