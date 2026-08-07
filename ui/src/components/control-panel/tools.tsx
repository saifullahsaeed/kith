import { ChevronDown, Code, Wrench } from "lucide-react";
import { CodeBlock, Markdown } from "@/components/files";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { Badge, DeleteButton, EmptyState, PageHeader } from "./chrome";
import { matches } from "./format";
import { CHIP } from "./types";
import type { Handlers } from "./types";

/* ── Tools (his self-made tools) ────────────────────────────────────────── */

export function Tools({
  snap,
  query,
  remove,
}: {
  snap: BrainSnapshot;
  query: string;
  remove: Handlers["remove"];
}) {
  const items = snap.tools.filter((t) => matches(query, t.name, t.description));
  return (
    <>
      <PageHeader
        icon={<Wrench className="size-5" />}
        color="rose"
        title="Tools"
        count={items.length}
        subtitle="Tools he wrote for himself, on his own."
      />
      {items.length === 0 ? (
        <EmptyState icon={<Wrench className="size-5" />}>No self-made tools.</EmptyState>
      ) : (
        <div className="space-y-3">
          {items.map((t) => (
            <details
              key={t.name}
              className="group rounded-xl border border-border/70 bg-card/50 shadow-sm transition-all hover:shadow-md"
            >
              <summary className="flex cursor-pointer list-none items-center gap-3 p-4">
                <span
                  className={cn(
                    "flex size-9 shrink-0 items-center justify-center rounded-xl",
                    CHIP.rose,
                  )}
                >
                  <Code className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-semibold">{t.name}</span>
                    <Badge>{t.language}</Badge>
                  </div>
                  <div className="mt-0.5 truncate text-xs text-muted-foreground">
                    <Markdown>{t.description}</Markdown>
                  </div>
                </div>
                <DeleteButton onClick={() => remove("tool", t.name, t.name)} />
                <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
              </summary>
              <div className="mx-4 mb-4 overflow-hidden rounded-xl border border-border/60 bg-muted/20">
                <CodeBlock code={t.code} language={t.language} label={t.language} />
              </div>
            </details>
          ))}
        </div>
      )}
    </>
  );
}
