import { useState } from "react";
import { Brain, Pin, Plus } from "lucide-react";
import { Markdown } from "@/components/files";
import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown";
import { EditableText } from "@/components/ui/editable-text";
import { ItemMenu } from "@/components/ui/item-menu";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { Badge, Composer, DeleteButton, EmptyState, PageHeader, SectionLabel } from "./chrome";
import { when } from "@/lib/dates";
import { matches } from "./format";
import { FIELD } from "./types";
import type { Handlers } from "./types";

/* ── Memory (front of mind vs. recall) ──────────────────────────────────── */

export function Memories({
  snap,
  query,
  remove,
  relevel,
  create,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const [content, setContent] = useState("");
  const [level, setLevel] = useState("recall");
  const items = snap.memories.filter((m) => matches(query, m.content, m.tags.join(" ")));
  const core = items.filter((m) => m.level === "core");
  const recall = items.filter((m) => m.level !== "core");
  const add = () => {
    if (!content.trim()) return;
    create("memory", { content, level });
    setContent("");
  };
  return (
    <>
      <PageHeader
        icon={<Brain className="size-5" />}
        color="violet"
        title="Memory"
        count={items.length}
        subtitle="What he holds onto — and how close to hand."
      />
      <Composer onSubmit={add}>
        <input
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Give him something to remember…"
          className={`${FIELD} flex-1`}
        />
        <Dropdown
          value={level}
          onChange={setLevel}
          className="w-40"
          ariaLabel="Memory level"
          options={[
            { value: "recall", label: "recall" },
            { value: "core", label: "front of mind" },
          ]}
        />
        <Button size="sm" onClick={add}>
          <Plus className="size-4" />
          Remember
        </Button>
      </Composer>

      {items.length === 0 ? (
        <EmptyState icon={<Brain className="size-5" />}>No memories yet.</EmptyState>
      ) : (
        <div className="space-y-8">
          {core.length > 0 ? (
            <div>
              <SectionLabel hint={`${core.length}`}>
                <span className="inline-flex items-center gap-1.5">
                  <Pin className="size-3 text-violet-500" /> Front of mind
                </span>
              </SectionLabel>
              <div className="grid gap-3 sm:grid-cols-2">
                {core.map((m) => (
                  <MemoryCard key={m.id} m={m} remove={remove} relevel={relevel} update={update} />
                ))}
              </div>
            </div>
          ) : null}
          {recall.length > 0 ? (
            <div>
              <SectionLabel hint={`${recall.length}`}>Recall</SectionLabel>
              <div className="grid gap-3 sm:grid-cols-2">
                {recall.map((m) => (
                  <MemoryCard key={m.id} m={m} remove={remove} relevel={relevel} update={update} />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </>
  );
}

function MemoryCard({
  m,
  remove,
  relevel,
  update,
}: {
  m: BrainSnapshot["memories"][number];
} & Pick<Handlers, "remove" | "relevel" | "update">) {
  const core = m.level === "core";
  return (
    <ItemMenu
      title={core ? "Front of mind" : "Memory"}
      copy={m.content}
      actions={[
        {
          label: core ? "Move back to recall" : "Move to front of mind",
          icon: <Pin className="size-3.5" />,
          onSelect: () => relevel(m.id, core ? "recall" : "core"),
        },
      ]}
      onDelete={() => remove("memory", m.id, m.content)}
    >
      <div
        className={cn(
          "group relative flex flex-col rounded-xl border bg-card/50 p-4 shadow-sm transition-all hover:shadow-md",
          core
            ? "border-violet-500/30 bg-violet-500/[0.04]"
            : "border-border/70 hover:border-border",
        )}
      >
        <div className="flex-1 text-sm leading-relaxed">
          <EditableText
            value={m.content}
            multiline
            render={(v) => <Markdown>{v}</Markdown>}
            onSave={(v) => update("memory", m.id, { content: v })}
          />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          {m.importance > 0 ? (
            <Badge className="border-violet-500/25 bg-violet-500/10 text-violet-500">
              importance {m.importance}
            </Badge>
          ) : null}
          {m.tags.map((t) => (
            <Badge key={t}>#{t}</Badge>
          ))}
          <span className="ml-auto tabular-nums">{when(m.created_at)}</span>
        </div>
        <div className="mt-3 flex items-center gap-1 border-t border-border/60 pt-3">
          <Button
            variant="ghost"
            size="xs"
            className={core ? "text-violet-500" : "text-muted-foreground"}
            onClick={() => relevel(m.id, core ? "recall" : "core")}
          >
            <Pin className="size-3" />
            {core ? "Front of mind" : "Move to front"}
          </Button>
          <div className="flex-1" />
          <DeleteButton onClick={() => remove("memory", m.id, m.content)} />
        </div>
      </div>
    </ItemMenu>
  );
}
