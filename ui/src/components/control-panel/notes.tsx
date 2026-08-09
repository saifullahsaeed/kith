import { useState } from "react";
import { Plus, StickyNote } from "lucide-react";
import { Markdown } from "@/components/files";
import { Button } from "@/components/ui/button";
import { EditableText } from "@/components/ui/editable-text";
import { ItemMenu } from "@/components/ui/item-menu";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { Composer, DeleteButton, EmptyState, PageHeader } from "./chrome";
import { when } from "@/lib/dates";
import { matches } from "./format";
import { FIELD } from "./types";
import type { Handlers } from "./types";

/* ── Notes (masonry of paper cards) ─────────────────────────────────────── */

export function Notes({
  snap,
  query,
  remove,
  create,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const [title, setTitle] = useState("");
  const items = snap.notes.filter((n) => matches(query, n.title, n.body));
  const add = () => {
    if (!title.trim()) return;
    create("note", { title });
    setTitle("");
  };
  return (
    <>
      <PageHeader
        icon={<StickyNote className="size-5" />}
        color="amber"
        title="Notes"
        count={items.length}
        subtitle="Things he's jotted down to keep."
      />
      <Composer onSubmit={add}>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="New note — give it a title…"
          className={`${FIELD} flex-1`}
        />
        <Button size="sm" onClick={add}>
          <Plus className="size-4" />
          Add note
        </Button>
      </Composer>
      {items.length === 0 ? (
        <EmptyState icon={<StickyNote className="size-5" />}>No notes yet.</EmptyState>
      ) : (
        <div className="gap-4 [column-fill:_balance] sm:columns-2 lg:columns-3">
          {items.map((n) => (
            <ItemMenu
              key={n.id}
              title={n.title}
              copy={`${n.title}\n\n${n.body}`}
              onDelete={() => remove("note", n.id, n.title)}
            >
              <div className="group mb-4 break-inside-avoid rounded-xl border border-border/70 bg-card/50 p-4 shadow-sm transition-all hover:border-amber-500/30 hover:shadow-md">
                <div className="mb-1 h-1 w-8 rounded-full bg-amber-500/40" />
                <div className="text-sm font-semibold leading-snug">
                  <EditableText
                    value={n.title}
                    onSave={(v) => update("note", n.id, { title: v })}
                  />
                </div>
                <div className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                  {/* He writes his notes in Markdown — show them that way, edit the raw text. */}
                  <EditableText
                    value={n.body}
                    onSave={(v) => update("note", n.id, { body: v })}
                    multiline
                    placeholder="(empty — click to write)"
                    render={(v) => <Markdown>{v}</Markdown>}
                  />
                </div>
                <div className="mt-3 flex items-center gap-2 border-t border-border/50 pt-2.5 text-[11px] text-muted-foreground">
                  <span className="tabular-nums">{when(n.updated_at)}</span>
                  <div className="flex-1" />
                  <DeleteButton onClick={() => remove("note", n.id, n.title)} />
                </div>
              </div>
            </ItemMenu>
          ))}
        </div>
      )}
    </>
  );
}
